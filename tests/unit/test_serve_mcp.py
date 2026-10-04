"""serve/mcp_tools.py 质检单：工具清单的成对断言 + 真实 MCP 调用 + 挂载回归钉子。

不开端口、不联网：用 FastMCP 的**内存传输** `Client(mcp)` 直接调工具；
`/mcp` 的 HTTP 挂载用 starlette TestClient 验（同样是内存）。
"""

import asyncio

import pytest
from starlette.testclient import TestClient

from file2kg.config import ServeConfig
from file2kg.serve import app as serve_app
from file2kg.store import Store
from file2kg.types import Chunk

MODEL = "Qwen/Qwen3-Embedding-0.6B"
DIM = 4
VEC = [1.0, 0.0, 0.0, 0.0]

DOCS = [
    ("默认端口是 9000，改端口要动配置文件。", "安装.md"),
    ("安装前请先装好 Docker 与 Python。", "安装.md"),
]

INIT_BODY = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {},
               "clientInfo": {"name": "pytest", "version": "0"}},
}
INIT_HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}


class _FakeEmbedder:
    def __init__(self, model=None, api_key=None, api_url=None) -> None:
        self.model_name = model or MODEL
        self._backend = None

    @property
    def is_loaded(self) -> bool:
        return self._backend is not None

    def embed(self, chunks):
        self._backend = object()
        for c in chunks:
            c.embedding = list(VEC)
            c.emb_model = self.model_name
        return chunks


@pytest.fixture(autouse=True)
def fake_embedder(monkeypatch):
    monkeypatch.setattr(serve_app, "Embedder", _FakeEmbedder)


@pytest.fixture
def db_dir(tmp_path):
    db = tmp_path / "db"
    db.mkdir()
    store = Store(str(db), "docs", MODEL, DIM)
    for text, source in DOCS:
        c = Chunk.from_text(text, source)
        c.embedding = list(VEC)
        c.emb_model = MODEL
        store.add_chunks([c])
    store.ensure_fts()
    return str(db)


@pytest.fixture
def svc(db_dir):
    return serve_app.build_service(ServeConfig(db_dir=db_dir))


def _tool_names(service) -> set[str]:
    return {t.name for t in asyncio.run(service.mcp.list_tools())}


def _call(service, name: str, args: dict):
    from fastmcp import Client

    async def scenario():
        async with Client(service.mcp) as c:
            return await c.call_tool(name, args)

    return asyncio.run(scenario())


# ---------- 工具清单：§① 在 MCP 侧的落点 ----------

def test_read_only_exposes_exactly_read_tools(svc):
    """只读模式：清单里**恰好**是两个读工具。

    用 `==` 而非 `<=`：空注册表也能通过"不含 ingest"的断言，那是假证明。
    """
    assert _tool_names(svc) == {"service_info", "search"}


def test_write_tool_absent_in_read_only(svc):
    """写工具**不在**清单里——是"没有"，不是"有但报错"（FR-008 / US2 场景 4）。"""
    assert "ingest" not in _tool_names(svc)


def test_capabilities_and_tools_are_consistent(svc):
    """能力清单与工具名集合**恒等**（同一份 CapabilityManifest 驱动，contracts §5）。"""
    assert _tool_names(svc) == set(svc.capabilities)


# ---------- 真实调用 ----------

def test_service_info_tool_returns_descriptor(svc):
    data = _call(svc, "service_info", {}).data
    assert set(data) == {"service", "db_dir", "table", "model", "dim", "mode", "warm", "capabilities"}
    assert data["mode"] == "read-only"
    assert data["warm"] is False  # 查自述不加载模型


def test_search_tool_returns_hits_with_source(svc):
    data = _call(svc, "search", {"query": "端口"}).data
    assert data["count"] > 0
    hit = data["results"][0]
    assert set(hit) == {"chunk_id", "text", "source", "heading", "page", "score"}
    assert hit["source"]  # Agent 能据此引用到具体出处


def test_search_tool_and_http_return_identical_results(svc):
    """两个入口**同一份实现**：MCP 与 HTTP 的结果必须逐条一致。

    各写一套的话迟早漂成两套打分口径，客户端从哪个入口拿结果会不一样。
    """
    mcp_data = _call(svc, "search", {"query": "端口", "k": 3}).data
    with TestClient(svc.app, raise_server_exceptions=False) as c:
        http_data = c.post("/search", json={"query": "端口", "k": 3}).json()
    assert [h["chunk_id"] for h in mcp_data["results"]] == [h["chunk_id"] for h in http_data["results"]]
    assert [h["score"] for h in mcp_data["results"]] == [h["score"] for h in http_data["results"]]


def test_search_tool_error_carries_actionable_hint(tmp_path):
    """工具报错也要带**修复指引**——Agent 同样需要知道下一步做什么（FR-016 的精神）。"""
    p = tmp_path / "nofts"
    p.mkdir()
    store = Store(str(p), "docs", MODEL, DIM)
    c = Chunk.from_text(DOCS[0][0], "a.md")
    c.embedding = list(VEC)
    c.emb_model = MODEL
    store.add_chunks([c])  # 刻意不建 FTS
    svc = serve_app.build_service(ServeConfig(db_dir=str(p)))

    with pytest.raises(Exception) as ei:  # fastmcp 抛 ToolError
        _call(svc, "search", {"query": "端口"})
    msg = str(ei.value)
    assert "ingest" in msg  # hint 传到了


# ---------- 挂载与 lifespan：U1/U2 的回归钉子 ----------

def test_mcp_is_mounted_at_slash_mcp(svc):
    """挂载路径必须是 /mcp（不能叠成 /mcp/mcp）——U1 实测结论的回归钉子。"""
    paths = {getattr(r, "path", "") for r in svc.app.routes}
    assert "/mcp" in paths


def test_initialize_succeeds_over_http(svc):
    """lifespan 已接线：POST /mcp 的 initialize 必须 200。

    U2 实测：漏接 lifespan 时这里会抛 RuntimeError（session manager 未初始化）。
    这条一红，说明有人把 `lifespan=mcp_app.lifespan` 去掉了。
    """
    with TestClient(svc.app) as c:
        r = c.post("/mcp", json=INIT_BODY, headers=INIT_HEADERS)
    assert r.status_code == 200


def test_service_info_does_not_load_model(svc):
    """走一遍 MCP 调用后 warm 仍是 False（FR-007 在 MCP 侧的配对断言）。"""
    _call(svc, "service_info", {})
    assert svc.embedder.is_loaded is False
