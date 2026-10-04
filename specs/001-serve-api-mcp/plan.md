# Implementation Plan: serve 常驻服务（HTTP API + MCP）

**Branch**: `001-serve-api-mcp` | **Date**: 2026-10-04 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-serve-api-mcp/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command; its definition describes the execution workflow.

## Summary

把 file2kg 从"一次性命令"变成**常驻检索后端**：一条 `file2kg serve` 启动单进程服务，
HTTP API 与 MCP 两个入口共用同一个嵌入模型实例，模型只加载一次即常驻内存，
把每次检索的十几秒冷启动压到亚秒级。

技术上是一个**薄壳**：复用既有 `Embedder` / `Store` / `Auditor`，不重写任何核心逻辑。
服务层的全部新增价值在于三件事——**权限在注册期就决定了能力是否存在**（默认只读，
无写工具）、**模型加载状态真实可观测**（预热非默认且可证伪）、**所有失败都如实可见**。

## Technical Context

**Language/Version**: Python ≥ 3.10（实际目标 3.11+；项目环境 3.11.16）

**Primary Dependencies**:
新增可选 extra `[serve]`：`fastmcp>=4.0,<5`（Apache-2.0）+ `starlette>=1.0.1` + `uvicorn>=0.35`。
后两者**已在 FastMCP 依赖树内**（其 server extra 与官方 `mcp` SDK 均无条件要求），属声明而非新增下载。
**HTTP 层不引入 FastAPI**——`Starlette` 纯路由即可，净新增安装包为 0。
详见 [research.md](./research.md) D1–D3。

**Storage**: 复用既有 LanceDB 库与 JSONL 审计；**不新增任何持久化格式、不改库 schema**

**Testing**: pytest（既有）。异步内省用 `asyncio.run()` 包在同步测试里，**不引入 pytest-asyncio**。
HTTP 测试客户端已由 smoke 实测确定（research.md U5）：`starlette.testclient.TestClient` 可用；
但它依赖 `httpx`，而 `httpx` 在本项目原本只是 `huggingface-hub` 的传递依赖——
**MUST 显式声明进 `dev` extra**，不得靠传递依赖支撑测试

**Target Platform**: 本机（Windows 优先，兼顾 macOS/Linux）

**Project Type**: single project（CLI + 常驻服务，同一 codebase）

**Performance Goals**: 服务就绪后检索响应**亚秒级**（对比每次冷启动约 10 秒，快一个数量级）；
进程内模型加载次数**恒为 1**

**Constraints**: 默认只读 / 默认不预热 / 默认只绑回环地址；不引入新持久化格式；
serve 依赖延迟导入（缺依赖给安装指引而非 traceback）

**Scale/Scope**: 单用户、单库；3 个 HTTP 端点 + 3 个 MCP 工具（其中 1 个写入工具仅写模式下存在）

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原则 | 结论 | 落点与证据 |
|------|------|-----------|
| **I. 本地优先，零 Key 默认** | ✅ 通过 | FR-003（默认模式无需 key）；FR-002（默认只绑 127.0.0.1）；新增依赖许可：fastmcp **Apache-2.0** / starlette **BSD-3-Clause** / uvicorn **BSD-3-Clause**，均可商用。**待验**：新增依赖（全新环境解析 68 个包 / 本项目环境实测净新增 53 个，零升降级）不得引入"必须联网/凭据"的默认路径——`keyring` 已知在依赖树内，U6 冒烟须**专门验证默认本地路径不触碰系统钥匙串**（research.md U6，实现期实测） |
| **II. 懒惰即设计** | ✅ 通过 | FR-005（首次需要才加载、之后常驻）；FR-006（预热非默认）；FR-007（只读能力不碰模型）；serve 依赖延迟导入（`[serve]` extra） |
| **III. 写操作默认只读**（NON-NEGOTIABLE） | ✅ 通过 | FR-008/009/011/012/013。**证明方式见下 §①** |
| **IV. 审计诚实：每字节有交代**（NON-NEGOTIABLE） | ✅ 通过 | FR-010（服务摄取复用 `Auditor`，格式与 CLI 一致）；FR-016（结构化错误 + 可操作 hint）；FR-018/019（不装死、干净退出）；D6（hybrid 缺索引报错不降级）；D7（写串行化，保护单写者前提） |
| **V. 一库一模**（NON-NEGOTIABLE） | ✅ 通过 | FR-014。**分层校验**见 research.md C3：启动时比对**模型名**（不加载模型），首次嵌入时校验**维度**（走既有 `_assert_dim`）。服务 MUST NOT 依赖 `dim=None` 路径自证 |
| **技术约束：数据契约单一来源** | ✅ 通过 | 不新增跨模块数据定义；`SearchHit` / `ServiceDescriptor` 等均为服务层投影（data-model.md）；若将来需跨模块共享再按契约移入 `types.py` |
| **技术约束：边界（明确不做）** | ✅ 通过 | HTTP API 与 MCP 在宪法中明确**不属于** Web UI 之列；不引入爬取、cron、图数据库、OCR |
| **开发工作流：实测优先于文档** | ✅ 通过 | 实现第 0 步即 `scripts/smoke_fastmcp.py`（见 research.md §4） |
| **开发工作流：复杂度必须辩护** | ✅ 通过 | 不引入 FastAPI（D2）；不引入 pytest-asyncio（D8）；serve 子包仅 3 个模块（§结构决策） |

**核心模块改动声明**（用户指引 #1「禁止复制或重写核心逻辑」）：本功能**不复制、不重写**任何核心逻辑，
但需在两处核心模块做**纯增量的最小改动**：

| 文件 | 改动 | 必要性 |
|------|------|--------|
| `embedder.py` | 增加只读访问器 `is_loaded` | FR-015 要求报告 `warm`；且这是 §② 可证伪性的支点（research.md C5） |
| `ingest.py` | `ingest()` 增加可选 `embedder` 注入参数（缺省 `None` ⇒ 行为不变） | 否则服务调用 `ingest()` 会产生**第二个模型实例**（research.md C1） |

两处改动均为**纯增量**：缺省路径行为完全不变，既有 110 个单测不受影响。

**Post-Phase 1 复审**：设计完成后重新核对，五条原则与全部约束项**均通过**，无违规、无需
Complexity Tracking。

---

## §① 写能力"默认不存在"的证明方式（用户指定重点）

**设计前提**：`CapabilityManifest` 在启动时构造，**运行期不可变**（权限模式只能重启变更）。
写能力的"不存在"因此是可静态断言的，而不依赖运行期检查。

**三层证明**：

| 层 | 断言 | 说明 |
|----|------|------|
| **注册期** | 只读模式下 `/ingest` 的 Route **从不进入** `routes=[...]`；`ingest` 工具**从不调用**注册 | 最强的一层——不存在于对象图中 |
| **可观察** | `GET /info` 的 `capabilities` 与 `await mcp.list_tools()` 的名字集合**恒等**且不含 `ingest` | 两个入口由**同一个** `CapabilityManifest` 驱动，不允许各维护一份 |
| **行为** | `POST /ingest` 返回 **404**（而非 403），由父 app 的 404 处理器补上 `WRITE_DISABLED` + 开启指引 | 满足 FR-008（不可调用）与 US3 场景 1（拒绝理由有帮助）：**可调用性 = 没有，可发现性 = 有帮助** |

**关键：断言必须成对**。只断言"不存在"是**假证明**——一个什么都没注册的实现同样能通过。
因此每个"缺席断言"必须配一个"在场断言"，用**同一个构建函数**、只改权限模式：

```python
# 反向配对：证明"缺席"不是因为功能没实现
def test_read_only_has_no_write_capability():
    svc_ro = build_service(mode="read-only", ...)
    assert "ingest" not in svc_ro.capabilities
    assert "/ingest" not in {r.path for r in svc_ro.app.routes if hasattr(r, "path")}

def test_read_write_has_write_capability():          # ← 配对项，缺了它就假通过
    svc_rw = build_service(mode="read-write", ...)
    assert "ingest" in svc_rw.capabilities
    assert "/ingest" in {r.path for r in svc_rw.app.routes if hasattr(r, "path")}

def test_mcp_tools_absent_in_read_only():
    names = {t.name for t in asyncio.run(build_service(mode="read-only", ...).list_tools())}
    assert names == {"service_info", "search"}

def test_mcp_tools_present_in_read_write():          # ← 配对项
    names = {t.name for t in asyncio.run(build_service(mode="read-write", ...).list_tools())}
    assert names == {"service_info", "search", "ingest"}
```

**落点**：`tests/unit/test_serve_service.py`（能力清单）、`test_serve_http.py`（路由缺席 + 404 语义）、
`test_serve_mcp.py`（工具缺席）。

---

## §② "预热非默认"的测试钉法（用户指定重点）

**设计前提**：`warm` **不得**由服务自持标志位回答——那可以撒谎。
`warm` MUST 由 `Embedder` 的真实加载状态回答（新增只读访问器 `is_loaded`，research.md C5）。

**三条钉子**（同样成对）：

```python
def test_preload_off_by_default():                   # 默认不预热
    svc = build_service(preload=False, ...)
    assert svc.embedder.is_loaded is False

def test_info_does_not_load_model():                 # 只读能力不拉模型（FR-007）
    svc = build_service(preload=False, ...)
    asyncio.run(call_info(svc))
    assert svc.embedder.is_loaded is False           # 查了 /info 依然没加载

def test_preload_on_when_explicit():                 # ← 配对项：证明 is_loaded 不是恒 False
    svc = build_service(preload=True, ...)
    assert svc.embedder.is_loaded is True

def test_ingest_reuses_resident_embedder():          # C1 的钉子
    svc = build_service(mode="read-write", ...)
    resident = svc.embedder
    asyncio.run(call_ingest(svc))
    assert svc.embedder is resident                  # 同一个实例，没有第二个模型
    assert svc.embedder.is_loaded is True
```

**为什么第三、四条不可省**：若 `is_loaded` 因为实现错误恒为 `False`，前两条会**假通过**——
一个永远不加载模型的实现看起来完美满足"预热非默认"。配对断言把这个漏洞堵死。
第四条同时把 research.md C1（第二个 Embedder 实例）钉在测试里。

**落点**：`tests/unit/test_serve_service.py`。

---

## Project Structure

### Documentation (this feature)

```text
specs/001-serve-api-mcp/
├── plan.md              # 本文件（/speckit-plan 输出）
├── spec.md              # 功能规格（/speckit-specify 输出）
├── research.md          # Phase 0：选型决策 + 代码考古
├── data-model.md        # Phase 1：实体与状态机
├── quickstart.md        # Phase 1：验证指南（7 个场景）
├── contracts/           # Phase 1：接口契约
│   ├── http-api.md      #   3 个端点 + 错误契约
│   └── mcp-tools.md     #   3 个工具 + 条件注册
├── checklists/
│   └── requirements.md  # 规格质量清单
└── tasks.md             # Phase 2 输出（/speckit-tasks 生成，非本命令）
```

### Source Code (repository root)

```text
src/file2kg/
├── cli.py               # 【改】+ serve 命令（内部延迟导入 serve 包，缺依赖给安装指引）
├── config.py            # 【改】+ ServeConfig（端口/绑定/权限模式/预热的默认值）
├── embedder.py          # 【改】+ is_loaded 只读访问器
├── ingest.py            # 【改】+ 可选 embedder 注入参数（缺省行为不变）
├── store.py             # 【不改】复用
├── auditor.py           # 【不改】复用
└── serve/               # 【新】子包
    ├── __init__.py      #   导出 build_service / run
    ├── app.py           #   组装：父 Starlette app + MCP 子 app + lifespan 接线
    ├── http_api.py      #   3 个路由 + 错误契约 + 404 兜底（WRITE_DISABLED）
    └── mcp_tools.py     #   工具定义 + 条件注册

scripts/
└── smoke_fastmcp.py     # 【新】实现第 0 步：实测 research.md U1–U6

tests/unit/
├── test_serve_service.py  # 【新】能力清单、分层校验、warm 状态（§①② 的落点）
├── test_serve_http.py     # 【新】路由契约、错误契约、404 语义
└── test_serve_mcp.py      # 【新】工具注册与缺席（成对断言）
```

**Structure Decision**:

延续项目既有的 **single project + flat modules** 布局，本功能新增一个 `serve/` 子包（3 个模块）：

- `app.py` 是**组装点与测试入口**——`build_service()` 一处构造 `Embedder`/`Store`/`CapabilityManifest`
  并返回 app，§①② 的全部测试都从这里进入。它必须独立于路由定义
- `http_api.py` 与 `mcp_tools.py` 拆开的理由：两者的**错误模型不同**（JSON 状态码 vs 工具级异常），
  混在一个文件里会让"这个辅助函数给谁用"变得含糊
- **不引入任何新的分层概念**（无 service/repository/controller 三件套）；`serve/` 是进程入口的
  组织，不是架构分层。若实现后发现总量很小，`http_api.py` 与 `mcp_tools.py` 可合并——
  但 `app.py` 的组装职责必须保留（它是可测试性的支点）

**实现顺序**（理由见 research.md §5）：

```text
0. scripts/smoke_fastmcp.py            ← 先解决 U1–U6，否则后面全建在沙子上
1. embedder.is_loaded + ingest(embedder=)   ← 两处核心最小改动
2. serve/app.py（Service 对象 + 能力清单 + 分层校验）
3. serve/http_api.py + serve/mcp_tools.py
4. cli.py:serve 命令 + pyproject [serve] extra
5. tests/（§①② 的成对断言）+ 全量回归
```

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

无宪法违规，本节不适用。
