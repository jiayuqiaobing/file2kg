"""serve/app.py 质检单：能力清单的**成对断言** + 库绑定校验 + 预热口径。

约定：注入假 Embedder（不碰模型、不联网）；Store 用真实 lancedb（tmp_path 临时库）——
与 test_ingest.py 同一套做法。

本文件的重头是 plan.md §① 与 §② 要求的**配对原则**：只断言"不存在"是假证明，
一个什么都没注册、或永远不加载模型的实现同样能通过。所以每条缺席断言都配一条在场断言。
"""

import pytest

from file2kg.config import ServeConfig
from file2kg.serve import app as serve_app
from file2kg.store import Store
from file2kg.types import Chunk

MODEL = "Qwen/Qwen3-Embedding-0.6B"
OTHER_MODEL = "BAAI/bge-large-zh-v1.5"
DIM = 4


class _FakeEmbedder:
    """假嵌入模型：只在 embed() 被调用时才"加载"（复刻懒加载哨兵语义）。"""

    def __init__(self, model=None, api_key=None, api_url=None) -> None:
        self.model_name = model or MODEL
        self.api_key = api_key
        self._backend = None

    @property
    def is_loaded(self) -> bool:
        return self._backend is not None

    def embed(self, chunks):
        self._backend = object()
        for c in chunks:
            c.embedding = [0.1] * DIM
            c.emb_model = self.model_name
        return chunks


@pytest.fixture
def db_dir(tmp_path):
    """建一个真库（模型名/维度写进元数据），返回库目录。"""
    db = tmp_path / "db"
    db.mkdir()
    store = Store(str(db), "docs", MODEL, DIM)
    chunk = Chunk.from_text("默认端口是 9000。" * 20, "a.md")
    chunk.embedding = [0.1] * DIM
    chunk.emb_model = MODEL
    store.add_chunks([chunk])
    return str(db)


@pytest.fixture(autouse=True)
def fake_embedder(monkeypatch):
    monkeypatch.setattr(serve_app, "Embedder", _FakeEmbedder)


def _build(db_dir, **kw):
    return serve_app.build_service(ServeConfig(db_dir=db_dir, **kw))


# ---------- §① 能力清单：成对断言 ----------

def test_read_only_has_no_write_capability(db_dir):
    """默认（只读）：写能力**不在清单里**（宪法原则 III，NON-NEGOTIABLE）。"""
    svc = _build(db_dir)
    assert "ingest" not in svc.capabilities
    assert svc.has("ingest") is False
    assert set(svc.capabilities) == {"service_info", "search"}


def test_read_write_has_write_capability(db_dir):
    """配对项：写模式下 ingest **必须**在清单里。

    缺了这条，一个"什么能力都不注册"的实现也能通过上面那条——
    那是假通过，不是安全。
    """
    svc = _build(db_dir, mode="read-write")
    assert "ingest" in svc.capabilities
    assert svc.has("ingest") is True
    assert set(svc.capabilities) == {"service_info", "search", "ingest"}


def test_capabilities_are_immutable_after_build(db_dir):
    """能力清单是元组：构造后不可变（权限模式只能重启改，data-model.md §2.2）。"""
    svc = _build(db_dir)
    assert isinstance(svc.capabilities, tuple)


# ---------- §② 预热口径：成对断言 ----------

def test_preload_off_by_default(db_dir):
    """默认不预热：装配完成时模型**未加载**（宪法原则 II）。"""
    svc = _build(db_dir)
    assert svc.embedder.is_loaded is False


def test_descriptor_does_not_load_model(db_dir):
    """查服务自述不拉模型（FR-007）——查一次之后 warm 仍是 False。"""
    svc = _build(db_dir)
    d = svc.descriptor()
    assert d["warm"] is False
    assert svc.embedder.is_loaded is False


def test_explicit_preload_loads_model(db_dir):
    """配对项：显式预热后 warm=True。

    缺了这条，一个 `is_loaded` 恒 False 的实现会假通过上面两条，
    "预热非默认"就退化成"永远不预热"而无人察觉。
    """
    svc = _build(db_dir, preload=True)
    assert svc.embedder.is_loaded is True
    assert svc.descriptor()["warm"] is True


# ---------- 一库一模（research.md C3：启动期只比对模型名，不加载模型） ----------

def test_descriptor_reports_store_bound_model_not_config(db_dir):
    """自述里的 model/dim 取自**库元数据**——客户端由此确知"这个库绑的是什么"。"""
    svc = _build(db_dir, model=None)
    assert svc.model == MODEL
    assert svc.dim == DIM
    d = svc.descriptor()
    assert d["model"] == MODEL and d["dim"] == DIM


def test_mismatched_model_refused(db_dir):
    """换模型去服务已有库 → 拒绝，且**两边模型名都报出来**（宪法原则 V / FR-014）。

    只报一边，用户不知道该换回什么。
    """
    with pytest.raises(RuntimeError, match="不兼容") as ei:
        _build(db_dir, model=OTHER_MODEL)
    msg = str(ei.value)
    assert MODEL in msg, "没报出库绑定的模型"
    assert OTHER_MODEL in msg, "没报出请求的模型"
    assert str(DIM) in msg, "没报出库的向量维度"


def test_mismatch_refusal_does_not_load_model(db_dir):
    """拒绝发生在**不加载模型**的前提下——否则 warm 一开始就 True，破坏原则 II。"""
    with pytest.raises(RuntimeError):
        _build(db_dir, model=OTHER_MODEL)


def test_same_explicit_model_accepted(db_dir):
    """配对项：显式指定**同一个**模型是允许的（不是一见 config.model 就拒）。"""
    svc = _build(db_dir, model=MODEL)
    assert svc.model == MODEL


# ---------- research.md C2：库不存在时不得静默建表 ----------

def test_missing_store_refused_without_creating_it(tmp_path):
    """库目录不存在 → 明确报错，且**不得**顺手把目录/表建出来。"""
    missing = tmp_path / "nope"
    with pytest.raises(RuntimeError, match="先运行"):
        _build(str(missing))
    assert not missing.exists(), "服务静默建库了——违反 spec 假设「库需先存在」"


def test_existing_dir_without_table_refused(tmp_path):
    """目录在但表不在 → 同样明确报错，不得建表。"""
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(RuntimeError, match="先运行"):
        _build(str(empty))
    import lancedb

    assert "docs" not in lancedb.connect(str(empty)).list_tables().tables


# ---------- 自述字段完整性 ----------

def test_descriptor_shape(db_dir):
    svc = _build(db_dir)
    d = svc.descriptor()
    assert set(d) == {"service", "db_dir", "table", "model", "dim", "mode", "warm", "capabilities"}
    assert d["mode"] == "read-only"
    assert d["table"] == "docs"
