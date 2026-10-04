# Research: serve 常驻服务

**Feature**: `001-serve-api-mcp` | **Date**: 2026-10-04 | **Spec**: [spec.md](./spec.md)

本文件记录 Phase 0 的选型决策。分两类：**外部调研**（FastMCP / HTTP 层现状）与
**代码考古**（v0.1 既有实现暴露的硬约束）。

> **可信度声明**：外部调研基于官方文档与 PyPI 元数据（2026-10-04 取），**均未在本机实跑**。
> 按宪法「实测优先于文档」，所有涉及 FastMCP/Starlette 运行时行为的结论，
> 实现阶段 MUST 先用 `scripts/smoke_*.py` 实测确认（见 §4）。

---

## 1. 外部调研结论

### D1. MCP 框架：FastMCP，钉 `>=4.0,<5`

**Decision**: 使用 FastMCP v4 线，`pyproject.toml` 声明 `fastmcp>=4.0,<5`。

**Rationale**:
- 蓝图既定（`file2kg-README.md`：CLI / HTTP API / MCP 三入口），用户技术指引 #2 明确指定
- 许可证 **Apache-2.0**，可商用，满足宪法「新增依赖 MUST 具备可商用许可证」
- requires-python `>=3.10`，与项目一致（实际目标 3.11+，项目环境 3.11.16）

**Alternatives considered**: 直接用官方 `mcp` SDK —— 需要自己实现工具注册、传输、会话管理
的胶水层，与「薄壳」指引和 YAGNI 冲突。FastMCP 是官方 SDK 之上的既有封装，已被广泛使用。

**必须知道的三条版本事实**（网上多数教程已过时）:

| 旧（v2/早期 v3，搜索到的多是这个） | 当前 v4.0.10 |
|---|---|
| 仓库 `jlowin/fastmcp` | **`PrefectHQ/fastmcp`** |
| `@mcp.tool(enabled=False)` | **已移除**——改为「根本不注册」或 `mcp.disable()` |
| `await server.get_tools()` → dict | **`await server.list_tools()` → list** |
| `FastMCP(host=..., port=...)` | `TypeError`——host/port 传给 `run()` / `http_app()` |
| `from mcp.server.fastmcp import FastMCP` | `from fastmcp import FastMCP` |
| 依赖 `httpx` | 依赖 **`httpx2`** + `httpcore2` |

### D2. HTTP 层：Starlette 纯路由，**不引入 FastAPI**

**Decision**: 用 Starlette `Route` 写 3 个 JSON 端点，把 `mcp.http_app()` 挂成子 app。
`pyproject.toml` 显式声明 `starlette>=1.0.1` 与 `uvicorn>=0.35`（**已在 FastMCP 依赖树内**，
声明而非新增下载）。

**Rationale**:
- **净新增安装包 = 0**。`fastmcp` 的 server extra 强制 `starlette>=1.0.1`、`uvicorn>=0.35`；
  官方 `mcp` SDK 本身也无条件要求 `uvicorn>=0.31.1` + `starlette>=0.27`——三重保证
- 3 个端点的规模下，Starlette 相对 FastAPI 多出的只是一次性约 40 行公共管道
  （JSON 解析 + Pydantic 校验 + 异常处理器）。Pydantic **已随 FastMCP 进树**，校验能力不打折
- 宪法「复杂度必须辩护 / YAGNI 优先」：为 3 个端点引入整套 DI + OpenAPI 体系不划算

**Alternatives considered**:

| 方案 | 净新增包 | 否决理由 |
|---|---|---|
| FastAPI 挂载 | +2（`fastapi`、`annotated-doc`） | 省约 40 行样板，代价是一套框架 + DI/OpenAPI 概念面；不划算 |
| Litestar | +9 | 未到 1.0；且与 Starlette 子 app 的跨框架挂载 + lifespan 合并**无官方文档** |
| Flask | +6 | WSGI，**无法**与 ASGI 的 MCP app 同 app 挂载，退化成"同进程两个端口" |
| stdlib `http.server` | 0 | CPython 文档明确劝退（"not recommended for production"）；无路由、无 404/405、无 JSON 辅助 |

**Starlette 1.0 破坏性变更（必须按新写法写，网上旧例会直接报错）**:
`on_startup` / `on_shutdown` / `on_event` / `add_event_handler` **全部移除**（改用 `lifespan=`）；
`@app.route()` / `@app.exception_handler()` / `@app.middleware()` **装饰器形式移除**
（改用 `routes=` / `exception_handlers=` / `middleware=` 构造参数）。

### D3. 依赖打包：可选 extra `[serve]`（**用户 2026-10-04 拍板**）

**Decision**:

```toml
[project.optional-dependencies]
serve = ["fastmcp>=4.0,<5", "starlette>=1.0.1", "uvicorn>=0.35"]
dev   = ["pytest>=8.0", "file2kg[serve]"]   # 测试需要 serve 依赖
```

`file2kg serve` 内部**延迟导入**；缺依赖时打印一行可操作指引而非 traceback。

**Rationale**:
- 依赖体量**实测两种基准**（2026-10-04）：
  - 全新环境干净解析（`--ignore-installed`）为 **68 个包**——这是最初的估计口径；
  - **本项目现有环境**干跑 `pip install --dry-run -e ".[serve]"` 为**净新增 53 个包**
    （含 `keyring`、`pywin32`、`opentelemetry-api`、`beartype`、`watchfiles` 等），
    且**既有依赖零升级、零降级**——差异来自本环境已装 64 个包，与 fastmcp 依赖树大面积重叠
    （pydantic / rich / httpx / anyio / certifi 等），`httpx2` 也已存在。

  无论按哪种基准，让只用 `ingest`/`query` 的用户背这些包，都与宪法原则 I
  「零设置、装完即用」的轻量承诺有张力
- 延迟导入同时满足宪法原则 II「import 零成本」
- 可逆：若使用数据表明多数用户要 serve，提升为核心依赖只需改一行

**Alternatives considered**: 核心依赖（完整 fastmcp）——零额外步骤，但 68 个包压在主路径上；
核心依赖 + `fastmcp-slim[server]`——能裁掉 client-only 依赖，但测试所需的内存客户端可能
要求额外声明 client extra，两类 extras 的组合关系会随上游漂移而复杂化。

### D4. 进程模型：单进程、单端口、父 Starlette app + MCP 子 app

**Decision**（满足 spec「MCP 与 HTTP 共用同一常驻服务」的假设）:

- 父 Starlette app 承载 3 个 JSON 路由**与**全局 `exception_handlers`
- `mcp.http_app(...)` 挂成子 app，路径 `/mcp`
- **lifespan 必须接线**（用 `fastmcp.utilities.lifespan.combine_lifespans` 或用父 app 的 lifespan）

**Rationale**:
- 同进程 ⇒ HTTP 与 MCP 共享同一个 `Embedder` 实例 ⇒ Agent 走 MCP 也享受模型常驻（US2 场景 5）
- **父 app 级别才有 404 处理器**——这正是 `WRITE_DISABLED` 契约（404 + 开启指引）的落点

**Alternatives considered**:
- `@mcp.custom_route`（FastMCP 自带，零胶水、天然无 lifespan 风险）：**否决**，因为拿不到 app 级
  404 处理器，无法为"访问被关闭的写端点"产出结构化提示，US3 场景 1 无法满足
- 双进程/双端口：**否决**，两个模型实例，直接违背"消除冷启动"

**已知陷阱（官方文档明确警告）**: 父 app **不会**自动执行被挂载子 app 的 lifespan，
漏接会导致 Streamable HTTP 的 session manager 未初始化、`/mcp` 首次请求即 500。
另注意挂载前缀不要叠加成 `/mcp/mcp`。

### D5. MCP 工具的条件注册：不注册，而非禁用

**Decision**: 只读模式下，写工具**从不调用注册**。

**Rationale**: v4 已移除 `enabled=` 参数；FastMCP 官方文档的立场与我们的设计一致——

> *"When something must never be reachable, leave it unregistered or guard it with
> authentication instead of relying on visibility."*

**Alternatives considered**: `mcp.disable(tags={"write"})` —— 属于**可见性**机制，工具仍在
注册表里，语义是"隐藏"而非"不存在"，不满足 FR-008「是"没有"，不是"有但报错"」。

---

## 2. 代码考古：v0.1 实现暴露的硬约束

这五条不是外部资料，是**读现有源码**得出的——它们直接决定实现形状，且都与「薄壳」指引冲突。

### C1. `ingest()` 内部自建 Embedder（必须改）

`ingest.py:104`：

```python
embedder = Embedder(model=model, api_key=api_key, api_url=api_url)
```

服务若直接调用 `ingest()`，会得到**第二个 Embedder 实例**——内存里两份模型（639MB×2），
且"模型常驻"名不副实。

**Decision**: 给 `ingest()` 增加**可选** `embedder` 注入参数（缺省 `None` ⇒ 保持现有行为，
构造自建实例）。

**Rationale**: 这是本功能**唯一必须触碰核心模块**的地方。改动是纯增量的（2-3 行），
缺省路径行为完全不变，既有测试不受影响。

**Alternatives considered**:
- 服务自己重写状态机 —— **否决**，直接违反用户指引 #1「禁止复制或重写核心逻辑」
- 不注入，接受第二个实例 —— **否决**，违背本功能的核心承诺（模型常驻）
- 只在写模式下让 ingest 自建 —— 半吊子方案：查询用常驻模型、摄取用第二个实例，
  内存峰值依然翻倍，且"服务持有哪个模型"变得含糊

### C2. `Store.__init__` 会建表（必须预检）

`store.py:87`：表不存在时 `create_table(...)`。服务启动探测库若直接 `Store(...)`，
会**静默建一个空库**——违反 spec 假设「库需先存在」。

**Decision**: 启动探测照抄 `cli.py:124-129` 的既有写法（先 `Path(db).exists()`，
再 `lancedb.connect(db).open_table(table)` 包在 `try/except ValueError` 里）。

### C3. `dim=None` 路径**不验证调用方模型**（一库一模的关键）

`store.py:77-80`：`dim is None` 时从**库元数据**读出维度，交给 `_assert_dim` 与
**库 schema 的列宽**比对。也就是说它检查的是「元数据与 schema 是否自洽」，
**从不检查调用方用的是不是同一个模型**。

**Decision**: 一库一模校验分层：
- **启动时（不加载模型）**：比对「库元数据里的模型名」vs「服务配置的模型名」，不符即拒启
- **首次真正嵌入时（模型已加载）**：拿到真实维度，走 `Store` 既有的 `_assert_dim`

**Rationale**: 这样既能在默认（不加载模型、`warm=false`）状态下完成 FR-014 的启动校验，
又不丢掉维度这一层防线。**服务 MUST NOT 依赖 `dim=None` 路径来证明一库一模。**

### C4. `Store.query()` 返回整行，含完整向量

`store.py:156` 返回 `.to_list()`——每行都带 `vector` 列（1024 维浮点）。
CLI 只挑三列渲染，所以问题被掩盖了；HTTP API 若直接透传，每个响应都会拖一条向量。

**Decision**: 投影为 `SearchHit`（剔除 `vector` 与 `text_hash`）。见 data-model.md §2.3。

### C5. `Embedder` 不暴露加载状态

`embedder.py:50` 的 `self._backend = None` 是私有的懒加载哨兵。FR-015 要求 `/info` 报告
`warm` 状态。

**Decision**: 给 `Embedder` 增加**只读访问器**（如 `is_loaded` 属性）。
服务**不得**自持一份 `warm` 标志位。

**Rationale**: 这一条同时是 Constitution Check ② 的支点——如果服务自己记 `warm`，
它可以撒谎；由 `Embedder` 的真实状态回答，"预热非默认"才是可证伪的。

---

## 3. 其他决策

### D6. hybrid 缺 FTS 索引：报错，不静默降级

**Decision**: 返回 503 `INDEX_MISSING` + "跑一次 ingest 建索引"的指引；
调用方若接受纯向量，须**显式**传 `hybrid: false`。

**Rationale**: 与 CLI 现有行为一致（`cli.py:135-136`）。宪法原则 IV：宁可暴露缺陷，
绝不静默。若静默降级，调用方会以为拿到的是混合检索结果。

### D7. 写操作串行化

**Decision**: 服务内同一时刻至多一个摄取作业；并发写请求返回 409 `WRITE_IN_PROGRESS`。

**Rationale**: `auditor.py` 的设计前提是**单写者 + 只追加**。两个并发作业写同一审计文件
会破坏这个前提（宪法原则 IV）。

### D8. 测试不引入 `pytest-asyncio`

**Decision**: 需要 `await` 的测试用 `asyncio.run(scenario())` 包在同步测试里。

**Rationale**: 宪法「复杂度必须辩护 / 能不加就不加」。`asyncio.run` 零新增依赖即可覆盖
`await mcp.list_tools()` 这类内省调用。若将来异步测试规模变大再引入。

---

## 4. 未验证项 —— 已由 T004/T005 实测结清

按宪法「实测优先于文档」，以及项目既有的**模块站点流程**（`README.md`：动核心模块前先写
`scripts/smoke_*.py` 实测第三方行为），以下各项在写 `serve/` 之前先跑
`scripts/smoke_fastmcp.py` 实测。

**实测结论（2026-10-04，fastmcp 4.0.10 / starlette 1.7.0 / uvicorn 0.54.0 / mcp 2.3.0）：**

| # | 结论 |
|---|------|
| U1 | ✅ `http_app(path="/")` + `Mount("/mcp")` → 客户端打 `/mcp` 得 200。**反直觉处**：`path="/mcp"` + `Mount("/mcp")` 叠加成 **`/mcp/mcp`**，打 `/mcp` 得 404 |
| U2 | ✅ **lifespan 必接**：漏接时 POST `/mcp` 抛 `RuntimeError`（session manager task group 未初始化），接上 `lifespan=sub.lifespan` 后 200。官方警告属实 |
| U3 | ✅ `await mcp.list_tools()` → **list**（不是 dict），取名字用 `t.name` |
| U4 | ✅ `{404: h}` 与 `{HTTPException: h}` **都能**产出结构化响应体；`WRITE_DISABLED` 契约可实现 |
| U5 | ⚠️ `TestClient` **可用**——但它依赖 `httpx`，而 `httpx` 在本项目只是 `huggingface-hub` 的**传递依赖**，未显式声明。**应写进 `dev` extra，勿靠传递依赖** |
| U6 | ✅ 用 U1 的正确路径跑通 initialize(200) + list_tools：**未导入任何 keyring/truststore 模块、未发起任何外网连接**。宪法原则 I 在 fastmcp 侧成立 |

> **一条方法论教训**：第一版探针用了错误的路径组合（`path="/mcp"` + `Mount("/mcp")`），
> 导致 U2 与 U6 双双 404——"lifespan 无关紧要"与"没有外网访问"两个假象**同时**出现。
> 路径错了，后面的探针全是空转。这正是"先实测、且要验到点子上"的价值。

**原始疑虑清单（保留供对照）：**

| # | 未验证项 | 为什么重要 | 验证方式 |
|---|----------|-----------|----------|
| U1 | `http_app(path=...)` 与 `Mount("/mcp", ...)` 的**路径组合** | 前缀叠加会得到 `/mcp/mcp`，MCP 客户端全挂 | smoke：起服务、列出工具 |
| U2 | 父 app 与 MCP 子 app 的 **lifespan 接线** | 官方文档明确警告漏接则 `/mcp` 首次请求 500 | smoke：起服务后立刻打 `/mcp` |
| U3 | `await mcp.list_tools()` 的**返回形状** | Constitution Check ① 的测试直接依赖它 | smoke：断言工具名集合 |
| U4 | Starlette 1.x 下 **404 处理器**能否产出结构化体 | `WRITE_DISABLED` 契约的落点 | smoke：请求未注册路由，看响应体 |
| U5 | **HTTP 测试客户端**用什么 | 依赖树里是 `httpx2` 而非 `httpx`，Starlette `TestClient` 是否可用**未知** | smoke：试 `TestClient` 与 `httpx2` ASGITransport；都不行则用 stdlib `urllib` 对真端口（仅集成测试） |
| U6 | 新增依赖是否会引入**联网/凭据**默认路径 | 宪法原则 I：「MUST NOT 引入必须联网或注册才能工作的默认路径」。新依赖含 `keyring`（系统凭据库）与 `truststore`——**默认本地路径 MUST NOT 触碰系统钥匙串** | smoke：断网冷启动服务 + 断言未访问 OS keyring |

**U5 尤其关键**：若 `TestClient` 不能用且不想引入 `httpx`，HTTP 层单元测试的形态要变
（直接调用 async 路由处理函数 + 少量真端口集成测试）。这属于"动第三方核心行为前先实测"的
典型场景，**必须在写第一个路由之前解决**。

---

## 5. 对实现顺序的影响

```text
0. scripts/smoke_fastmcp.py     ← 先解决 U1–U6，否则后面全建在沙子上
1. Embedder.is_loaded + ingest(embedder=)    ← 两处核心最小改动
2. serve/service.py（能力清单、分层校验）
3. serve/http_api.py + serve/mcp_tools.py
4. serve/app.py 组装 + cli.py serve 命令
5. pyproject [serve] extra + 测试
```

第 0 步不是可选项——项目自己的 README 就把"先实测再写码"列为纪律，且本次调研的两份报告
**都没有实际运行过 fastmcp**。
