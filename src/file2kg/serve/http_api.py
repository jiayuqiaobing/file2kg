"""serve/http_api.py：HTTP 入口——统一错误契约 +（各故事逐步加入的）路由。

错误契约见 contracts/http-api.md §5：统一错误体 {"error": {code, message, hint}}，
`hint` MUST 可操作——只说"哪里错了"不合格，必须给出下一步。

**密钥擦除**（宪法原则 I / FR-020）：所有面向外部的错误输出都过 `scrub()`，
服务启动时把配置里的密钥登记进擦除表，确保它不会顺着异常文本漏出去。

「可调用性 = 没有；可发现性 = 有帮助」：只读模式下写路由**根本不注册**，
但兜底 404 处理器认得这是一条被关闭的能力，会给出开启方式。
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import BaseModel, ValidationError
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from ..types import Chunk

log = logging.getLogger("file2kg.serve")

# 已知但可能被关闭的路径 → 关闭时的提示。**这不等于该能力存在**：
# 路由没注册、不可调用，这里只是让 404 别把用户晾着（US3 场景 1）。
DISABLED_ROUTE_HINTS: dict[str, tuple[str, str, str]] = {
    "/ingest": (
        "WRITE_DISABLED",
        "写能力未启用",
        "以 --allow-write 重启服务可开启（当前为只读模式）",
    ),
}

# 需要从对外文本里抹掉的敏感值（密钥等）。由 build_app() 在启动时登记。
_REDACT: list[str] = []


def register_secret(value: str | None) -> None:
    """登记一个必须在对外文本中擦除的敏感值。空值忽略。"""
    if value:
        _REDACT.append(value)


def scrub(text: str) -> str:
    """擦除已登记的敏感值（FR-020 / 宪法原则 I）。"""
    for secret in _REDACT:
        text = text.replace(secret, "***")
    return text


def error_body(code: str, message: str, hint: str) -> dict:
    """统一错误体。message/hint 一律过擦除。"""
    return {"error": {"code": code, "message": scrub(message), "hint": scrub(hint)}}


class ApiError(Exception):
    """带 code/hint 的对外错误：路由里 raise 它，由 handler 渲染成统一错误体。"""

    def __init__(self, status: int, code: str, message: str, hint: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.hint = hint


# ---------- 异常处理器 ----------


def _available_endpoints(app) -> str:
    """可用端点从**实际注册的路由**推导，不硬编码。

    硬编码的提示会承诺不存在的端点——服务在自己的错误信息里说谎，违反原则 IV。
    （真实验收时抓到的：/info 尚未实现，提示却说它在。）
    """
    items: list[str] = []
    for r in getattr(app, "routes", []):
        path = getattr(r, "path", "")
        methods = getattr(r, "methods", None)
        if not path or path.startswith("/mcp"):
            continue
        for m in sorted((methods or set()) - {"HEAD", "OPTIONS"}):
            items.append(f"{m} {path}")
    return "可用端点：" + "、".join(sorted(set(items))) if items else "当前没有可用端点"


async def _handle_not_found(request, exc) -> JSONResponse:  # noqa: ARG001
    path = request.url.path
    if path in DISABLED_ROUTE_HINTS:
        code, message, hint = DISABLED_ROUTE_HINTS[path]
        return JSONResponse(error_body(code, message, hint), status_code=404)
    return JSONResponse(
        error_body("ROUTE_NOT_FOUND", f"无此端点: {path}", _available_endpoints(request.app)),
        status_code=404,
    )


async def _handle_ipv4(request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        error_body("ROUTE_NOT_FOUND" if exc.status_code == 404 else "INVALID_REQUEST",
                   str(exc.detail), "检查请求路径与参数"),
        status_code=exc.status_code,
    )


async def _handle_api_error(request, exc: ApiError) -> JSONResponse:  # noqa: ARG001
    return JSONResponse(error_body(exc.code, exc.message, exc.hint), status_code=exc.status)


async def _handle_unexpected(request, exc: Exception) -> JSONResponse:  # noqa: ARG001
    # 不把裸异常文本抛给调用方（宪法原则 IV）；细节走日志，并同样擦除。
    log.error("服务内部异常: %s", scrub(f"{type(exc).__name__}: {exc}"))
    return JSONResponse(
        error_body("INTERNAL_ERROR", "服务内部错误", "查看服务日志与审计文件定位"),
        status_code=500,
    )


# ---------- 请求解析与校验 ----------


class SearchRequest(BaseModel):
    """检索请求（contracts/http-api.md §3）。收窄的：不复用库行。"""

    query: str
    k: int = 10
    hybrid: bool = True


_FTS_HINT = "先运行一次 file2kg ingest 建立索引；或改用纯向量检索（hybrid=false）"


async def _read_body(request):
    try:
        return await request.json()
    except Exception as e:  # noqa: BLE001 — JSONDecodeError/UnicodeDecodeError 等都是用户输入问题
        raise ApiError(
            400, "INVALID_REQUEST", "请求体不是合法 JSON",
            "以 Content-Type: application/json 发送 JSON 对象",
        ) from e


def _parse_search_request(payload) -> SearchRequest:
    if not isinstance(payload, dict):
        raise ApiError(400, "INVALID_REQUEST", "请求体必须是 JSON 对象", '形如 {"query": "..."}')
    try:
        req = SearchRequest.model_validate(payload)
    except ValidationError as e:
        first = e.errors()[0]
        loc = ".".join(str(x) for x in first.get("loc", ())) or "(根)"
        raise ApiError(
            400, "INVALID_REQUEST", f"参数校验失败：{loc} {first.get('msg')}",
            "query 必填且为非空字符串；k 为 1..50 的整数；hybrid 为布尔",
        ) from e
    if not req.query.strip():
        raise ApiError(400, "INVALID_REQUEST", "query 不能为空或纯空白", "传入非空的检索文本")
    if not 1 <= req.k <= 50:
        raise ApiError(
            400, "INVALID_REQUEST", f"k 越界: {req.k}",
            "k 取值范围 1..50（与 CLI 的 --k 上限一致）",
        )
    return req


def _ensure_store_present(service) -> None:
    """运行期库可用性检查：目录被删/移动 → 503，而不是崩或者静默返回空。"""
    if not Path(service.config.db_dir).exists():
        raise ApiError(
            503, "STORE_UNAVAILABLE", f"库目录不可用: {service.config.db_dir}",
            "检查库路径，或先运行 file2kg ingest 重建库",
        )


def _check_dim(service, real_dim: int) -> None:
    """一库一模的**第二层**：拿到模型实际输出维度后，与库维度比对（research.md C3）。

    不在启动时做，是因为那需要先加载模型——会让 warm 一开始就是 True，破坏原则 II。
    也 MUST NOT 用 `Store(dim=None)` 代替：那条路径只检查"元数据 vs schema"自洽，
    **从不验证调用方模型**。
    """
    if service._dim_checked:  # noqa: SLF001 — service 是本模块的协作对象
        return
    if real_dim != service.dim:
        raise ApiError(
            500, "STORE_MODEL_MISMATCH",
            f"库 '{service.config.table_name}' 的向量维度是 {service.dim}，"
            f"当前模型 {service.embedder.model_name} 实际输出 {real_dim} 维——不兼容！",
            "换回建库时的模型，或运行 file2kg ingest --force 重建库",
        )
    service._dim_checked = True


def _to_hit(row: dict) -> dict:
    """库行 → SearchHit **白名单投影**（research.md C4）。

    `Store.query()` 返回的是整行，含完整 `vector` 与 `text_hash`。
    这里逐字段写死，就是为了让向量**没有任何机会**进入响应。
    """
    return {
        "chunk_id": row.get("chunk_id"),
        "text": row.get("text"),
        "source": row.get("source"),
        "heading": row.get("heading"),
        "page": row.get("page"),
        "score": row.get("_relevance_score", 0.0),
    }


# ---------- 路由 ----------


def search_documents(service, req: SearchRequest) -> dict:
    """检索核心 —— **HTTP 与 MCP 两个入口共用同一份实现**。

    共用不是为了省几行：两个入口各写一套，迟早会漂成两套打分口径，
    客户端从 MCP 拿到的排序和从 HTTP 拿到的对不上（SC-006 / T024 的"清单恒等"同理）。
    """
    _ensure_store_present(service)

    chunk = Chunk.from_text(req.query, "query")
    service.embedder.embed([chunk])  # 首次调用才加载模型，之后常驻（原则 II + 本功能的核心价值）
    _check_dim(service, len(chunk.embedding))

    try:
        rows = service.store.query(
            chunk.embedding, text=req.query if req.hybrid else None, k=req.k, hybrid=req.hybrid
        )
    except NotImplementedError as e:
        raise ApiError(503, "INDEX_MISSING", "关键词索引缺失，混合检索无法执行", _FTS_HINT) from e
    except ValueError as e:
        # 实测（2026-10-04，lancedb 0.38）：缺 FTS 索引时抛的是 **ValueError**
        # "Cannot perform full text search unless an INVERTED index has been created..."，
        # **不是 NotImplementedError**（cli.py 那条同款捕获也从未生效，已单独修复）。
        # 这里按消息精确识别，不吞掉其它 ValueError。
        msg = str(e)
        if "INVERTED index" in msg or "full text search" in msg:
            raise ApiError(503, "INDEX_MISSING", "关键词索引缺失，混合检索无法执行", _FTS_HINT) from e
        raise

    results = [_to_hit(r) for r in rows]
    body = {
        "results": results,
        "count": len(results),
        "mode": "hybrid" if req.hybrid else "vector",
    }
    if not results:
        body["note"] = "没有命中——库可能为空，或尚未摄取任何文档"
    return body


async def search_endpoint(request) -> JSONResponse:
    """POST /search —— 检索（读操作，恒存在）。"""
    service = request.app.state.service
    req = _parse_search_request(await _read_body(request))
    return JSONResponse(search_documents(service, req))


# ---------- 组装 ----------


def build_routes(service) -> list[Route]:
    """按**能力清单**装配路由——这是"写能力默认不存在"的注册期落点。

    只读模式下 `/ingest` 从不进入这个列表：是"没有"，不是"有但拒绝"。
    US3 / US4 各自往这里加自己的路由。
    """
    routes: list[Route] = [
        Route("/search", search_endpoint, methods=["POST"]),  # US1：读，恒存在
    ]
    # US4: GET /info、US3: POST /ingest（仅写模式）——各自的故事里加入
    return routes


def build_app(service) -> Starlette:
    """父 Starlette app：HTTP 路由 + 挂在 /mcp 的 MCP 子 app。

    MCP 的挂载方式由实测确定（scripts/smoke_fastmcp.py，2026-10-04）：
    - **U1**：必须 `http_app(path="/")` + `Mount("/mcp", ...)`；用 `path="/mcp"` 会叠成
      `/mcp/mcp`，客户端打 `/mcp` 只会拿到 404。
    - **U2**：lifespan **必接**。漏接时 `/mcp` 抛 `RuntimeError`（session manager 的
      task group 未初始化）——官方文档的警告属实。
    """
    register_secret(service.config.api_key)

    routes = build_routes(service)
    lifespan = None
    if service.mcp is not None:
        mcp_app = service.mcp.http_app(path="/")
        routes.append(Mount("/mcp", app=mcp_app))
        lifespan = mcp_app.lifespan

    app = Starlette(
        routes=routes,
        lifespan=lifespan,
        exception_handlers={
            404: _handle_not_found,
            HTTPException: _handle_ipv4,
            ApiError: _handle_api_error,
            Exception: _handle_unexpected,
        },
    )
    app.state.service = service
    return app
