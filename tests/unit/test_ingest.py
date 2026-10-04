"""ingest.py 质检单：增量状态机全部分支。

约定：注入假 Embedder（dim=4 固定向量，记录批次调用数）不碰模型；
Store 用真实 lancedb（tmp_path 临时库）。断言基于 2026-09-02 冒烟实测数字
（scripts/smoke_ingest.py 验证过的行数/事件矩阵）。
"""

import json
import os
import shutil
from pathlib import Path

import pytest

from file2kg.ingest import ingest

TEXT_A = "这是安装文档的说明内容。第二句用于凑长度。第三句把字数加够。第四句重复填充。" * 4  # >30 字
TEXT_B = "# 标题\n端口配置说明。第二句。第三句。第四句。第五句。第六句。" * 2


@pytest.fixture
def fake_embedder(monkeypatch):
    calls = {"batches": 0}

    class _Fake:
        def __init__(self, **kw):
            self.model_name = kw.get("model") or "Qwen/Qwen3-Embedding-0.6B"

        def embed(self, chunks):
            calls["batches"] += 1
            for c in chunks:
                c.embedding = [0.1, 0.2, 0.3, 0.4]
                c.emb_model = self.model_name
            return chunks

    monkeypatch.setattr("file2kg.ingest.Embedder", _Fake)
    return calls


@pytest.fixture
def workdir(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    db = tmp_path / "db"
    db.mkdir()
    (docs / "a.md").write_text(TEXT_A, encoding="utf-8")
    (docs / "b.md").write_text(TEXT_B, encoding="utf-8")
    return str(docs), str(db)


def _rows(db_dir: str) -> list[str]:
    import lancedb

    conn = lancedb.connect(db_dir)
    tables = conn.list_tables().tables
    if "docs" not in tables:
        return []
    return conn.open_table("docs").to_arrow().column("source").to_pylist()


def _by_type(events, kind):
    return [e for e in events if e["type"] == kind]


def _state(db_dir: str) -> dict:
    return json.loads((Path(db_dir) / "state.json").read_text(encoding="utf-8"))


# ---------- 首次摄入 ----------

def test_first_run_persists_and_reports(workdir, fake_embedder):
    docs, db = workdir
    report, events = ingest(docs, db)
    assert report.files_total == 2
    assert report.ingested == 2
    assert report.failed == 0
    assert len(_by_type(events, "create")) == 2
    assert sorted(e["source"] for e in _by_type(events, "create")) == ["a.md", "b.md"]
    assert len(report.chunks) >= 2  # 每文件至少 1 块（实测 2）
    state = _state(db)
    assert state["a.md"]["status"] == "ok"
    assert "fingerprint" in state


def test_second_run_all_skip(workdir, fake_embedder):
    docs, db = workdir
    ingest(docs, db)
    report, events = ingest(docs, db)
    assert report.dupes == 2  # a/b 全跳过（实测）
    assert report.ingested == 0
    assert fake_embedder["batches"] == 1  # 第二次运行零嵌入调用
    assert len(_rows(db)) == 2  # 行数不动


# ---------- 修改：先删后增 ----------

def test_modify_file_updates_and_removes_old(workdir, fake_embedder):
    docs, db = workdir
    ingest(docs, db)
    Path(docs, "a.md").write_text(TEXT_A + "更新后才有的新句子。往右边续写。", encoding="utf-8")
    report, events = ingest(docs, db)
    assert len(_by_type(events, "update")) == 1
    assert report.ingested == 1
    rows = _rows(db)
    assert rows.count("a.md") == 1  # 旧 a 块被清掉、新块 1 个（实测：库净 2 行）
    assert rows.count("b.md") == 1


# ---------- 僵尸防线 ----------

def test_deleted_file_cleared_from_db(workdir, fake_embedder):
    docs, db = workdir
    ingest(docs, db)
    Path(docs, "b.md").unlink()
    report, events = ingest(docs, db)
    assert len(_by_type(events, "delete")) == 1
    assert _rows(db) == ["a.md"]  # 实测：库行数 1、只剩 a


def test_short_file_flips_to_dropped(workdir, fake_embedder):
    """清洗后为空（<30 字）→ 记 dropped 状态，绝不再白洗。"""
    docs, db = workdir
    Path(docs, "short.md").write_text("短文", encoding="utf-8")
    ingest(docs, db)
    ingest(docs, db)  # 第二次不再重洗（dropped 状态 + mtime 粗筛，files_total=3 但 a/b/ 全部跳过）
    state = _state(db)
    assert state["short.md"]["status"] == "dropped"


# ---------- mtime 误报 ----------

def test_touch_without_change_does_not_reembed(workdir, fake_embedder):
    docs, db = workdir
    ingest(docs, db)
    p = Path(docs, "a.md")
    st = p.stat()
    os.utime(p, (st.st_mtime + 5, st.st_mtime + 5))  # 只改 mtime，内容没变
    report, events = ingest(docs, db)
    assert any(
        e["type"] == "skip" and e["source"] == "a.md" and "hash" in e["detail"] for e in events
    )
    assert fake_embedder["batches"] == 1  # 没有第二轮嵌入


# ---------- 坏文件：error_count 上限 ----------

def test_bad_file_error_limit_skips_without_reading(workdir, fake_embedder):
    docs, db = workdir
    Path(docs, "bad.md").write_bytes(b"\xc3\x28\x00")  # 四个编码全挂
    for i in range(1, 5):
        _, events = ingest(docs, db)
        if i < 4:
            assert any(e["type"] == "error" and "超限" not in e.get("detail", "") for e in events)
        else:
            assert any(e["detail"] == "失败超限跳过" for e in _by_type(events, "error"))
    state = _state(db)
    assert state["bad.md"]["error_count"] == 3  # 停在 3（实测第 4 次不再读数）


def test_force_retries_error_file(workdir, fake_embedder):
    docs, db = workdir
    Path(docs, "bad.md").write_bytes(b"\xc3\x28\x00")
    for _ in range(4):
        ingest(docs, db)
    _, events = ingest(docs, db, force=True)
    assert any(e["type"] == "error" and "超限" not in e.get("detail", "") for e in events)


# ---------- config 指纹 ----------

def test_fingerprint_change_warns(workdir, fake_embedder):
    docs, db = workdir
    ingest(docs, db)
    _, events = ingest(docs, db, chunk_size=256)
    assert any(e["type"] == "warning" and "指纹" in e["detail"] for e in events)


# ---------- 目录语义 ----------

def test_ignores_skip_dirs_and_unsupported_ext(workdir, fake_embedder):
    docs, db = workdir
    Path(docs, ".git").mkdir()
    Path(docs, ".git", "x.md").write_text(TEXT_A, encoding="utf-8")
    Path(docs, "node_modules").mkdir()
    Path(docs, "node_modules", "y.md").write_text(TEXT_A, encoding="utf-8")
    Path(docs, "noise.xlsx").write_bytes(b"not processed")
    report, _ = ingest(docs, db)
    assert report.files_total == 2  # 只有 a.md b.md（跳过 .git/node_modules/xlsx）


def test_missing_docs_dir_raises(workdir):
    docs, db = workdir
    with pytest.raises(ValueError, match="不存在"):
        ingest(str(Path(docs) / "nope"), db)


def test_empty_dir_clears_all(workdir, fake_embedder):
    docs, db = workdir
    ingest(docs, db)
    shutil.rmtree(docs)
    Path(docs).mkdir()
    report, events = ingest(docs, db)
    assert len(_by_type(events, "delete")) == 2  # a/b 全清（实测空目录事件）
    assert _rows(db) == []
    state = _state(db)
    assert "a.md" not in state and "b.md" not in state  # 状态键被移除


# ---------- Embedder 注入（serve 复用常驻模型） ----------

class _InjectedEmbedder:
    """外部传进来的 Embedder：记录自己被调用了几次。"""

    model_name = "Qwen/Qwen3-Embedding-0.6B"

    def __init__(self) -> None:
        self.batches = 0

    def embed(self, chunks):
        self.batches += 1
        for c in chunks:
            c.embedding = [0.1, 0.2, 0.3, 0.4]
            c.emb_model = self.model_name
        return chunks


def test_injected_embedder_is_used_and_no_second_one_built(workdir, fake_embedder):
    """传入 embedder 时：用它，且**不另建**一个（serve 复用常驻模型的支点）。

    若这里另建，服务里会同时存在两份 639MB 模型——"模型常驻"名不副实。
    """
    docs, db = workdir
    injected = _InjectedEmbedder()
    report, _ = ingest(docs, db, embedder=injected)
    assert report.ingested == 2
    assert injected.batches >= 1  # 注入的实例真被用了
    assert fake_embedder["batches"] == 0  # 没有另建 Embedder（fixture 计数保持 0）


def test_default_path_still_self_builds_embedder(workdir, fake_embedder):
    """不传 embedder 时：行为不变，仍由 ingest 自建 —— **配对断言**。

    缺了这条，一个"永远忽略注入参数、只用自己的"实现照样能通过上一条，
    而 CLI 路径（依赖自建）可能在别处悄悄坏掉。
    """
    docs, db = workdir
    report, _ = ingest(docs, db)
    assert report.ingested == 2
    assert fake_embedder["batches"] >= 1  # 自建的那个被调用了
