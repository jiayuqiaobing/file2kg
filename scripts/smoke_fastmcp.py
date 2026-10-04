"""fastmcp 冒烟：写 serve/ 之前先实测——research.md §4 的 U1–U6 全是文档级结论。

两份调研报告都**没有实际运行过 fastmcp**，所以 serve/app.py 的一行代码都不该先写。
本脚本把 U1–U6 逐条跑成事实，结论回填到 docstring 末尾，再据此改文档/写代码。

覆盖（都是动 serve/app.py 前必须实测的路径）：
U1 http_app(path=...) + Mount 的路径组合（会不会叠成 /mcp/mcp）
U2 父 app 与 MCP 子 app 的 lifespan 接线（官方警告"漏接则 /mcp 首次请求 500"——真的吗）
U3 await mcp.list_tools() 的返回形状（list 还是 dict；怎么取工具名）
U4 Starlette 1.x 的 404 处理器能否产出结构化响应体（WRITE_DISABLED 契约的落点）
U5 HTTP 测试客户端形态（TestClient 在 httpx2 环境下能否用）
U6 默认本地路径是否触碰系统钥匙串 / 发起外网连接（宪法原则 I）

实测结论（2026-10-04，fastmcp 4.0.10 / starlette 1.7.0 / uvicorn 0.54.0 / mcp 2.3.0）：
U1 http_app(path="/") + Mount("/mcp") → 客户端打 /mcp，200。
   反直觉处：path="/mcp" + Mount("/mcp") 会叠成 **/mcp/mcp**（打 /mcp 得 404）——
   serve/app.py 必须用 path="/"，不是 path="/mcp"。
U2 **lifespan 必接**：漏接时 POST /mcp 抛 RuntimeError（StreamableHTTPSessionManager
   task group was not initialized）；接上（lifespan=sub.lifespan）后 200。官方警告属实。
U3 await mcp.list_tools() → **list**（不是 dict），取名字用 t.name。
U4 Starlette 1.x 下 {404: h} 与 {HTTPException: h} **都能**产出结构化响应体，任选其一
   （WRITE_DISABLED 契约因此可实现）。
U5 starlette.testclient.TestClient **可用**——但它依赖 httpx，而 httpx 在本项目只是
   huggingface-hub 的**传递依赖**，未显式声明。建议把 httpx 写进 dev extra，勿靠传递依赖。
U6 用 U1 的正确路径跑通 initialize(200) + list_tools 全程：**未导入任何 keyring/truststore
   模块、未发起任何外网连接**。宪法原则 I 的"默认零联网/零凭据"在 fastmcp 侧成立。

反直觉处复现要点：第一版探针用了 path="/mcp" + Mount("/mcp")，结果 U2/U6 双双 404，
"lifespan 无关紧要"与"无外网"的假象同时出现——**路径错了，后面的探针全是空转**。
"""

import asyncio
import socket
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fastmcp import FastMCP  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402
from starlette.routing import Mount, Route  # noqa: E402

# 最小 MCP initialize 请求（streamable-http）
INIT_BODY = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "smoke", "version": "0"},
    },
}
INIT_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}

RESULTS: dict[str, str] = {}


def sep(title: str) -> None:
    print(f"\n{'=' * 62}\n{title}\n{'=' * 62}")


def dump_routes(routes, indent: int = 0) -> list[str]:
    """把 Starlette 路由树摊平成 ("类型 路径") 文本行。"""
    out = []
    for r in routes:
        path = getattr(r, "path", None) or getattr(r, "path_format", "?")
        out.append("  " * indent + f"{type(r).__name__:12} {path}")
        sub = getattr(r, "routes", None)
        if sub:
            out.extend(dump_routes(sub, indent + 1))
    return out


def make_server(name: str = "smoke") -> FastMCP:
    mcp = FastMCP(name)

    @mcp.tool
    def echo(text: str) -> str:
        """原样回显。"""
        return text

    return mcp


# ---------------------------------------------------------------- U5 先跑

def u5_test_client() -> bool:
    """U5 最先跑：U1/U2/U4 都想知道能不能用内存客户端。"""
    sep("U5 HTTP 测试客户端形态")
    ok = False
    try:
        import httpx  # noqa: PLC0415
        print("[U5] httpx  版本:", httpx.__version__)
    except Exception as e:  # noqa: BLE001
        print("[U5] httpx 不可用:", e)

    try:
        import httpx2  # noqa: PLC0415
        print("[U5] httpx2 版本:", getattr(httpx2, "__version__", "?"))
    except Exception as e:  # noqa: BLE001
        print("[U5] httpx2 不可用:", e)

    try:
        from starlette.testclient import TestClient  # noqa: PLC0415

        async def homepage(request):  # noqa: ARG001
            return JSONResponse({"ok": True})

        app = Starlette(routes=[Route("/", homepage)])
        with TestClient(app) as c:
            r = c.get("/")
        print("[U5] TestClient 最小请求:", r.status_code, r.json())
        ok = r.status_code == 200
    except Exception as e:  # noqa: BLE001
        print("[U5] TestClient 不可用:", type(e).__name__, e)
        print("     备选：httpx2 的 ASGITransport，或直连真端口")

    print("[U5] 结论:", "TestClient 可用" if ok else "TestClient 不可用，需备选方案")
    RESULTS["U5"] = "TestClient 可用" if ok else "TestClient 不可用"
    return ok


def try_testclient():
    from starlette.testclient import TestClient  # noqa: PLC0415

    return TestClient


# ---------------------------------------------------------------- U1

def build_parent(mcp: FastMCP, sub_path: str = "/", mount_at: str = "/mcp",
                 wire_lifespan: bool = True):
    """按 serve/app.py 计划的样子组装父 app。sub_path="/" + Mount("/mcp") 是 U1 实测结论。"""
    sub = mcp.http_app(path=sub_path)
    kw = {"lifespan": sub.lifespan} if wire_lifespan else {}
    return Starlette(routes=[Mount(mount_at, app=sub)], **kw), sub


def u1_path_combination(has_client: bool) -> None:
    sep("U1 http_app(path=...) + Mount 的路径组合")
    TestClient = try_testclient() if has_client else None
    if not has_client:
        print("[U1] 无 TestClient，仅打印路由树")

    combos = [("/", "/mcp"), ("/mcp", "/mcp"), ("/mcp", "/mcp-server")]
    found = None
    for sub_path, mount_at in combos:
        parent, _sub = build_parent(make_server("u1"), sub_path, mount_at, wire_lifespan=True)
        print(f"\n[U1] http_app(path={sub_path!r}) + Mount({mount_at!r}) + lifespan 已接:")
        for line in dump_routes(parent.routes):
            print("     ", line)
        if not has_client:
            continue
        with TestClient(parent) as c:
            for client_path in ("/mcp", "/mcp/mcp", "/mcp-server/mcp"):
                try:
                    r = c.post(client_path, json=INIT_BODY, headers=INIT_HEADERS)
                    mark = "  <== 可用" if r.status_code == 200 else ""
                    print(f"     POST {client_path:18} -> {r.status_code}{mark}")
                    if r.status_code == 200 and found is None:
                        found = (sub_path, mount_at, client_path)
                except Exception as e:  # noqa: BLE001
                    print(f"     POST {client_path:18} -> 异常 {type(e).__name__}")

    if found:
        print(f"\n[U1] 结论: http_app(path={found[0]!r}) + Mount({found[1]!r}) → 客户端打 {found[2]!r}")
        RESULTS["U1"] = f"http_app(path={found[0]!r}) + Mount({found[1]!r}) 可用，客户端路径 {found[2]!r}"
    else:
        RESULTS["U1"] = "未找到可用组合（看上面路由树）"


# ---------------------------------------------------------------- U2

def u2_lifespan(has_client: bool) -> None:
    sep("U2 父 app 与 MCP 子 app 的 lifespan 接线")
    if not has_client:
        print("[U2] 无 TestClient，跳过（需真端口才能验）")
        RESULTS["U2"] = "跳过"
        return

    TestClient = try_testclient()
    _parent, sub = build_parent(make_server("u2"))
    print("[U2] mcp.http_app() 返回类型:", type(sub).__name__)
    print("[U2] 有 .lifespan 属性:", hasattr(sub, "lifespan"))

    results: dict[bool, str] = {}
    for wire in (False, True):
        label = "接上" if wire else "漏接"
        parent, _ = build_parent(make_server("u2"), wire_lifespan=wire)
        try:
            with TestClient(parent) as c:
                r = c.post("/mcp", json=INIT_BODY, headers=INIT_HEADERS)
            results[wire] = str(r.status_code)
            print(f"[U2] {label} lifespan -> POST /mcp = {r.status_code}")
        except Exception as e:  # noqa: BLE001
            results[wire] = type(e).__name__
            print(f"[U2] {label} lifespan -> 异常 {type(e).__name__}: {str(e)[:90]}")

    if results.get(True) == "200" and results.get(False) != "200":
        RESULTS["U2"] = f"lifespan 必须接：漏接={results.get(False)}，接上=200"
    else:
        RESULTS["U2"] = f"漏接={results.get(False)}, 接上={results.get(True)}"
    print("[U2] 结论:", RESULTS["U2"])


# ---------------------------------------------------------------- U3

def u3_list_tools() -> None:
    sep("U3 await mcp.list_tools() 的返回形状")
    mcp = make_server("u3")
    try:
        tools = asyncio.run(mcp.list_tools())
        print("[U3] 返回类型:", type(tools).__name__)
        print("[U3] 是否 list:", isinstance(tools, list))
        names = [t.name for t in tools]
        print("[U3] 工具名:", names)
        print("[U3] 按名字找:", [t.name for t in tools if t.name == "echo"])
        try:
            mcp.add_tool  # noqa: B018
        except AttributeError:
            pass
        RESULTS["U3"] = f"list_tools() -> {type(tools).__name__}，取名字用 t.name"
    except Exception as e:  # noqa: BLE001
        print("[U3] 失败:", type(e).__name__, e)
        traceback.print_exc()
        RESULTS["U3"] = f"失败: {type(e).__name__}"


# ---------------------------------------------------------------- U4

def u4_404_handler(has_client: bool) -> None:
    sep("U4 Starlette 1.x 的 404 处理器能否产出结构化响应体")
    if not has_client:
        print("[U4] 无 TestClient，跳过")
        RESULTS["U4"] = "跳过"
        return

    TestClient = try_testclient()

    async def homepage(request):  # noqa: ARG001
        return JSONResponse({"ok": True})

    def notfound(request, exc):  # noqa: ARG001
        return JSONResponse(
            {"error": {"code": "WRITE_DISABLED", "message": "写能力未启用",
                       "hint": "以 --allow-write 重启服务可开启"}},
            status_code=404,
        )

    from starlette.exceptions import HTTPException  # noqa: PLC0415

    for label, handlers in [
        ("{404: h}", {404: notfound}),
        ("{HTTPException: h}", {HTTPException: notfound}),
    ]:
        try:
            app = Starlette(routes=[Route("/", homepage)], exception_handlers=handlers)
            with TestClient(app) as c:
                r = c.get("/nope")
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text[:60]
            print(f"[U4] handlers={label:22} -> {r.status_code}  {body}")
        except Exception as e:  # noqa: BLE001
            print(f"[U4] handlers={label:22} -> 异常 {type(e).__name__}: {e}")

    RESULTS["U4"] = "见上方两行对比（哪一行是结构化 JSON 就用哪个）"


# ---------------------------------------------------------------- U6

def u6_no_side_channels(has_client: bool) -> None:
    sep("U6 默认路径是否触碰钥匙串 / 外网连接（宪法原则 I）")
    before_mods = set(sys.modules)
    real_create = socket.create_connection
    blocked: list[str] = []

    def guarded(address, *a, **k):
        host = address[0] if isinstance(address, tuple) else str(address)
        if host not in ("127.0.0.1", "localhost", "::1", "0.0.0.0"):
            blocked.append(host)
            raise RuntimeError(f"默认路径尝试外网连接: {host}")
        return real_create(address, *a, **k)

    socket.create_connection = guarded
    err = None
    try:
        mcp = make_server("u6")
        parent, _sub = build_parent(mcp)  # 用 U1 实测出的正确组合
        if has_client:
            TestClient = try_testclient()
            with TestClient(parent) as c:
                r = c.post("/mcp", json=INIT_BODY, headers=INIT_HEADERS)
            print("[U6] initialize 请求状态码:", r.status_code)
        asyncio.run(mcp.list_tools())
        print("[U6] 建服务 + initialize + 列工具 全程完成，无外网拦截触发")
    except Exception as e:  # noqa: BLE001
        err = e
        print("[U6] 过程中异常:", type(e).__name__, e)
    finally:
        socket.create_connection = real_create

    new_mods = sorted(
        m for m in (set(sys.modules) - before_mods)
        if "keyring" in m.lower() or "truststore" in m.lower()
    )
    print("[U6] 被拦截的外网连接:", blocked or "无")
    print("[U6] 过程中新导入的 keyring/truststore 模块:", new_mods or "无")

    if not blocked and not new_mods and err is None:
        RESULTS["U6"] = "默认路径未触碰钥匙串、未发起外网连接 ✓"
    else:
        RESULTS["U6"] = f"外网={blocked or '无'}, keyring模块={new_mods or '无'}, err={err}"


# ---------------------------------------------------------------- main

def main() -> None:
    print("fastmcp 冒烟 —— 版本探针")
    try:
        import importlib.metadata as md  # noqa: PLC0415
        for p in ("fastmcp", "fastmcp-slim", "starlette", "uvicorn", "mcp"):
            try:
                print(f"   {p:14} {md.version(p)}")
            except Exception:  # noqa: BLE001, S110
                print(f"   {p:14} ?")
    except Exception as e:  # noqa: BLE001
        print("   版本读取失败:", e)

    has_client = u5_test_client()

    for fn in (u1_path_combination, u2_lifespan, u4_404_handler, u6_no_side_channels):
        try:
            fn(has_client)
        except Exception as e:  # noqa: BLE001
            print(f"\n[{fn.__name__}] 整体失败:", type(e).__name__, e)
            traceback.print_exc()

    try:
        u3_list_tools()
    except Exception as e:  # noqa: BLE001
        print("[U3] 整体失败:", type(e).__name__, e)

    sep("结论汇总（回填 docstring）")
    for k in sorted(RESULTS):
        print(f"{k}: {RESULTS[k]}")
    print("\n冒烟跑完 ✓（逐条核对上方输出，把结论回填 docstring 后再动 serve/）")


if __name__ == "__main__":
    main()
