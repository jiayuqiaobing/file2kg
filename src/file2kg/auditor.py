"""Auditor：作业审计（JSONL 只追加）——差异化核心：每步可回放、每次可追责。

一次作业 = 一个文件 `audit/<job_id>.jsonl`（天然轮转：每作业一文件）。

成熟经验落地（2026-09-02 网搜确认，来源见项目文档第 9 节）：
- 只追加 + 单写者 + 单次 write（完整性三支柱）；作业重跑续写在**同一文件**，绝不复写历史
- 尾部撕裂（torn tail）：崩溃半行 = 没有 `\n` 结尾 → 打开时**截断修复**（repair 并记录字节数）；
  没写完换行 = 未提交，一律视为未发生（保守协议）
- 中间坏行（有换行但 parse 失败）= 磁盘/篡改信号 → **硬错误抛出来**，绝不在坏日志上接弄
- seq 连续序列号：`seq = 尾行 + 1`；断档 = 日志被动手脚（读端可查）
- 存储失败不静默改址：写失败直接抛（带缺口的日志比坦白失败的运行更危险）
- flush() 每事件、close() 收尾 fsync（Windows 每行 fsync 太贵，不做）
- job_id 消毒：只留 [A-Za-z0-9_.-]，防路径注入
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

from .types import JobReport

_SCHEMA = 1
_JOB_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _sanitize(job_id: str) -> str:
    safe = _JOB_ID_RE.sub("_", job_id) or "job"
    return safe if not safe.startswith(".") else "_" + safe  # 点开头 ≈ 隐藏文件，避免


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Auditor:
    """一个作业的审计写手。`with Auditor(...) as a: a.log(event)`。

    事件行协议（schema v1，job_start 行写明）：{"seq": int, "ts": str, "type": str, ...}。
    seq 从 1 起；job_start 行 seq=0；job_end 行携带 report 摘要。
    log() 只接受带 "type" 的 dict；seq/ts 由 Auditor 注入（调用方无法伪造）。
    """

    def __init__(self, audit_dir: str, job_id: str) -> None:
        self.path = Path(audit_dir) / f"{_sanitize(job_id)}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._closed = False

        if self.path.exists():
            self._repair_tail(self.path)   # 先裁撕裂尾（丢尾巴）
            self._verify_interior(self.path)  # 再验中间（坏断层硬错误）
            self.seq = _last_seq(self.path)
            self._f = open(self.path, "a", encoding="utf-8", newline="")
            return

        self.seq = 0
        self._f = open(self.path, "a", encoding="utf-8", newline="")
        self._write({"seq": self.seq, "ts": _now(), "type": "job_start",
                     "job_id": _sanitize(job_id), "schema": _SCHEMA})

    # ---------- 写 ----------

    def log(self, event: dict) -> int:
        """追加一条事件（type 必填；seq/ts 自动注入）。返回本条 seq。"""
        if self._closed:
            raise RuntimeError(f"审计日志已关闭: {self.path}")
        if not event.get("type"):
            raise ValueError("事件必须带 type")
        self.seq += 1
        entry = dict(event)
        entry["seq"] = self.seq
        entry["ts"] = _now()
        self._write(entry)
        return self.seq

    def close(self, report: JobReport | None = None) -> None:
        """收尾：先写 job_end（可选 report 摘要），再 flush+fsync。幂等。"""
        if self._closed:
            return
        if report is not None:
            self.log({
                "type": "job_end",
                "report": {
                    "files_total": report.files_total,
                    "ingested": report.ingested,
                    "dupes": report.dupes,
                    "skipped": report.skipped,
                    "failed": report.failed,
                    "chunk_count": len(report.chunks),
                },
            })
        self._f.flush()
        os.fsync(self._f.fileno())
        self._f.close()
        self._closed = True

    def __enter__(self) -> "Auditor":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------- 实现 ----------

    def _write(self, entry: dict) -> None:
        self._f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._f.flush()

    @staticmethod
    def _repair_tail(path: Path) -> int:
        """截断撕裂结尾（无 \\n 收尾的尾段）。返回裁掉的字节数；文件完好返回 0。"""
        data = path.read_bytes()
        if not data or data.endswith(b"\n"):
            return 0
        cut = data.rfind(b"\n") + 1  # 保留最后一个换行（含）之前的全部内容
        with open(path, "ab") as f:
            f.truncate(cut)
        return len(data) - cut

    @staticmethod
    def _verify_interior(path: Path) -> None:
        """每行必须是合法 JSON 且非空（中间坏行 = 硬错误，绝不静默重来）。"""
        with open(path, "r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                if not line.strip():
                    raise RuntimeError(f"审计日志第 {lineno} 行是空行——正常追加不会产生，人工检查: {path}")
                try:
                    json.loads(line)
                except json.JSONDecodeError as e:
                    raise RuntimeError(
                        f"审计日志第 {lineno} 行损坏（{e}）——中间坏行不修，人工处理: {path}"
                    ) from e


def _last_seq(path: Path) -> int:
    """读文件尾部最后一行的 seq（O(n) 读一次；审计文件一作业一个，毫秒级）。"""
    last = ""
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                last = line
    if not last:
        return 0
    return int(json.loads(last).get("seq", -1))
