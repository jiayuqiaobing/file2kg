"""serve/http_api.py 质检单：/search 契约、错误契约、以及"不装死"。

约定：假 Embedder（不碰模型）+ 真 lancedb 临时库 + starlette TestClient（内存，不开端口）。

本文件把 research.md C4 钉死在断言里：`Store.query()` 返回的是**整行含完整向量**，
API 必须投影——否则每个响应都拖一条 1024 维浮点数组。
"""

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
    ("配置文件在 config.yaml，不要手改。", "配置.md"),
]


class _FakeEmbedder:
    """假嵌入模型：任何查询都返回 VEC（与库里向量相同 → 必命中）。"""

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


def _make_db(db_path, with_fts: bool = True) -> str:
    store = Store(str(db_path), "docs", MODEL, DIM)
    for text, source in DOCS:
        c = Chunk.from_text(text, source)
        c.embedding = list(VEC)
        c.emb_model = MODEL
        store.add_chunks([c])
    if with_fts:
        store.ensure_fts()
    return str(db_path)


@pytest.fixture(autouse=True)
def fake_embedder(monkeypatch):
    monkeypatch.setattr(serve_app, "Embedder", _FakeEmbedder)


@pytest.fixture
def db_dir(tmp_path):
    p = tmp_path / "db"
    p.mkdir()
    return _make_db(p)


@pytest.fixture
def svc(db_dir):
    return serve_app.build_service(ServeConfig(db_dir=db_dir))


@pytest.fixture
def client(svc):
    # raise_server_exceptions=False：我们要测的是**对外契约**（状态码+错误体），
    # 而不是 Starlette 内部是否重抛。
    with TestClient(svc.app, raise_server_exceptions=False) as c:
        c.service = svc
        yield c


def _search(client, **body):
    return client.post("/search", json=body)


# ---------- 正常契约 ----------

def test_search_returns_hits(client):
    r = _search(client, query="默认端口是多少")
    assert r.status_code == 200
    data = r.json()
    assert data["count"] == len(data["results"]) > 0
    assert data["mode"] == "hybrid"


def test_hit_fields_are_exactly_the_projection(client):
    """命中字段 = SearchHit 投影，一个不多一个不少（data-model.md §2.3）。"""
    hit = _search(client, query="端口").json()["results"][0]
    assert set(hit) == {"chunk_id", "text", "source", "heading", "page", "score"}


def test_response_never_leaks_vector_or_text_hash(client):
    """C4 铁证：整行里带着 vector 与 text_hash，投影 MUST 剔除。

    漏了这条，每个检索响应都会拖一条 DIM 维浮点数组——浪费带宽 + 泄漏内部表示。
    """
    body = _search(client, query="端口").json()
    for hit in body["results"]:
        assert "vector" not in hit
        assert "text_hash" not in hit
        assert "emb_model" not in hit
    assert "vector" not in body


def test_results_match_direct_store_query(client):
    """SC-006：服务结果与直接走 Store 的结果逐条一致（不引入第二套检索口径）。"""
    svc = client.service
    r = _search(client, query="端口", k=3)
    served = [(h["chunk_id"], h["score"]) for h in r.json()["results"]]

    direct = svc.store.query(list(VEC), text="端口", k=3, hybrid=True)
    expected = [(row["chunk_id"], row["_relevance_score"]) for row in direct]
    assert served == expected


def test_vector_only_mode(client):
    r = _search(client, query="端口", hybrid=False)
    assert r.status_code == 200
    assert r.json()["mode"] == "vector"


# ---------- 参数校验 ----------

@pytest.mark.parametrize("bad_k", [0, -1, 51, 9999])
def test_k_out_of_range_rejected(client, bad_k):
    r = _search(client, query="端口", k=bad_k)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "INVALID_REQUEST"
    assert r.json()["error"]["hint"]


@pytest.mark.parametrize("bad_query", ["", "   ", "\n\t "])
def test_empty_query_rejected(client, bad_query):
    r = _search(client, query=bad_query)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "INVALID_REQUEST"


def test_missing_query_rejected(client):
    r = client.post("/search", json={"k": 3})
    assert r.status_code == 400


def test_malformed_json_rejected(client):
    r = client.post("/search", content=b"{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "INVALID_REQUEST"


# ---------- 错误契约的形状 ----------

def test_error_body_shape(client):
    err = _search(client, query="", k=1).json()["error"]
    assert set(err) == {"code", "message", "hint"}
    assert all(isinstance(err[k], str) and err[k] for k in err)


def test_unknown_route_returns_structured_404(client):
    r = client.get("/nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "ROUTE_NOT_FOUND"


# ---------- 常驻语义：模型只加载一次（SC-002，本功能的核心承诺） ----------

def test_model_loaded_once_across_many_searches(tmp_path, monkeypatch):
    """连续 100 次检索，进程内 Embedder 只被**构造**一次（SC-002）。

    计数点放在构造上：每 new 一个 Embedder 就意味着一次模型加载。
    这条一旦红，"消除冷启动"就是空话——服务只是把慢命令包了层壳。
    """
    constructions = {"n": 0}

    class _CountingEmbedder(_FakeEmbedder):
        def __init__(self, *a, **kw):
            constructions["n"] += 1
            super().__init__(*a, **kw)

    monkeypatch.setattr(serve_app, "Embedder", _CountingEmbedder)

    p = tmp_path / "db"
    p.mkdir()
    svc = serve_app.build_service(ServeConfig(db_dir=_make_db(p)))
    assert constructions["n"] == 1  # 装配时构造一次

    with TestClient(svc.app, raise_server_exceptions=False) as c:
        for _ in range(100):
            assert c.post("/search", json={"query": "端口"}).status_code == 200

    assert constructions["n"] == 1  # 100 次检索之后，仍然是那一个
    assert svc.embedder.is_loaded is True  # 而且它已被加载、常驻


# ---------- 宪法原则 I / FR-003 ----------

def test_service_works_without_any_api_key(client):
    """零 key 默认可用：没配 FILE2KG_API_KEY 也能正常检索（宪法原则 I）。"""
    assert client.service.config.api_key is None
    assert _search(client, query="端口").status_code == 200


# ---------- 不装死（FR-018 / SC-007） ----------

def test_service_survives_malformed_requests(client):
    """连发一串畸形请求后，服务仍能正常响应——只拒绝请求，不殉职。"""
    for _ in range(3):
        client.post("/search", content=b"}{", headers={"Content-Type": "application/json"})
        _search(client, query="", k=0)
        client.get("/nope")
    assert _search(client, query="端口").status_code == 200


# ---------- 索引缺失：报错不降级（research.md D6） ----------

def test_hybrid_without_fts_index_returns_503(tmp_path):
    p = tmp_path / "nofts"
    p.mkdir()
    svc = serve_app.build_service(ServeConfig(db_dir=_make_db(p, with_fts=False)))
    with TestClient(svc.app, raise_server_exceptions=False) as c:
        r = c.post("/search", json={"query": "端口"})
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "INDEX_MISSING"
        assert "ingest" in r.json()["error"]["hint"]

        # 配对：同一库显式改纯向量**必须**能跑——证明不是"整个库坏了"
        ok = c.post("/search", json={"query": "端口", "hybrid": False})
        assert ok.status_code == 200


# ---------- 库运行期消失：503 且不崩（contracts §5） ----------

def test_store_unavailable_returns_503_and_stays_alive(tmp_path):
    import shutil

    p = tmp_path / "gone"
    p.mkdir()
    svc = serve_app.build_service(ServeConfig(db_dir=_make_db(p)))
    with TestClient(svc.app, raise_server_exceptions=False) as c:
        assert c.post("/search", json={"query": "端口"}).status_code == 200
        shutil.rmtree(p)  # 库目录被删掉
        r = c.post("/search", json={"query": "端口"})
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "STORE_UNAVAILABLE"
        # 服务没死：错误体仍是结构化的，进程还能继续响应
        assert c.get("/nope").status_code == 404


# ---------- 空结果不是错误 ----------

def test_empty_result_is_200_with_note(tmp_path):
    p = tmp_path / "empty"
    p.mkdir()
    Store(str(p), "docs", MODEL, DIM)  # 建库但不放任何 chunk（空表不建 FTS——未实测路径）
    svc = serve_app.build_service(ServeConfig(db_dir=str(p)))
    with TestClient(svc.app, raise_server_exceptions=False) as c:
        r = c.post("/search", json={"query": "随便问问", "hybrid": False})
        assert r.status_code == 200
        assert r.json()["results"] == []
        assert r.json()["note"]  # 空结果给说明，不是裸空
