# Contract: HTTP API

**Feature**: `001-serve-api-mcp` | **Date**: 2026-10-04 | **Spec**: [../spec.md](../spec.md)

默认监听 `http://127.0.0.1:<port>`。所有响应为 `application/json; charset=utf-8`。

---

## 1. 端点清单

| 方法 | 路径 | 能力 | 出现条件 |
|------|------|------|----------|
| `GET` | `/info` | `service_info` | **恒有** |
| `POST` | `/search` | `search` | **恒有** |
| `POST` | `/ingest` | `ingest` | **仅 `mode=read-write`** |

**默认（`read-only`）模式下 `/ingest` 路由 MUST NOT 被注册。**
这不是"注册了但返回 403"——是路由不存在。只有兜底 404 处理器会给出针对性提示（见 §4）。

---

## 2. `GET /info`

返回 `ServiceDescriptor`（见 [data-model.md §2.1](../data-model.md)）。**不触发模型加载。**

**200 响应**

```json
{
  "service": "file2kg/0.2.0",
  "db_dir": "D:/work/mykb",
  "table": "docs",
  "model": "Qwen/Qwen3-Embedding-0.6B",
  "dim": 1024,
  "mode": "read-only",
  "warm": false,
  "capabilities": ["service_info", "search"]
}
```

- `model` / `dim` 取自**库元数据**（建库时的绑定），不是进程配置——客户端由此确知"这个库绑的是什么"
- `warm` 反映模型是否已常驻；默认启动后为 `false`（宪法原则 II）
- `capabilities` 与 `CapabilityManifest` 完全一致；**只读模式下不含 `ingest`**

---

## 3. `POST /search`

**请求体**

```json
{ "query": "默认端口是多少", "k": 10, "hybrid": true }
```

| 字段 | 类型 | 必填 | 默认 | 校验 |
|------|------|------|------|------|
| `query` | string | 是 | — | 去空白后非空；超长返回 400 |
| `k` | integer | 否 | 10 | 1..50，越界返回 400 |
| `hybrid` | boolean | 否 | `true` | — |

**200 响应**

```json
{
  "results": [
    {
      "chunk_id": "a1b2c3d4e5f60718",
      "text": "[Topic: 安装 > 前置条件] ...",
      "source": "安装.md",
      "heading": "安装 > 前置条件",
      "page": null,
      "score": 0.0321
    }
  ],
  "count": 1,
  "mode": "hybrid"
}
```

- **`vector` 与 `text_hash` 字段 MUST NOT 出现在响应中**（见 data-model.md §1 的约束）
- `count == 0` 时返回 **200**（空结果不是错误），并附 `note` 说明原因：

  ```json
  { "results": [], "count": 0, "mode": "hybrid",
    "note": "库为空或尚未建立关键词索引——先运行一次 file2kg ingest" }
  ```

- `mode` 回显**实际生效**的检索模式

**hybrid 但关键词索引缺失**：返回 **503 + `INDEX_MISSING`**，**不做静默降级**。
调用方若接受纯向量，应显式传 `"hybrid": false`。（与 CLI 行为一致；理由见 research.md）

---

## 4. `POST /ingest`（仅 `read-write`）

**请求体**

```json
{ "docs_dir": "D:/work/docs", "force": false }
```

**200 响应** —— `JobSummary` 投影（见 [data-model.md §2.4](../data-model.md)）

```json
{
  "job_id": "2c3f4a01b8d9",
  "files_total": 42,
  "ingested": 40,
  "dupes": 1,
  "skipped": 0,
  "failed": 1,
  "chunk_count": 318,
  "audit_path": "D:/work/file2kg-audit/2c3f4a01b8d9.jsonl",
  "skipped_files": {},
  "errors": { "坏文件.md": "读取失败: ..." }
}
```

- `skipped_files` / `errors` MUST 出现在响应里（宪法原则 IV：已知的不完美必须可见）
- 并发写请求：同一时刻至多一个作业；第二个请求被拦截并返回 **409 + `WRITE_IN_PROGRESS`**

---

## 5. 错误契约

**统一错误体**

```json
{
  "error": {
    "code": "INDEX_MISSING",
    "message": "关键词索引缺失，混合检索无法执行",
    "hint": "先运行一次 file2kg ingest 建立索引；或改用纯向量检索 mode=vector"
  }
}
```

- `hint` MUST 存在且可操作（FR-016）——只描述"哪里错了"不合格，必须给出下一步
- `message` MUST NOT 包含裸异常堆栈（宪法原则 IV）

| code | HTTP | 触发条件 | hint 指向 |
|------|------|----------|-----------|
| `INVALID_REQUEST` | 400 | `query` 为空/超长、`k` 越界、JSON 畸形 | 合法取值区间 |
| `ROUTE_NOT_FOUND` | 404 | 路径不存在 | 可用端点清单 |
| `WRITE_DISABLED` | 404 | 请求了 `/ingest` 但当前为 `read-only` | **如何开启写权限** |
| `WRITE_IN_PROGRESS` | 409 | 已有作业在跑 | 稍后重试 |
| `STORE_UNAVAILABLE` | 503 | 库目录/表在运行期消失 | 重建库或检查路径 |
| `INDEX_MISSING` | 503 | hybrid 检索但 FTS 索引缺失 | 跑一次 ingest，或改纯向量 |
| `INTERNAL_ERROR` | 500 | 其他内部异常 | 指向服务日志与审计 |

> `WRITE_DISABLED` 由**兜底 404 处理器**产生：路由本身未注册，处理器识别出这是"被关闭的能力"
> 后给出开启方式。这既满足 FR-008（能力不存在，不可调用），又满足 US3 场景 1（拒绝理由明确）。
> **可调用性 = 没有；可发现性 = 有帮助的提示。** 二者不矛盾。

---

## 6. 边界与安全

- 默认只绑 `127.0.0.1`；绑定非回环地址须显式指定，且启动输出 MUST 打印"知识库已暴露到网络"
- 端口被占用 → 启动失败并明确报错，**不静默换端口**
- 不设认证（本机单用户定位，见 spec Assumptions）
- 日志 MUST NOT 输出密钥
- 服务停止 MUST 干净释放端口与文件句柄（SC-009）
