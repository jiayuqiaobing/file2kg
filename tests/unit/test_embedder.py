"""embedder.py 质检单：懒加载、参数语义、一库一模标签、归一化。

约定：测试一律注入假后端（不碰真实模型、不联网）——模型层的事由集成测试/实测负责。
"""

from file2kg.embedder import Embedder, _l2_normalize
from file2kg.types import Chunk


class _FakeBackend:
    """记录调用的假模型后端：encode 记录参数，返回 list-of-list（测 list 兜底路径）。"""

    def __init__(self, dim: int = 4) -> None:
        self.dim = dim
        self.calls: list[tuple] = []

    def encode(self, texts, **kwargs):
        self.calls.append((list(texts), kwargs))
        return [[0.1] * self.dim] * len(texts)


def _chunks(n: int = 3) -> list[Chunk]:
    return [Chunk.from_text(f"第{i}段文本", "a.md") for i in range(n)]


# ---------- 懒加载 ----------

def test_constructor_touches_nothing():
    """构造零成本：不加载后端、不带模型名猜测（_backend 哨兵为 None）。"""
    e = Embedder()
    assert e._backend is None
    assert e._mode == "local"


def test_embed_empty_never_loads():
    """空列表：直接返回，连后端都不建（file2kg --help 路径的铁证）。"""
    e = Embedder()
    e._backend = _FakeBackend()
    out = e.embed([])
    assert out == []
    assert e._backend.calls == []  # encode 没被调


# ---------- 参数语义（避坑守护） ----------

def test_default_batch_size_is_cpu_friendly():
    """默认批量 16（CPU 推荐区间 8-16，32+ 可能更慢——网搜避坑）。"""
    assert Embedder().batch_size == 16


def test_encode_kwargs_passed_explicitly():
    """encode 参数全显式：normalize=True（漏=余弦坏）+ progress=False（非交互坏）。"""
    e = Embedder()
    fake = _FakeBackend()
    e._backend = fake
    e.embed(_chunks(2))
    kwargs = fake.calls[0][1]
    assert kwargs["normalize_embeddings"] is True
    assert kwargs["show_progress_bar"] is False
    assert kwargs["batch_size"] == 16


# ---------- 一库一模标签 ----------

def test_local_mode_tags_model():
    """本地模式：emb_model 标上默认模型名（一库一模的记账）。"""
    e = Embedder()
    fake = _FakeBackend()
    e._backend = fake
    chunks = _chunks(2)
    e.embed(chunks)
    assert all(c.emb_model == "Qwen/Qwen3-Embedding-0.6B" for c in chunks)


def test_api_mode_sets_mode_and_accepts_model():
    """API 模式（传 api_key）：_mode=api，模型名可换（dashscope 用 text-embedding-*）。"""
    e = Embedder(model="text-embedding-v4", api_key="sk-test")
    assert e._mode == "api"
    assert e.model_name == "text-embedding-v4"


# ---------- 输出契约 ----------

def test_embedding_filled_and_listified():
    """列表兜底路径：非 numpy 的 list-of-list 也能装进 chunk（双兼容）。"""
    e = Embedder()
    e._backend = _FakeBackend(dim=4)
    chunks = _chunks(3)
    e.embed(chunks)
    assert all(len(c.embedding) == 4 for c in chunks)
    assert all(isinstance(c.embedding, list) for c in chunks)


def test_embed_returns_same_list():
    """就地修改并返回同一列表（链式可用、不复制大列表）。"""
    e = Embedder()
    e._backend = _FakeBackend()
    chunks = _chunks(2)
    assert e.embed(chunks) is chunks


# ---------- 归一化（API 端点默认不一致的兜底） ----------

def test_l2_normalize_makes_unit_vector():
    """L2 归一化：模长=1（余弦与点积一致的根基）。"""
    v = _l2_normalize([3.0, 4.0])
    assert abs(sum(x * x for x in v) - 1.0) < 1e-9


def test_l2_normalize_idempotent():
    """幂等：归一化再归一化不变（API 已归一化的向量无害通过）。"""
    v = _l2_normalize([0.1, 0.2, 0.3])
    assert _l2_normalize(v) == v


def test_l2_normalize_zero_vector_unchanged():
    """零向量除零保护：分母为 0 时原样返回（不炸）。"""
    assert _l2_normalize([0.0, 0.0]) == [0.0, 0.0]
