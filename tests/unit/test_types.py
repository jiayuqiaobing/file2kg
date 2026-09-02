"""types.py 质检单：数据契约是否成立。

覆盖契约点：id/hash 计算、from_text 自动生成、字段默认值、JobReport 计数。
"""

import re

import pytest

from datetime import datetime

from file2kg.types import Chunk, RawDoc, JobReport, AuditChunk, make_hash, make_chunk_id


# ---------- 哈希工具 ----------

def test_make_hash_returns_64_hex():
    """合格身份证：64 位十六进制。"""
    assert re.fullmatch(r"[0-9a-f]{64}", make_hash("任何文本"))


def test_make_hash_is_deterministic():
    """同文本 → 同哈希（幂等）。"""
    assert make_hash("相同内容") == make_hash("相同内容")


def test_make_hash_differs_for_diff_text():
    """不同文本 → 不同哈希。"""
    assert make_hash("内容A") != make_hash("内容B")


def test_make_chunk_id_is_16_hex():
    """chunk_id：16 位十六进制。"""
    assert re.fullmatch(r"[0-9a-f]{16}", make_chunk_id("任意文本"))


def test_chunk_id_is_hash_prefix():
    """chunk_id 是 hash 的前 16 位——规则不打架。"""
    text = "安装包下载地址在官网"
    assert make_chunk_id(text) == make_hash(text)[:16]


# ---------- Chunk ----------

def test_chunk_from_text_auto_fills():
    """from_text 免手填：id、hash 都自动生成。"""
    c = Chunk.from_text("前置条件：Windows 11", "docs/手册.md", heading="安装")
    assert c.chunk_id == make_hash("前置条件：Windows 11")[:16]
    assert c.text_hash == make_hash("前置条件：Windows 11")
    assert c.heading == "安装"
    assert c.source == "docs/手册.md"


def test_chunk_defaults_no_embedding():
    """分块后未向量化：embedding 必须为 None（v0.2 的诚实标志）。"""
    c = Chunk.from_text("还没向量化的块", "a.md")
    assert c.embedding is None
    assert c.page is None
    assert c.emb_model == ""


def test_chunk_same_text_same_id():
    """同一内容两次切分 → 同一 id（增量去重的基础）。"""
    t = "Docker run -p 8000:8000"
    a = Chunk.from_text(t, "x.md")
    b = Chunk.from_text(t, "y.md")
    assert a.chunk_id == b.chunk_id


def test_chunk_holds_embedding():
    """embedder 的活：向量能装进去。"""
    c = Chunk.from_text("测试", "a.md", emb_model="Qwen3-Embedding-0.6B")
    c.embedding = [0.1] * 1024
    assert c.embedding is not None
    assert len(c.embedding) == 1024


# ---------- RawDoc / AuditChunk / JobReport ----------

def test_rawdoc_defaults():
    """RawDoc 默认值：非 PDF 无页码，meta 为空。"""
    d = RawDoc(text="第1页内容", source="notes.txt")
    assert d.page is None
    assert d.meta == {}


def test_auditchunk_is_lightweight_record():
    """审计记录字段必须齐全（README 承诺的溯源项）。"""
    a = AuditChunk(
        chunk_id="abc123", source="手册.md", page=3,
        heading="安装 > 前置条件", emb_model="Qwen3-Embedding-0.6B",
        text_hash="f" * 64,
    )
    assert a.page == 3
    assert a.emb_model == "Qwen3-Embedding-0.6B"


def test_jobreport_zero_defaults():
    """作业报告初始全 0——账本从空开始。"""
    r = JobReport(job_id="job-001", started_at=datetime.now())
    assert r.ingested == 0 and r.dupes == 0 and r.failed == 0 and r.skipped == 0
    assert r.files_total == 0  # 账本第一笔也是 0（此前漏测字段）

    assert r.chunks == [] and r.skipped_files == {} and r.errors == {}
