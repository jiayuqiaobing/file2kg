"""auditor.py 质检单：只追加协议、撕裂修复、坏行硬错误、seq 连续性。

断言基于 2026-09-02 冒烟实测（scripts/smoke_auditor.py 全过后的行为承诺）。
"""

import json
from datetime import datetime
from pathlib import Path

import pytest

from file2kg.auditor import Auditor, _last_seq, _sanitize
from file2kg.types import JobReport


@pytest.fixture
def audit_dir(tmp_path):
    return str(tmp_path)


def _lines(path):
    return [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _parse(path):
    return [json.loads(l) for l in _lines(path)]


def _write_raw(path, text: str):
    with open(path, "a", encoding="utf-8", newline="") as f:
        f.write(text)
        f.flush()


# ---------- 新建 + 行协议 ----------

def test_new_file_writes_header_and_events(audit_dir):
    with Auditor(audit_dir, "job1") as a:
        assert a.seq == 0  # header 行 seq=0
        s1 = a.log({"type": "create", "source": "a.md"})
        assert s1 == 1
        s2 = a.log({"type": "error", "source": "bad.md"})
        assert s2 == 2
    entries = _parse(Path(audit_dir) / "job1.jsonl")
    assert entries[0]["type"] == "job_start"
    assert entries[0]["seq"] == 0 and entries[0]["schema"] == 1
    assert [e["seq"] for e in entries] == [0, 1, 2]  # 连续
    assert entries[1]["source"] == "a.md" and entries[1]["type"] == "create"
    assert all("ts" in e for e in entries)


def test_log_requires_type(audit_dir):
    with Auditor(audit_dir, "job1") as a:
        with pytest.raises(ValueError, match="type"):
            a.log({"source": "a.md"})


# ---------- 续写：同 job 重开 ----------

def test_resume_continues_seq_without_new_header(audit_dir):
    with Auditor(audit_dir, "job1") as a:
        a.log({"type": "create", "source": "a.md"})
        a.log({"type": "update", "source": "b.md"})
    with Auditor(audit_dir, "job1") as a:
        assert a.seq == 2  # 从最后一行 + 1（实测）
        assert a.log({"type": "delete", "source": "c.md"}) == 3
    entries = _parse(Path(audit_dir) / "job1.jsonl")
    assert sum(1 for e in entries if e["type"] == "job_start") == 1  # header 不重写
    assert [e["seq"] for e in entries] == [0, 1, 2, 3]


# ---------- 尾部撕裂 ----------

def test_torn_tail_repaired_and_resumes(audit_dir):
    with Auditor(audit_dir, "job1") as a:
        a.log({"type": "create", "source": "a.md"})
        a.log({"type": "create", "source": "b.md"})
    p = Path(audit_dir) / "job1.jsonl"
    p.write_bytes(p.read_bytes()[:-8])  # 手工砍半行（无 \n 结尾）
    with Auditor(audit_dir, "job1") as a:
        assert a.seq == 1  # 回到最后完整行（实测）
        assert a.log({"type": "skip", "source": "c.md"}) == 2
    entries = _parse(p)
    assert entries[-1]["type"] == "skip"  # 没接在坏尾后面写


def test_torn_tail_returns_repair_bytes(audit_dir):
    p = Path(audit_dir) / "job1.jsonl"
    _write_raw(p, '{"seq": 1}\n{"seq": 2,')  # 半行
    assert Auditor._repair_tail(p) == len(b'{"seq": 2,')  # 裁掉的字节数
    assert p.read_bytes().endswith(b"\n")


def test_intact_file_not_touched(audit_dir):
    p = Path(audit_dir) / "job1.jsonl"
    _write_raw(p, '{"seq": 1}\n')
    assert Auditor._repair_tail(p) == 0  # 完好不裁


# ---------- 中间坏行 / 空行 = 硬错误 ----------

def test_interior_bad_line_hard_error(audit_dir):
    p = Path(audit_dir) / "job1.jsonl"
    _write_raw(p, '{"seq": 1}\nNOT JSON\n{"seq": 2}\n')
    with pytest.raises(RuntimeError, match="第 2 行损坏"):
        Auditor(audit_dir, "job1")


def test_interior_blank_line_hard_error(audit_dir):
    p = Path(audit_dir) / "job1.jsonl"
    _write_raw(p, '{"seq": 1}\n\n{"seq": 2}\n')
    with pytest.raises(RuntimeError, match="空行"):
        Auditor(audit_dir, "job1")


# ---------- 收尾 ----------

def test_close_writes_job_end_with_report_summary(audit_dir):
    from datetime import datetime as d

    a = Auditor(audit_dir, "job1")
    a.log({"type": "create", "source": "a.md"})
    r = JobReport(job_id="j", started_at=d(2026, 9, 2), files_total=3, ingested=1,
                  dupes=1, failed=1)
    a.close(r)
    entries = _parse(Path(audit_dir) / "job1.jsonl")
    end = entries[-1]
    assert end["type"] == "job_end"
    assert end["report"]["files_total"] == 3
    assert end["report"]["chunk_count"] == 0
    assert "chunks" not in end["report"]  # 摘要不带 chunk 明细（防 JSON 膨胀）


def test_close_idempotent(audit_dir):
    a = Auditor(audit_dir, "job1")
    a.log({"type": "create", "source": "a.md"})
    r = JobReport(job_id="j", started_at=datetime(2026, 9, 2))
    a.close(r)
    a.close(r)  # 不得炸、不得重写（实测）
    entries = _parse(Path(audit_dir) / "job1.jsonl")
    assert sum(1 for e in entries if e["type"] == "job_end") == 1


def test_log_after_close_rejected(audit_dir):
    a = Auditor(audit_dir, "job1")
    a.close()
    with pytest.raises(RuntimeError, match="已关闭"):
        a.log({"type": "create", "source": "a.md"})


# ---------- job_id 消毒 ----------

def test_sanitize_blocks_path_tricks():
    assert _sanitize("../evil/name") == "_.._evil_name"  # 点开头加前缀（实测规则）
    assert _sanitize("a" * 80 + "/b") == "a" * 80 + "_b"
    assert "/" not in _sanitize("x/y")
    assert "\\" not in _sanitize("x\\y")
    assert _sanitize("...") == "_..."  # 点开头一律加 _（防隐藏文件/纯点）
    assert _sanitize("") == "job"  # 兜底名


def test_sanitized_path_stays_inside_audit_dir(tmp_path):
    with Auditor(str(tmp_path), "../evil") as a:
        assert a.path == tmp_path / "_.._evil.jsonl"


# ---------- 辅助 ----------

def test_last_seq_of_empty_file_is_zero(audit_dir):
    p = Path(audit_dir) / "j.jsonl"
    p.write_text("", encoding="utf-8")
    assert _last_seq(p) == 0
