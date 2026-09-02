"""Ingest：docs 目录 → 向量库 —— 管道总调 + 增量状态机（差异化核心之一）。

一次 `ingest()` = 一个"作业"。输入目录、输出库与状态文件，返回 JobReport + 事件流
（事件流交给 auditor.py 写 JSONL；ingest 只做状态机，不碰文件审计）。

成熟经验落地（2026-09-02 网搜确认，来源见项目文档第 9 节）：
- mtime+size 粗筛、文本 hash 最终裁决（mtime 误报：git checkout/rsync/备份恢复会 bump
  时间戳但内容没变，重嵌是纯浪费；漏报：时钟偏移 mtime 倒退会静默跳过真实变更，更糟）
- 文档级"先删后增"，不做 chunk 局部替换（小改动也会让 chunk 边界漂移，局部替换不稳定）
- 文件删除必须清理（僵尸 chunk：源文件下线但旧块还在库，检索会命中过期信息）
- config 指纹防静默过期（换模型/chunk 配置后旧产物不报废也必须提示，不静默）
- 坏文件 error_count 连续失败超限 → 标记需人工，不无限重试

批语义（D1/D4）：窗口攒满 flush 一次（embed → add 成对），"批"是作业内事务单元。
关键不变量：**状态只在批提交成功后推进**（pending_state 攒批、flush 成功才并入 state，
崩溃时库+状态天然 back 到上一提交点——at-least-once + 幂等 upsert + 源级先删后增）。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from .chunker import chunk as chunk_docs
from .cleaner import clean
from .embedder import Embedder
from .loaders import REGISTRY, load
from .store import Store
from .types import AuditChunk, JobReport, make_hash

_VERSION = "0.1.0"
_WINDOW = 32  # 批窗口：攒满这么多 chunk 就 embed+add 一组
_ERROR_LIMIT = 3  # 连续失败超限 → 标"需人工"，之后跳过（--force 可重试）

# 目录遍历时永远跳过的垃圾目录
_SKIP_DIRS = {"node_modules", "__pycache__", ".venv", "venv", ".git", ".gitlab", "dist", "build", ".idea", ".vscode"}


def _load_state(path: Path) -> dict:
    """状态文件不必存在：黑户跑第一次 = 全量摄入。"""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(path: Path, state: dict) -> None:
    """原子写：先写临时文件再替换（崩溃不会剩半截 JSON）。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _scan(docs_dir: Path) -> list[str]:
    """扫描支持的扩展名文件；跳过垃圾目录；排序固定（状态机顺序确定性）。"""
    rels: list[str] = []
    for dirpath, dirnames, filenames in os.walk(docs_dir):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if p.suffix.lower().lstrip(".") in REGISTRY:
                rels.append(p.relative_to(docs_dir).as_posix())
    return rels


def _fingerprint(model: str, chunk_size: int, overlap: int) -> str:
    """config 指纹：模型/分块参数/本模块版本。变了说明旧产物语义过期。"""
    return f"{_VERSION}|{model}|{chunk_size}|{overlap}"


def ingest(
    docs_dir: str,
    db_dir: str,
    table_name: str = "docs",
    model: str | None = None,
    chunk_size: int = 512,
    overlap: int = 64,
    api_key: str | None = None,
    api_url: str | None = None,
    force: bool = False,
    state_file: str | None = None,
    window: int = _WINDOW,
) -> tuple[JobReport, list[dict]]:
    """执行一次摄取作业。返回 (JobReport, 事件流)。

    事件流元素（供 auditor）：
      {"type": "create"|"update"|"delete"|"skip"|"error"|"warning", "source": rel, ...}
    状态机分支：mtime+size 一致 → 跳过；文本 hash 一致 → 只更新状态；
    否则 → 先删后增；状态里有、目录没了 → delete_source（僵尸防线）。
    """
    docs_dir = Path(docs_dir)
    if not docs_dir.is_dir():
        raise ValueError(f"docs 目录不存在: {docs_dir}")
    state_path = Path(state_file) if state_file else Path(db_dir) / "state.json"

    state = _load_state(state_path)
    report = JobReport(job_id=make_hash(f"{docs_dir}|{datetime.now().isoformat()}")[:12], started_at=datetime.now())
    events: list[dict] = []

    embedder = Embedder(model=model, api_key=api_key, api_url=api_url)
    fp = _fingerprint(embedder.model_name, chunk_size, overlap)
    if state.get("fingerprint") and state["fingerprint"] != fp:
        events.append({
            "type": "warning",
            "source": "",
            "detail": f"config 指纹变更（{state['fingerprint']} → {fp}），旧产物已过期——建议 --force 全量重建",
        })

    scan = _scan(docs_dir)
    report.files_total = len(scan)

    store: Store | None = None
    window_chunks: list = []
    pending_deletes: set[str] = set()  # 本批待清理的旧源（先删后增）
    pending_state: dict[str, dict] = {}  # 本批待落盘状态（批提交成功才并入 state）

    def flush() -> None:
        """窗口提交：embed → 建/开库 → 先删后增 → add。批内任一失败则本批全部放弃。"""
        nonlocal store
        if not window_chunks:
            return
        embedder.embed(window_chunks)
        if store is None:
            store = Store(db_dir, table_name, embedder.model_name, dim=len(window_chunks[0].embedding))
        for src in pending_deletes:
            store.delete_source(src)
        store.add_chunks(window_chunks)  # 幂等 upsert：批失败重跑不会重复
        # —— 提交成功，推进状态 ——
        window_chunks.clear()
        pending_deletes.clear()
        state.update(pending_state)
        pending_state.clear()
        _save_state(state_path, state)

    scanned = set(scan)
    for rel in scan:
        full = docs_dir / rel
        try:
            st = full.stat()
        except OSError as e:  # 权限等 stat 级故障
            report.failed += 1
            report.errors[rel] = f"stat 失败: {e}"
            events.append({"type": "error", "source": rel, "detail": str(e)})
            continue

        prev = state.get(rel)
        if prev and not force:
            # 第一道门：mtime+size 粗筛（不读文件）
            if prev.get("mtime") == st.st_mtime and prev.get("size") == st.st_size:
                report.dupes += 1
                events.append({"type": "skip", "source": rel, "detail": "mtime/size 未变"})
                continue
            # 坏文件超限：不再浪费在读它上面（需人工，--force 可重试）
            if prev.get("error_count", 0) >= _ERROR_LIMIT:
                report.failed += 1
                report.errors[rel] = f"连续失败 {_ERROR_LIMIT} 次，需人工检查（--force 可重试）"
                events.append({"type": "error", "source": rel, "detail": "失败超限跳过"})
                continue

        try:
            docs = load(full)
            for d in docs:
                d.source = rel  # 统一 source = 相对 docs_dir（load 返回的是绝对路径，会与
                # delete_source/状态键对不上——实测冒烟抓到的 delete 失效 bug）
            kept = clean(docs)
            if not kept:
                report.skipped_files[rel] = "清洗后为空（低质/乱码）"
                events.append({"type": "skip", "source": rel, "detail": "清洗丢弃"})
                # 推进状态：否则每次运行都白洗一遍（无入库动作，可立即落盘）
                state[rel] = {"mtime": st.st_mtime, "size": st.st_size, "text_hash": "", "status": "dropped"}
                _save_state(state_path, state)
                continue
            text_hash = make_hash("\n".join(d.text for d in kept))
            if prev and prev.get("text_hash") == text_hash and not force:
                # 第二道门：mtime 误报（git checkout 等），内容其实没变——只更新状态
                state[rel] = {"mtime": st.st_mtime, "size": st.st_size, "text_hash": text_hash, "status": "ok"}
                _save_state(state_path, state)
                report.dupes += 1
                events.append({"type": "skip", "source": rel, "detail": "文本 hash 未变（mtime 误报）"})
                continue

            chunks = chunk_docs(kept, chunk_size=chunk_size, overlap=overlap)
            if prev:
                pending_deletes.add(rel)  # 内容变了：先删后增（文档级，不做 chunk 局部替换）
            window_chunks.extend(chunks)
            pending_state[rel] = {"mtime": st.st_mtime, "size": st.st_size, "text_hash": text_hash, "status": "ok"}
            report.ingested += 1
            events.append({"type": "update" if prev else "create", "source": rel})
            report.chunks.extend(
                AuditChunk(
                    chunk_id=c.chunk_id, source=c.source, page=c.page,
                    heading=c.heading, emb_model=c.emb_model, text_hash=c.text_hash,
                )
                for c in chunks
            )
            if len(window_chunks) >= window:
                flush()
        except Exception as e:  # D2：坏文件逐文件隔离，不炸整批
            report.failed += 1
            report.errors[rel] = str(e)
            err_prev = state.get(rel) or {}
            state[rel] = {
                **err_prev,
                "error_count": err_prev.get("error_count", 0) + 1,
                "status": "error",
            }
            _save_state(state_path, state)
            events.append({"type": "error", "source": rel, "detail": str(e)})

    flush()  # 尾批

    # 僵尸防线：状态里见过、目录里没有 → 清库（库从未建过则无旧块，跳过）
    for rel in [r for r in state if r != "fingerprint" and r not in scanned]:
        del state[rel]
        if store is None:
            try:
                store = Store(db_dir, table_name, embedder.model_name)  # 只删：维度从库元数据读
            except ValueError:
                store = None  # 表不存在 → 本来就没有旧块
        if store is not None:
            store.delete_source(rel)
            events.append({"type": "delete", "source": rel})

    state["fingerprint"] = fp  # 指纹先赋值，再统一落盘
    _save_state(state_path, state)

    # 作业末重建 FTS（hybrid 前提）；库里没数据就跳过（空库建索引未实测）
    if store is not None and store._table.count_rows() > 0:
        store.ensure_fts()

    return report, events
