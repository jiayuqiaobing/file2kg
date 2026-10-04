# Quickstart: serve 常驻服务 —— 验证指南

**Feature**: `001-serve-api-mcp` | **Date**: 2026-10-04 | **Spec**: [spec.md](./spec.md)

本文档是**验证指南**，不是实现说明。每个场景给出命令与预期结果，用来证明功能确实可用。
契约细节见 [contracts/](./contracts/)，数据结构见 [data-model.md](./data-model.md)。

---

## 前置准备

```bash
pip install -e ".[dev]"          # 项目环境（Python ≥ 3.10）

# 先建一个库（服务假定库已存在，不负责首次建库）
file2kg ingest demo_docs/ --db mykb
```

---

## 场景 1：服务自述证明"默认只读"

**证明什么**：FR-008 / FR-009、US4 —— 默认模式是只读，且这一点对客户端可见。

```bash
file2kg serve --db mykb --port 8765      # 不加任何权限开关
curl http://127.0.0.1:8765/info
```

**预期**

```json
{
  "service": "file2kg/0.2.0",
  "db_dir": "...mykb",
  "table": "docs",
  "model": "Qwen/Qwen3-Embedding-0.6B",
  "dim": 1024,
  "mode": "read-only",
  "warm": false,
  "capabilities": ["service_info", "search"]
}
```

✅ `mode` 是 `read-only`；`capabilities` **不含 `ingest`**
✅ `warm` 是 `false` —— 启动**没有**顺手把模型加载起来（宪法原则 II）
✅ 这个请求本身没有把 `warm` 变成 `true`（再 `curl` 一次 `/info` 仍是 `false`）

---

## 场景 2：常驻加速 —— 第二次开始亚秒级

**证明什么**：SC-001 / SC-002、US1 —— 模型只加载一次，之后常驻。

```bash
time curl -s -X POST http://127.0.0.1:8765/search \
     -H 'Content-Type: application/json' \
     -d '{"query":"默认端口是多少"}' > /dev/null      # 第一次：付冷启动

time curl -s -X POST http://127.0.0.1:8765/search \
     -H 'Content-Type: application/json' \
     -d '{"query":"怎么配置"}' > /dev/null            # 第二次：应显著变快
```

**预期**

✅ 第一次明显慢（加载模型），第二次起**亚秒级**
✅ `curl http://127.0.0.1:8765/info` 此时 `warm` 为 `true`
✅ 连续 100 次检索后，服务日志中模型加载记录**只有 1 次**（SC-002）

---

## 场景 3：写能力"默认不存在"的双入口证明

**证明什么**：FR-008、US2 场景 4、US3 场景 1 —— **Constitution Check ① 的核心验证**。

**(a) HTTP 侧：路由不存在，但拒绝理由有帮助**

```bash
curl -i -X POST http://127.0.0.1:8765/ingest \
     -H 'Content-Type: application/json' -d '{"docs_dir":"docs"}'
```

**预期**：HTTP **404**（不是 403），响应体为

```json
{ "error": { "code": "WRITE_DISABLED",
  "message": "写能力未启用",
  "hint": "以 --allow-write 重启服务可开启（当前为只读模式）" } }
```

✅ 状态码是 404 —— 路由**从未注册**，"不存在"而非"存在但拒绝"
✅ 提示仍然可操作 —— 拒绝不等于把用户晾着

**(b) MCP 侧：工具清单里根本没有写工具**

连接 MCP 客户端（或直接查 `/info`），列出工具：

**预期**：只看到 `service_info` 与 `search`；**`ingest` 不存在**。

✅ 两处清单恒等（同一份 `CapabilityManifest` 驱动，见 contracts/mcp-tools.md §5）

---

## 场景 4：显式开启写权限后，经服务摄取并留痕

**证明什么**：FR-010 / FR-011、US3 —— 写是 opt-in，且产出物与 CLI 一致。

```bash
# 停掉只读服务，显式开启写权限重启
file2kg serve --db mykb --port 8765 --allow-write --docs-dir demo_docs/

curl -s -X POST http://127.0.0.1:8765/ingest \
     -H 'Content-Type: application/json' -d '{"docs_dir":"demo_docs/"}'
```

**预期**

```json
{ "job_id": "...", "files_total": 3, "ingested": 3, "dupes": 0,
  "skipped": 0, "failed": 0, "chunk_count": 12,
  "audit_path": "...mykb/../file2kg-audit/<job_id>.jsonl",
  "skipped_files": {}, "errors": {} }
```

✅ `/info` 的 `capabilities` 现在含 `ingest`
✅ `audit_path` 指向的审计文件真实存在，且与 CLI 摄取产生的审计**同格式**
✅ 再跑一次同样的请求 → `ingested: 0`（增量生效，不重复入库）

**(c) 并发写**

同时发两个 `/ingest` 请求 → 第二个返回 **409 `WRITE_IN_PROGRESS`**，库与审计不出现交叉写坏。

---

## 场景 5：一库一模 —— 模型不匹配时拒绝服务

**证明什么**：FR-014、US4 场景 2、宪法原则 V。

```bash
# 库是 Qwen3-Embedding-0.6B 建的，故意用别的模型去服务它
file2kg serve --db mykb --port 8765 --model BAAI/bge-large-zh-v1.5
```

**预期**

✅ 服务**拒绝启动**，报错同时说清两边的模型名与维度：

```
错误: 库 'docs' 由 Qwen/Qwen3-Embedding-0.6B(1024维) 建立，当前模型 BAAI/bge-large-zh-v1.5(...)
      不兼容！请换回原模型或重建库
```

✅ **绝不静默打开**——宁可起不来，不可哑开
✅ 注意：这个检查发生在**不加载模型**的前提下（比对库元数据里的模型名），
   否则会破坏场景 1 的 `warm: false`

---

## 场景 6：异常情形不装死

**证明什么**：SC-007 / SC-008、宪法原则 IV。

| 操作 | 预期 |
|------|------|
| `{"query":""}` | 400 `INVALID_REQUEST` + 合法取值提示 |
| `{"query":"x","k":9999}` | 400 + `k` 的合法区间 |
| 空库上 hybrid 检索 | 503 `INDEX_MISSING` + "先跑一次 ingest"（**不静默降级**） |
| 运行中删掉库目录再检索 | 503 `STORE_UNAVAILABLE`，服务**不崩溃** |
| 服务起来后端口被抢占的情况 | 启动即明确失败，**不静默换端口** |
| 上述任一操作之后 | 服务仍在运行（`/info` 仍可访问） |

---

## 场景 7：干净退出

**证明什么**：SC-009。

```bash
# Ctrl-C 停止服务后立即重启
file2kg serve --db mykb --port 8765
```

✅ 端口已释放，立即可重启，无"地址已被占用"
✅ 无残留半截状态文件

---

## 一页速查

| 想验证 | 跳到 |
|--------|------|
| 默认只读、预热非默认 | 场景 1 |
| 模型常驻、亚秒响应 | 场景 2 |
| 写能力默认不存在（HTTP + MCP） | 场景 3 |
| 写权限 opt-in + 审计留痕 | 场景 4 |
| 一库一模拒开 | 场景 5 |
| 异常不装死 | 场景 6 |
| 干净退出 | 场景 7 |
