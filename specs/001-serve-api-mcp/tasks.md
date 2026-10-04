---

description: "Task list for serve 常驻服务（HTTP API + MCP）"

---

# Tasks: serve 常驻服务（HTTP API + MCP）

**Input**: Design documents from `/specs/001-serve-api-mcp/`

**Prerequisites**: [plan.md](./plan.md)（必需）、[spec.md](./spec.md)（必需）、[research.md](./research.md)、[data-model.md](./data-model.md)、[contracts/](./contracts/)、[quickstart.md](./quickstart.md)

**Tests**: 本清单**包含测试任务**——项目宪法「开发工作流与质量门」规定
「每个模块完成时 MUST 同时交付其测试」，测试不是可选项。

**Organization**: 按用户故事分阶段，每个故事可独立实现与验证。

---

## ⚠️ 三条硬约束（执行前必读）

> 以下三条由用户明确指定，优先级高于本清单其余任何安排。

### 约束 1：第 0 个实质性任务是 smoke 实测

`scripts/smoke_fastmcp.py`（T004）是本功能**第 0 个实质性产物**，**阻塞所有后续任务**。
它的存在理由：本次调研的两份报告**都没有实际运行过 fastmcp**，`research.md` §4 列出的
U1–U6 全部停在文档层面。项目 README 自己的纪律是「动每个核心模块前先验证实测行为」。

### 约束 2：装依赖前必须先干跑报告

**禁止**直接安装。T002 必须先跑 `pip install --dry-run -e ".[serve]"` 并报告会变更/升级哪些包；
**重点核对已被依赖的包**（`numpy`、`torch`、`pyarrow`、`lancedb`、`typer`、`sentence-transformers`）
是否被降级或替换。T003 是独立的人工确认门——**核对通过后才真正安装**。

### 约束 3：🔴 实测与文档冲突时，改文档，不硬写代码

若 T005 的 smoke 结果与 `plan.md` / `research.md` 的假设**不符**：

1. **停下**，不要为了实现而迁就错误假设
2. 回到 `plan.md` / `research.md` **修订文档**，写明实测结论与新的决策
3. 若影响规格（`spec.md`）或契约，一并修订
4. 修订完成后再继续实现

**Why**：本项目的宪法与 README 都把「实测优先于文档」列为纪律。
硬写代码会让文档变成一张谎言——后来的人照着读会踩进同一个坑。
**这条闸门（T006）是硬门，不是提醒。**

---

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行（不同文件、无未完成依赖）
- **[Story]**: 所属用户故事（US1–US4）
- 每个任务都带确切文件路径

## Path Conventions

单项目结构：`src/file2kg/`、`tests/`、`scripts/` 均在仓库根目录（见 plan.md 结构决策）。

---

## Phase 1: Setup（环境与实测前置）

**Purpose**: 把「文档级结论」变成「实测结论」，这是本功能一切后续工作的地基

**⚠️ 本阶段不完成，Phase 2 及以后一律不得开始。**

- [X] T001 在 `pyproject.toml` 增加 `[project.optional-dependencies]` 的 `serve` extra（`fastmcp>=4.0,<5`、`starlette>=1.0.1`、`uvicorn>=0.35`），并让 `dev` extra 引用 `file2kg[serve]`（测试需要）——**必须先于 T002**，否则后续无法用 `-e ".[serve]"` 安装
- [X] T002 在项目环境 `D:/Miniconda3/envs/file2kg-env/python.exe -m pip install --dry-run -e ".[serve]"` 干跑，**逐条报告**会新增/升级/降级/替换的包；**重点核对** `numpy`、`torch`、`pyarrow`、`lancedb`、`typer`、`sentence-transformers`、`openai` 是否被改动 —— 实测：净新增 53 个包，**零升级零降级**，重点包全部保持不动（见下）
- [X] T003 【人工确认门】核对 T002 报告：确认无既有包被降级或替换；**用户确认后**才执行真正的安装（`pip install -e ".[serve]"`）。若报告显示会破坏现有环境 → **停止**，回到 research.md D3 重新评估打包方案 —— 用户已确认；安装成功（退出码 0），`file2kg.__file__` 已指向 D:\ 工作目录，既有依赖零变化
- [X] T004 编写 `scripts/smoke_fastmcp.py`，逐项实测 research.md §4 的 U1–U6：U1 `http_app(path=...)` + `Mount` 的路径组合（不得得到 `/mcp/mcp`）、U2 父 app 与 MCP 子 app 的 lifespan 接线、U3 `await mcp.list_tools()` 的返回形状、U4 Starlette 1.x 的 404 处理器能否产出结构化响应体、U5 HTTP 测试客户端形态（`TestClient` 在 `httpx2` 环境下是否可用）、U6 fastmcp 是否引入联网/凭据默认路径 —— 已写完并在首轮运行中**抓出自有探针缺陷**（U1 未接 lifespan、U2/U6 用了错误的路径组合导致空转），修正后重跑
- [X] T005 运行 `scripts/smoke_fastmcp.py`，把 U1–U6 的**实测结论**（含反直觉处）记入脚本 docstring；若结论与文档假设不同，同步记录差异 —— 结论：U1 `path="/"` + `Mount("/mcp")`（`path="/mcp"` 会叠成 /mcp/mcp）；U2 lifespan 漏接抛 RuntimeError、接上 200；U3 `list_tools()` → list；U4 两种 404 处理器写法都行；U5 TestClient 可用但依赖未声明的 httpx；U6 零 keyring 零外网
- [X] T006 🚦 **【实测闸门】** 对照 T005 结果与 plan.md / research.md：**不符则先修订文档**（plan.md / research.md / 必要时 spec.md + contracts/），修订完成后才进入 Phase 2。**禁止带着已知的错误实现** —— 闸门通过：**无与文档矛盾**的结论，全部是原 U1–U6 疑虑的**解答**；已回填 smoke docstring、更新 research.md §4（结论表 + 教训）、更新 plan.md 的 Testing 行，并据 U5 把 `httpx` 显式加进 `dev` extra

**Checkpoint**: U1–U6 全部有实测结论，文档与实测一致 —— 地基踏实，可以开始搭建

---

## Phase 2: Foundational（阻塞所有用户故事）

**Purpose**: 所有故事共享的骨架——核心模块最小改动 + 服务组装点 + 错误契约管道

**?? CRITICAL**: 本阶段完成前，任何用户故事都不能开始

- [X] T007 [P] 在 `src/file2kg/embedder.py` 增加只读访问器 `is_loaded`（反映 `_backend is not None`，即模型是否已加载常驻）；同步在 `tests/unit/test_embedder.py` 补测试：新建实例为 `False`、`embed()` 后为 `True`——**先写测试确认失败** —— 完成：先红（3 failed，AttributeError）后绿（14 passed）；含配对断言（走真实 `_ensure_backend` 路径后为 True）
- [X] T008 [P] 在 `src/file2kg/ingest.py` 给 `ingest()` 增加**可选** `embedder: Embedder | None = None` 注入参数（`None` 时保持现有行为，构造自建实例）；同步在 `tests/unit/test_ingest.py` 补测试：传入实例时**不产生第二个 Embedder**、缺省时行为不变——**先写测试确认失败** —— 完成：先红（TypeError: unexpected keyword 'embedder'）后绿；配对断言已落（注入者被用 + 缺省仍自建）
- [X] T009 [P] 在 `src/file2kg/config.py` 增加 `ServeConfig` dataclass：`host="127.0.0.1"`、`port=<默认端口>`、`mode="read-only"`、`preload=False`、`docs_dir=<cwd>`；默认值即「最安全」值，与宪法原则 II/III 一致 —— 完成，并新建 `tests/unit/test_config.py`（12 项）：三个默认值分别钉住宪法原则 I/II/III，含显式翻转的配对断言与构造期校验（非法 mode/port 即抛）
- [X] T010 创建 `src/file2kg/serve/__init__.py`（导出 `build_service` / `run`）与 `src/file2kg/serve/app.py` 的 `build_service()` 组装点：库存在性**预检**（照抄 `src/file2kg/cli.py:124-129` 的写法，**不得**让 `Store.__init__` 静默建表，参见 research.md C2）、构造 `Embedder`/`Store`、构造 `CapabilityManifest`（启动后不可变）、返回 Starlette app；**实现预热执行路径**：`preload=True` 时在返回 app 前用一次性 `Chunk` 调一次 `embedder.embed([...])` 触发加载（结果丢弃、不入库、不写审计），`preload=False` 时 MUST NOT 触碰模型。预热**失败**（模型下载失败/无网）MUST 如实报告原因与镜像指引，且**不得**导致启动失败、**不得**报告 `warm=True` —— 完成。附带：一库一模的**模型名比对**在本任务一并实现（它与"用哪个模型装 Embedder"不可分割，故早于 T032 落地）；T032 转为补 `/info` 面的验证与测试
- [X] T011 在 `src/file2kg/serve/http_api.py` 实现错误契约公共管道：统一错误体 `{"error": {code, message, hint}}`（`hint` MUST 可操作）、`exception_handlers` 注册（含 404 兜底，见 contracts/http-api.md §5）、JSON 解析失败的容错；**所有面向外部的错误输出（HTTP 错误体、CLI 错误信息、服务日志）MUST NOT 回显配置中的密钥**（宪法原则 I） —— 完成。密钥擦除（FR-020）在本任务即落地：`register_secret()` + `scrub()`，错误体与日志统一过擦除；`DISABLED_ROUTE_HINTS` 承载"可发现性≠可调用性"（路由由各故事注册）
- [X] T012 在 `tests/unit/test_serve_service.py` 写 `CapabilityManifest` 的**成对断言**：只读模式不含 `ingest` **且** 写模式必须含 `ingest`（plan.md §① 的配对原则，缺配对项即为假证明）——**先写测试确认失败** —— 完成（13 项）：能力清单成对断言 + §② 预热成对断言（默认 False / 查自述不改 / 显式预热 True）+ research.md C2（库不存在**不得静默建表**，含"没被建出来"的反向断言）+ C3（模型不匹配拒开，且拒开时不加载模型）。**路由级的缺席/在场断言归 T025**（此刻路由尚未注册，FTS 断言会空转）

**Checkpoint**: 组装点就绪、能力清单可断言、错误契约可用 —— 用户故事可以开始了

---

## Phase 3: User Story 1 - 常驻检索：一次启动，反复秒回 (Priority: P1) 🎯 MVP

**Goal**: 模型只加载一次即常驻，后续检索亚秒级返回

**Independent Test**: 起服务 → 连续两次检索 → 第二次显著快于第一次且达到亚秒级；`/search` 结果与 CLI 检索语义一致

### Implementation for User Story 1

- [X] T013 [P] [US1] 在 `tests/unit/test_serve_http.py` 写 `/search` 契约测试：请求/响应字段、`k` 越界（1..50）返回 400、空 `query` 返回 400、**响应体 MUST NOT 含 `vector` 与 `text_hash` 字段**（data-model.md §2.3 的硬约束）；另加三条断言：① **未设 `FILE2KG_API_KEY` 时服务正常启动且检索正常**（FR-003 / 宪法原则 I）；② **连发若干畸形/越界请求后服务仍能正常响应**，退出次数为 0（FR-018 / SC-007）；③ **同一库同一查询下，服务检索结果与 `file2kg query` CLI 结果逐条一致**（SC-006）——**先写测试确认失败**
- [X] T014 [US1] 在 `src/file2kg/serve/http_api.py` 实现 `POST /search`：解析校验 → 复用既有 `Embedder`/`Store.query()` → **投影为 `SearchHit`**（剔除 `vector`/`text_hash`，见 research.md C4）；空结果返回 200 + `note`，不是错误
- [X] T015 [US1] 在 `src/file2kg/serve/app.py` 落实**常驻语义**：`Embedder` 实例由 `build_service()` 持有并注入检索路径，进程内只加载一次（`Embedder` 的懒加载哨兵天然保证，不得重复构造）
- [X] T016 [US1] 在 `src/file2kg/serve/http_api.py` 补齐检索路径的**错误分支**：hybrid 缺 FTS 索引 → **503 `INDEX_MISSING`** + "跑一次 ingest 建索引"指引，**MUST NOT 静默降级为纯向量**（research.md D6，`hybrid: false` 是显式逃生口）；运行期库不可用（目录被删除/移动）→ **503 `STORE_UNAVAILABLE`** 且**服务保持存活**（FR-018 / contracts §5）
- [X] T017 [US1] 在 `src/file2kg/cli.py` 增加 `serve` 命令。**选项集**：`--db`、`--table`、`--model`、`--host`（默认 `127.0.0.1`）、`--port`、`--allow-write`（默认关）、`--preload`（默认关）、`--docs-dir`（默认 cwd）——默认值一律取自 `ServeConfig`（T009），CLI 层 MUST NOT 另立默认值。**行为**：① **延迟导入** `serve` 包，缺依赖时打印一行安装指引而非 traceback（宪法原则 II）；② 启动时向 stderr 打印服务自述（库/模型/权限模式/预热状态，US1 场景 1）；③ 密钥**只认环境变量** `FILE2KG_API_KEY`，MUST NOT 提供命令行选项（宪法原则 I）；④ `--host` 指定**非回环地址**时 MUST 打印"知识库已暴露到网络"提示（FR-002）；⑤ **端口被占用时 MUST 明确报错并以退出码 2 结束，MUST NOT 静默换端口**。**接口契约**：stdout = 数据、stderr = UI；退出码 0 = 正常运行至停止 / 1 = 用户输入错（参数非法、库目录不存在）/ 2 = 运行失败（端口被占、库与模型不匹配、缺 serve 依赖）
- [X] T018 [US1] 实现干净退出：Ctrl-C 能优雅停止、释放端口与文件句柄、可立即重启（FR-019 / SC-009）。注意 uvicorn 在 Windows 下信号只在主线程安装，Typer 同步命令跑在主线程故可行（research.md D4 相关调研）
- [X] T019 [US1] 集成验证：真端口起服务，按 quickstart.md 场景 2 确认第二次检索亚秒级；并**按 SC-002 连续发起 100 次检索，断言进程内模型加载次数恒为 1**（不得缩水为 2 次——SC 是承诺，改小它是掩盖而不是解决）

**Checkpoint**: MVP 达成 —— 常驻检索可用且可独立验证

---

## Phase 4: User Story 2 - Agent 通过 MCP 接入，默认只读 (Priority: P2)

**Goal**: 任何 MCP 客户端可接入；默认工具清单**只有读操作**

**Independent Test**: MCP 内存客户端连接 → 工具清单只有 `service_info`/`search` → 调用 `search` 拿到带来源的结果

**依赖**: 需要 US1 的检索路径（`search` 工具复用同一实现）

### Implementation for User Story 2

- [ ] T020 [P] [US2] 在 `tests/unit/test_serve_mcp.py` 写**成对**工具清单断言（用 `asyncio.run()` 包同步测试，不引入 pytest-asyncio，见 research.md D8）：只读模式工具名集合 == `{"service_info","search"}` **且** 写模式必须含 `ingest`——**先写测试确认失败**
- [ ] T021 [US2] 在 `src/file2kg/serve/mcp_tools.py` 实现 `service_info` 与 `search` 两个工具，工具描述 MUST 足以让 Agent 正确理解用途与参数（FR-017），描述文案见 contracts/mcp-tools.md §2–§3
- [ ] T022 [US2] 在 `src/file2kg/serve/app.py` 挂载 `mcp.http_app()` 为子 app 并**接线 lifespan**（漏接会导致 `/mcp` 首次请求 500，官方文档明确警告）——具体写法以 T005 的 U1/U2 实测结论为准
- [ ] T023 [US2] 核对挂载路径不叠加成 `/mcp/mcp`，起真服务打一次 `/mcp` 冒烟确认会话管理器已初始化
- [ ] T024 [US2] 断言**双入口清单恒等**：`GET /info` 的 `capabilities` 与 MCP 工具名集合严格相等（同一份 `CapabilityManifest` 驱动，contracts/mcp-tools.md §5）

**Checkpoint**: US1 + US2 都可用 —— 检索常驻 + Agent 只读接入

---

## Phase 5: User Story 3 - 显式开启写权限，经服务触发增量摄取 (Priority: P3)

**Goal**: 写能力 opt-in；开启后经服务摄取，产出与 CLI 同格式的审计

**Independent Test**: 默认起服务确认写能力不可达 → `--allow-write` 重启 → 触发摄取 → 库更新且新增一份审计文件

**依赖**: 需要 US1 的服务骨架

### Implementation for User Story 3

- [ ] T025 [US3] 在 `tests/unit/test_serve_http.py` 写写能力测试：只读模式下 `POST /ingest` 返回 **404**（**不是 403**）且错误体 code 为 `WRITE_DISABLED`、`hint` 指向开启方式；写模式下 `/ingest` 路由**必须存在**（配对断言）——**先写测试确认失败**（本任务与 T030 同写一个文件，**不可与 T030 并行**）
- [ ] T026 [US3] 在 `src/file2kg/serve/mcp_tools.py` 实现**条件注册**的 `ingest` 工具：仅 `mode=="read-write"` 时调用注册（v4 无 `enabled=` 参数，"永不注册"是官方推荐做法，见 research.md D5）
- [ ] T027 [US3] 在 `src/file2kg/serve/http_api.py` 实现 `POST /ingest`（仅写模式注册）：调用既有 `ingest()` 并传入**常驻 Embedder 实例**（T008 的注入参数，避免第二个模型实例）→ 投影为 `JobSummary`；`skipped_files` 与 `errors` **MUST** 出现在响应中（宪法原则 IV）
- [ ] T028 [US3] 在 `src/file2kg/serve/app.py` 实现**写操作串行化**：同一时刻至多一个作业，并发写返回 **409 `WRITE_IN_PROGRESS`**（保护 `Auditor` 的单写者前提，research.md D7）
- [ ] T029 [US3] 验证审计留痕：经服务触发摄取后，确认审计文件真实存在、格式与 CLI 摄取产出的审计**逐字段一致**，且不存在"库已变更但无对应审计"的状态（SC-005）

**Checkpoint**: 写入路径 opt-in 且全程留痕

---

## Phase 6: User Story 4 - 服务自述：随时说清自己在服务什么 (Priority: P4)

**Goal**: 客户端随时可查所服务的库、绑定模型与维度、权限模式、预热状态

**Independent Test**: 查询 `/info` 返回完整自述；模型不匹配时服务拒绝启动并说明两边模型名

**依赖**: 需要 US1 的服务骨架

### Implementation for User Story 4

- [ ] T030 [US4] 在 `tests/unit/test_serve_http.py` 写 `/info` 测试：字段完整性、`model`/`dim` **取自库元数据**而非进程配置、**`warm` 状态成对断言**（默认 `False`、显式 `--preload` 后 `True`，plan.md §② 的配对原则）、**调用 `/info` 不改变 `warm`**（FR-007）——**先写测试确认失败**（本任务与 T025 同写一个文件，**不可与 T025 并行**）
- [ ] T031 [US4] 在 `src/file2kg/serve/http_api.py` 实现 `GET /info`，返回 `ServiceDescriptor`（data-model.md §2.1）。`warm` MUST 由 `Embedder.is_loaded` 回答，**服务不得自持标志位**（research.md C5）
- [ ] T032 [US4] 在 `src/file2kg/serve/app.py` 实现启动时的**分层一库一模校验**：比对库元数据中的模型名 vs 配置模型名，不符即**拒绝启动**并同时报出两边模型名与维度；此检查 MUST NOT 加载模型（否则破坏 `warm=false`，research.md C3）

**Checkpoint**: 四个故事全部独立可用

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: 端到端走查、安全加固、发布准备、文档同步、回归

- [ ] T033 [P] 按 quickstart.md 的**全部 7 个场景**逐条走查，记录实际结果与预期的差异
- [ ] T034 [P] 更新 `README.md`：命令参考表加 `serve`；安装说明加 `pip install "file2kg[serve]"`；速度预期表把「常驻服务 v0.2」从计划改为已实现
- [ ] T035 [P] 更新 `file2kg-README.md`：路线图勾掉 v0.2 的 serve 条目；若实现中产生了新的踩坑经验，按项目惯例记入对应模块 docstring
- [ ] T036 跑全量测试（既有约 110 个 + 本功能新增），确认**无回归**；宪法「交付前 MUST 跑通全量测试」
- [ ] T037 在 `tests/unit/test_serve_service.py` 增加**密钥不泄漏断言**：以哨兵值（如 `sk-SENTINEL`）作为 API key 构造服务，断言 HTTP 错误体、服务日志、审计文件中均不出现该哨兵片段（FR-020 / 宪法原则 I）
- [ ] T038 发布准备：把 `pyproject.toml` 的 `version` 从 `0.1.0` 提到 `0.2.0`，并核对 `contracts/*.md`、`quickstart.md` 中 `file2kg/0.2.0` 的引用口径一致（可考虑改为从包元数据动态读取版本，避免今后再漂移）
- [ ] T039 宪法合规终检：逐条复核五原则（重点 III 的成对断言、II 的预热默认、V 的拒开），确认 plan.md 的 Constitution Check 结论与最终实现一致——**在 T037/T038 完成后执行**，确保覆盖新增内容

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 Setup**: 无依赖，但 **T006 闸门必须通过** 才能继续
- **Phase 2 Foundational**: 依赖 Phase 1 —— **阻塞所有用户故事**
- **Phase 3 US1**: 依赖 Phase 2；**是 US2/US3/US4 的隐含前置**（后三者都需要服务骨架与检索路径）
- **Phase 4 US2 / Phase 5 US3 / Phase 6 US4**: 都依赖 US1；彼此之间可并行
- **Phase 7 Polish**: 依赖全部所需故事完成

### 用户故事依赖（诚实标注）

| 故事 | 依赖 | 说明 |
|------|------|------|
| US1 (P1) | Phase 2 | 无故事间依赖——**独立可交付的 MVP** |
| US2 (P2) | **US1** | `search` 工具复用 US1 的检索路径；工具清单断言独立 |
| US3 (P3) | **US1** | 摄取走服务骨架；可先行实现"缺席"部分 |
| US4 (P4) | **US1** | `/info` 需要服务在运行 |

> 三个后续故事之间**互不依赖**，可并行推进。

### Within Each User Story

- 测试先写并确认**失败**，再实现（宪法「测试即合同」）
- 组装点 → 服务逻辑 → 端点
- 核心实现完成后才做集成验证

### Parallel Opportunities

- T007–T009（Phase 2 的三个 [P] 任务）分属不同文件，可并行
- T013 与 T020 可并行编写（分属 `test_serve_http.py` 与 `test_serve_mcp.py`）
- **T025 与 T030 虽分属 US3/US4，但都写 `tests/unit/test_serve_http.py`，MUST 串行**（已去掉二者的 `[P]` 标记）
- T033–T035（文档类）可并行

---

## Parallel Example: Phase 2 Foundational

```bash
# 三个 [P] 任务分属三个不同文件，可同时进行：
Task: "T007 embedder.py 增加 is_loaded + 测试"
Task: "T008 ingest.py 增加 embedder 注入参数 + 测试"
Task: "T009 config.py 增加 ServeConfig"
```

---

## Implementation Strategy

### MVP First（US1 Only）

1. Phase 1 Setup —— **T006 闸门是硬门**
2. Phase 2 Foundational（阻塞项）
3. Phase 3 US1
4. **停下验证**：按 quickstart 场景 2 确认常驻加速成立
5. 此时已可发布：一个能反复秒回的本地检索服务

### Incremental Delivery

1. Setup + Foundational → 地基就绪
2. US1 → 验证 → **MVP**（常驻检索）
3. US2 → 验证 → Agent 接入（只读）
4. US3 → 验证 → 写入 opt-in
5. US4 → 验证 → 服务自述
6. Polish → 安全加固 + 发布准备 + 全量回归 + 文档同步

每个故事增量交付且不破坏前面的故事。

---

## Notes

- [P] = 不同文件、无未完成依赖
- 每个故事结束都是可独立验证的检查点
- **T006 是硬闸门**：实测与文档冲突 → 改文档，不硬写代码
- **T003 是人工确认门**：干跑报告核对通过才装依赖
- **T001 必须先于 T002**：先声明 extra，再 `pip install -e ".[serve]"`
- 提交时机：每个任务或逻辑组完成后提交
- 避免：模糊任务、同文件冲突、破坏独立性的跨故事依赖
