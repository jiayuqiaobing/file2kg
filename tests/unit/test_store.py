"""store.py 质检单：维度自检拒开、幂等 upsert、hybrid 重排、源清退。

约定：全部假数据（dim=4）、不碰真实模型不联网；断言基于 2026-09-02 冒烟实测
（scripts/smoke_store.py 全过后的行为承诺——含 delete 单引号语法、RRF 排序、
_lrelevance_score 字段形状这些版本敏感点）。
"""

import pytest

from file2kg.store import Store
from file2kg.types import Chunk

DIM = 4


def _mk(text: str, source: str, vec: list[float]) -> Chunk:
    c = Chunk.from_text(text, source)
    c.embedding = vec
    c.emb_model = "假模型"
    return c


def _sample():
    return [
        _mk("默认监听端口是 9000 端口", "a.md", [0.1, 0.2, 0.3, 0.4]),
        _mk("端口转发功能需要在配置里开启", "a.md", [0.15, 0.2, 0.3, 0.4]),
        _mk("人类历史上最伟大的发明", "b.md", [0.9, -0.2, 0.1, -0.5]),
    ]


@pytest.fixture
def store(tmp_path):
    return Store(str(tmp_path), "t", "假模型", DIM)


# ---------- 建库 / 维度自检（前车之鉴 9.2） ----------

def test_create_records_model_and_dim(store):
    """建库：schema 元数据写好 model/dim（create_table 不收 metadata 的替代方案）。"""
    meta = dict(store._table.schema.metadata or {})
    assert meta[b"model"] == "假模型".encode("utf-8")
    assert meta[b"dim"] == str(DIM).encode("utf-8")


def test_reopen_same_model_dim_ok(tmp_path):
    """同模型同维度重开：正常打开，不折腾。"""
    s = Store(str(tmp_path), "t", "假模型", DIM)
    s2 = Store(str(tmp_path), "t", "假模型", DIM)  # 不得抛错
    assert s2.table_name == "t"


def test_reopen_wrong_dim_rejects(tmp_path):
    """换模型重开：维度不符——拒开！错误信息把两边维度/模型名都说清楚。"""
    Store(str(tmp_path), "t", "假模型", DIM)
    with pytest.raises(RuntimeError, match="假模型.*4维.*另一模型.*8维.*不兼容"):
        Store(str(tmp_path), "t", "另一模型", 8)


def test_create_table_makes_chunk_id_index(store):
    """建库即建 chunk_id 索引（BTree；merge_insert 前提，实测缺它必炸）。"""
    inds = store._table.list_indices()  # 实测 API 名（0.38）
    assert any(i.columns == ["chunk_id"] for i in inds)


# ---------- 写入：幂等 upsert ----------

def test_add_persists_rows(store):
    store.add_chunks(_sample())
    assert store._table.count_rows() == 3


def test_add_same_chunk_id_idempotent(store):
    """同 chunk_id 重复 add：覆盖不重复（断点重跑/重复摄入不产生脏行）。"""
    store.add_chunks(_sample())
    store.add_chunks(_sample())  # 再跑一遍
    assert store._table.count_rows() == 3


def test_add_empty_is_noop(store, capsys):
    store.add_chunks([])  # 不炸
    assert store._table.count_rows() == 0


def test_add_embeddingless_chunk_rejected(store):
    """未向量化的 chunk 不许入库——管道顺序错也要当场报错，不静默。"""
    bad = Chunk.from_text("没经过 embedder 的块", "c.md")
    with pytest.raises(ValueError, match="未向量化"):
        store.add_chunks([bad])


# ---------- 源清退 ----------

def test_delete_source_removes_rows(store, tmp_path):
    store.add_chunks(_sample())
    store.delete_source("a.md")
    assert store._table.count_rows() == 1  # 只清 a.md，b.md 还剩
    sources = store._table.to_arrow().column("source").to_pylist()  # 不依赖 pandas
    assert set(sources) == {"b.md"}


# ---------- 检索：hybrid + RRF ----------

def test_query_hybrid_ranks_text_hits_first(store):
    """句号：文本命中（端口）的两条分列前二，无关内容垫底（实测 RRF 重排如此）。"""
    store.add_chunks(_sample())
    store.ensure_fts()
    res = store.query([0.1, 0.2, 0.3, 0.4], text="端口", k=3)
    assert len(res) == 3
    assert res[0]["text"].__contains__("端口")
    assert res[1]["text"].__contains__("端口")
    assert res[2]["text"] == "人类历史上最伟大的发明"
    assert res[0]["chunk_id"] != res[1]["chunk_id"]


def test_query_hybrid_carries_relevance_score(store):
    """重排结果带 _relevance_score 字段（实测字段名）。"""
    store.add_chunks(_sample()[:2])
    store.ensure_fts()
    res = store.query([0.1, 0.2, 0.3, 0.4], text="端口", k=2)
    assert all("_relevance_score" in r for r in res)
    assert all(r["_relevance_score"] > 0 for r in res)


def test_query_vector_only_orders_by_similarity(store):
    """纯向量模式：与查询向量余弦最近的在最前（c1 与查询向量完全一致）。"""
    store.add_chunks(_sample())
    res = store.query([0.1, 0.2, 0.3, 0.4], k=2, hybrid=False)
    assert res[0]["chunk_id"] == _sample()[0].chunk_id


def test_query_hybrid_requires_text(store):
    """hybrid 模式缺关键词：当场报错（FTS 没路由到要喂的词，别静默降级）。"""
    store.add_chunks(_sample())
    with pytest.raises(ValueError, match="关键词"):
        store.query([0.1, 0.2, 0.3, 0.4])


# ---------- FTS 索引重建 ----------

def test_ensure_fts_twice_is_idempotent(store):
    """两次调用不炸（replace=True 重建=幂等；作业末每次调都安全）。"""
    store.add_chunks(_sample()[:2])
    store.ensure_fts()
    store.ensure_fts()
