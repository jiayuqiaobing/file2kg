"""serve/http_api.py：HTTP 入口——统一错误契约 +（各故事逐步加入的）路由。

错误契约见 contracts/http-api.md §5：统一错误体 {"error": {code, message, hint}}，
`hint` MUST 可操作——只说"哪里错了"不合格，必须给出下一步。

**密钥擦除**（宪法原则 I / FR-020）：所有面向外部的错误输出都过 `scrub()`，
服务启动时把配置里的密钥登记进擦除表，确保它不会顺着异常文本漏出去。

「可调用性 = 没有；可发现性 = 有帮助」：只读模式下写路由**根本不注册**，
但兜底 404 处理器认得这是一条被关闭的能力，会给出开启方式。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel, ValidationError
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.routing import Route

from ..auditor import Auditor
from ..ingest import ingest as run_ingest_pipeline
from ..types import Chunk

log = logging.getLogger("file2kg.serve")

# 已知但可能被关闭的路径 → 关闭时的提示。**这不等于该能力存在**：
# 路由没注册、不可调用，这里只是让 404 别把用户晾着（US3 场景 1）。
_WRITE_HINT = "以 --allow-write 重启服务可开启（当前为只读模式）"

DISABLED_ROUTE_HINTS: dict[str, tuple[str, str, str]] = {
    "/ingest": ("WRITE_DISABLED", "写能力未启用", _WRITE_HINT),
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


async def _read_body(request, *, allow_empty: bool = False):
    raw = await request.body()
    if allow_empty and not raw.strip():
        return {}  # 参数全可选的端点（/ingest）允许空体，不必逼用户发一个 {}
    try:
        return json.loads(raw)
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


async def info_endpoint(request) -> JSONResponse:
    """GET /info —— 服务自述（读操作，恒存在）。

    **不触发模型加载**：`warm` 由 `Embedder.is_loaded` 回答，服务不自持标志位
    （research.md C5）——所以查自述永远是廉价的。
    """
    return JSONResponse(request.app.state.service.descriptor())


# ---------- 摄取（写，仅写模式注册） ----------


class IngestRequest(BaseModel):
    """摄取请求（contracts/http-api.md §4）。字段全可选。"""

    docs_dir: str | None = None
    force: bool = False


def _parse_ingest_request(payload) -> IngestRequest:
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ApiError(400, "INVALID_REQUEST", "请求体必须是 JSON 对象", '形如 {"docs_dir": "..."}')
    try:
        return IngestRequest.model_validate(payload)
    except ValidationError as e:
        first = e.errors()[0]
        raise ApiError(
            400, "INVALID_REQUEST", f"参数校验失败：{first.get('msg')}",
            "docs_dir 为字符串（可省略，用服务启动时的默认目录）；force 为布尔",
        ) from e


def run_ingest_job(service, docs_dir: str | None = None, force: bool = False) -> dict:
    """执行一次摄取作业 —— **HTTP 与 MCP 两个入口共用同一份实现**。

    与 CLI 入口走同一条管道、写同一套审计：**不新开任何写入路径**，
    全部复用既有的单点组件（宪法原则 III / FR-012）。
    """
    if not service.allow_write:
        # 防御性兜底：正常路径下写模式下才会注册本能力，路由/工具根本不存在
        raise ApiError(404, "WRITE_DISABLED", "写能力未启用", _WRITE_HINT)

    if not service.acquire_write():
        raise ApiError(
            409, "WRITE_IN_PROGRESS", "已有摄取作业在运行",
            "等当前作业结束后重试（同一时刻至多一个作业）",
        )
    try:
        report, events = run_ingest_pipeline(
            docs_dir=str(docs_dir or service.config.docs_dir),
            db_dir=service.config.db_dir,
            table_name=service.config.table_name,
            force=force,
            embedder=service.embedder,  # 复用常驻模型：不许出现第二份模型副本（research.md C1）
        )
        # 审计走既有 Auditor——与 CLI 同一套纪律（只追加、单写者、不静默）
        auditor = Auditor(service.config.audit_dir, report.job_id)
        audit_path = str(auditor.path)
        with auditor:
            for event in events:
                auditor.log(event)
            auditor.close(report)
    except ApiError:
        raise
    except Exception as e:  # 目录不存在 / 库不可写 等运行期失败
        raise ApiError(
            500, "INGEST_FAILED", f"摄取失败：{type(e).__name__}: {e}",
            "检查 docs_dir 是否存在、库目录是否可写；细节见服务日志与审计",
        ) from e
    finally:
        service.release_write()

    return {
        "job_id": report.job_id,
        "files_total": report.files_total,
        "ingested": report.ingested,
        "dupes": report.dupes,
        "skipped": report.skipped,
        "failed": report.failed,
        "chunk_count": len(report.chunks),
        "audit_path": audit_path,
        # 这两个字段是宪法原则 IV 在服务层的落点：缺了它们，服务就会"看起来成功"
        "skipped_files": report.skipped_files,
        "errors": report.errors,
    }


async def ingest_endpoint(request) -> JSONResponse:
    """POST /ingest —— 增量摄取（写操作，**仅写模式注册**）。"""
    service = request.app.state.service
    req = _parse_ingest_request(await _read_body(request, allow_empty=True))
    return JSONResponse(run_ingest_job(service, req.docs_dir, req.force))


# ---------- 组装 ----------


def build_routes(service) -> list[Route]:
    """按**能力清单**装配路由——这是"写能力默认不存在"的注册期落点。

    只读模式下 `/ingest` 从不进入这个列表：是"没有"，不是"有但拒绝"。
    US3 / US4 各自往这里加自己的路由。
    """
    routes: list[Route] = [
        Route("/search", search_endpoint, methods=["POST"]),  # US1：读，恒存在
        Route("/info", info_endpoint, methods=["GET"]),  # US4：读，恒存在
    ]
    if service.allow_write:
        # 只读模式下这一行**从不执行**：/ingest 根本不在路由表里，
        # 客户端拿到的是 404 而不是 403（"没有这条路"，不是"有但拒绝"）
        routes.append(Route("/ingest", ingest_endpoint, methods=["POST"]))
    return routes


def build_app(service) -> Starlette:
    """父 Starlette app：HTTP 路由 + 并入的 MCP 路由。

    MCP 的接入方式由**两轮实测**确定（scripts/smoke_fastmcp.py + 真机联调）：
    - **U1（首轮）**：`http_app(path="/")` + `Mount("/mcp", ...)` 不会叠成 `/mcp/mcp`。
    - **修正（真机联调）**：Mount 会让**规范 URL `/mcp` 先吃一个 307** 跳到 `/mcp/`。
      Claude Code 的客户端会跟随，但 urllib 一类不跟随 POST 307 的客户端**直接失败**。
      故改为**直接并入子 app 的路由**：`/mcp` 直连 200（代价是 `/mcp/` 变成 307，
      而那是没人用的路径）。见 tests/unit/test_serve_mcp.py 的 redirect 断言。
    - **U2（两轮都成立）**：lifespan **必接**。漏接时 `/mcp` 抛 `RuntimeError`
      （session manager 的 task group 未初始化）——并入路由也**不会**自动接上它。
    """
    register_secret(service.config.api_key)

    routes = build_routes(service)
    lifespan = None
    if service.mcp is not None:
        mcp_app = service.mcp.http_app(path="/mcp")
        routes.extend(mcp_app.routes)  # 并入而非 Mount：见 docstring 的修正说明
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
