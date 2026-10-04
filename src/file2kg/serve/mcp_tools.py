"""serve/mcp_tools.py：MCP 工具定义与**条件注册**。

宪法原则 III 在 MCP 侧的落点：写工具在只读模式下**从不调用注册**。
FastMCP v4 已移除 `enabled=` 参数，官方文档的立场与我们的设计一致：

    "When something must never be reachable, leave it unregistered or guard it
     with authentication instead of relying on visibility."

即：不是"注册了但隐藏"，是**根本不存在**。客户端（Agent）看不到它，
也就不存在"Agent 误写库"这条路径。

检索复用 `http_api.search_documents`——两个入口同一份实现，不引入第二套打分口径。
"""

from __future__ import annotations

from fastmcp import FastMCP

from .http_api import ApiError, _parse_search_request, run_ingest_job, search_documents


def _as_tool_error(e: ApiError) -> ValueError:
    """把带 hint 的服务错误转成工具错误。

    FastMCP 会把工具抛出的异常原样传给客户端（实测：`ToolError`，
    消息形如 "Error calling tool 'search': <原文>"），所以**把 hint 拼进去**——
    Agent 同样需要"下一步该干什么"，不是只被告知"错了"（FR-016 的精神）。
    """
    return ValueError(f"{e.message}；{e.hint}")


def build_mcp(service) -> FastMCP:
    """按能力组装 MCP 服务。只读模式下写工具**从不出现**。"""
    mcp = FastMCP("file2kg")

    @mcp.tool
    def service_info() -> dict:
        """查询当前知识库服务的自述信息：所服务的库、绑定的嵌入模型与向量维度、
        权限模式（只读/可写）、模型是否已加载常驻。不触发模型加载，可随时安全调用。"""
        return service.descriptor()

    @mcp.tool
    def search(query: str, k: int = 10, hybrid: bool = True) -> dict:
        """在本地知识库中检索相关文档片段。返回带来源文件、标题层级与页码的片段，
        可直接用于引用。使用混合检索（向量 + 关键词），对术语精确匹配（端口号、
        接口名）同样有效。k 为返回条数（1..50，默认 10）；hybrid=false 时退化为纯向量检索。"""
        try:
            req = _parse_search_request({"query": query, "k": k, "hybrid": hybrid})
        except ApiError as e:
            raise _as_tool_error(e) from e
        try:
            return search_documents(service, req)
        except ApiError as e:
            raise _as_tool_error(e) from e

    if service.allow_write:

        @mcp.tool
        def ingest(docs_dir: str | None = None, force: bool = False) -> dict:
            """对指定文档目录执行一次**增量**摄取：只处理新增与变化的文件，并清理已删除
            文件对应的旧内容。每次调用产生一份作业审计。耗时取决于变更量，可能较长。
            docs_dir 省略时用服务启动时的默认目录；force=true 忽略增量状态、全量重挂。"""
            try:
                return run_ingest_job(service, docs_dir, force)
            except ApiError as e:
                raise _as_tool_error(e) from e

    # 只读模式下上面这个工具**从不被注册**——能力不存在，而非存在但拒绝。

    return mcp
