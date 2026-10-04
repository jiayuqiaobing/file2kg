# file2kg

**文件 → 知识库摄取管道**：把「一堆文档」变成「一个能回答问题的本地知识库」，是 RAG 全链路最上游的那一步。

```bash
file2kg ingest docs/                 # 摄取：文件 → 干净 chunk → 向量库
file2kg query "默认端口是多少"      # 检索：混合召回（向量 + 关键词 + RRF）
```

## 为什么需要它

80% 的 RAG 项目死在第一步：文档来自四面八方（GitHub 仓库、产品手册、课堂笔记、导出网页……），抓取、清洗、分块、嵌入、索引、增量更新、失败重试——全是脏活。file2kg 把这一步做成一个可重复、可审计的 CLI：

- **增量同步**：重复运行只处理变化与新增（mtime + 内容哈希双层判定，文件删除自动清理），库永远和目录一致
- **混合检索**：向量 + 关键词 + RRF 融合，术语精确匹配（端口号、接口名）不再漏
- **全作业审计**：每次摄取一条 JSONL 审计（文件级事件：创建/更新/跳过/失败），断点可查，出问题不用瞎猜哪个文件坏了
- **一库一模一样**：库记录嵌入模型指纹，模型不匹配直接拒绝打开——语义空间永不污染

## 目标用户

- 有一堆文档（md/txt）想建立本地检索，不想逐条粘到聊天框里问
- 自己做 RAG/AI 应用，需要一段「稳的」文件摄取链路，不想从零造轮子
- 隐私敏感，全链路可以本地跑（模型一样可以本地加载，零上传）

**为什么不用聊天直接塞？** 对话框是个会话，不是数据库：上下文窗口有限、关对话就没了、塞过的内容下次还得重塞。file2kg 的理念是「文档落库，问答旁路」——知识入库一次，任何地方都可以检索、引用、复用。

## 安装

```bash
pip install .                # 从本仓库安装（或 pipx install . 全局可用）
pip install -e ".[dev]"      # 开发模式 + pytest

# 要跑常驻服务（serve：HTTP API + MCP）才需要这个 extra——
# 它会带入 FastMCP 及其 50+ 个传递依赖，所以刻意不压在主路径上
pip install -e ".[serve]"
```

要求 Python ≥ 3.10。默认模型 Qwen3-Embedding-0.6B（Apache-2.0，商用自由），首次加载自动下载；下载困难时设镜像 `export HF_ENDPOINT=https://hf-mirror.com`（模型卡直达）。

## 快速开始

```bash
# 1. 摄取：把 docs/ 里的 md/txt 变成库
file2kg ingest docs/ --db mykb

# 2. 检索：混合召回 top 10，带来源与分数
file2kg query "默认端口是多少" --db mykb

# 再次摄取：什么变了就处理什么，没变的 0 秒跳过
file2kg ingest docs/ --db mykb
```

### 换嵌入模型 / 换参数？

表里写了维度指纹，**本库模型不兼容会拒绝打开**（换模型请重建库）：

```bash
file2kg ingest docs/ --db mykb --force   # 全量重挂（换模型/分块参数后）
```

### 不想下载模型？（API 模式）

设置 `FILE2KG_API_KEY` 即切换到 OpenAI 兼容 API（默认阿里云 DashScope），零本地加载：

```bash
export FILE2KG_API_KEY=sk-xxx
file2kg ingest docs/ --db mykb
```

## 命令参考

| 命令 | 说明 |
|------|------|
| `file2kg ingest <dir>` | 摄取目录（增量；`--force` 全量；`--chunk-size/--overlap/--window` 可调） |
| `file2kg query <text>` | 混合检索（`--no-hybrid` 纯向量；`--k 条数`） |
| `file2kg serve` | 常驻服务：HTTP API + MCP，模型常驻内存。**默认只读**（写能力需显式 `--allow-write`）；`--host/--port/--preload/--docs-dir` 可调 |

通用选项：`--db`（库目录，默认 `file2kg-db/`）、`--table`（表名，默认 `docs`）、`--audit`（审计目录，默认 `file2kg-audit/`）。API 端点用 `--api-url` 覆盖。

## 架构

```
文件目录 ──→ Loader ──→ Cleaner ──→ Chunker（注入面包屑） ──→ Filter（可选）
                    │                    │
                    ▼                    ▼
             单文件清洗            带来源的结构化 chunk
                    │                    │
                    ▼                    ▼
              Embedder（本地 Qwen3 / API） ──→ Store（LanceDB：向量 + FTS 索引）
                                                     │
                                                     ├── ← query 混合检索（向量 + 关键词 + RRF）
                                                     ▼
                              Auditor（作业 JSONL：每文件事件可断点、可审计）
```

- `Store`：LanceDB，原生向量检索 + FTS 关键词索引 + RRF 融合，本地单目录，零服务器
- `Auditor`：只追加 JSONL，信号撕裂打开即修复，存储失败不静默改址（业界日志最佳实践）

## 速度预期（诚实版）

| 场景 | 耗时 | 原因 |
|------|------|------|
| 增量摄取（没文件变） | **0 秒** | 懒加载：没有变化不加载模型 |
| 首次摄取 / query | ~10 秒 | Python 生态 import 税（torch + sentence-transformers），**不是模型慢**（模型仅 ~0.7 秒） |
| **`serve` 首次检索**（冷） | **~26 秒** | 服务启动时**不**预加载模型；第一次真正检索才加载（默认行为，`--preload` 可改） |
| **`serve` 后续检索**（热） | **~0.08 秒** | 模型常驻内存。实测：26.1s → 0.078s，**提速 360×**（同一查询连打 5 次） |
| API 模式（阿里云 DashScope） | 1-2 秒 | 网络往返，无本地加载 |

## 开发

```bash
# 测试（110 个单测，全链路用假嵌入模型，秒级跑完）
python -m pytest tests/
```

- 模块站点流程：动每个核心模块前先验证 LanceDB/typer/FastMCP 的实测行为（本项目记录了几处踩坑：RRF 大写 K、谓词单引号、索引新 API、`http_app(path="/")` 与 lifespan 必接等，见 `scripts/smoke_*.py`）
- 路线图：v0.2 —— ~~`serve` 常驻 api/mcp~~ ✅ 已完成；fastembed/ONNX 引擎切换（验收协议：余弦相似度 ≥0.999 才切默认）；PDF/DOCX 加载器
- 常驻服务的完整设计与验收记录见 `specs/001-serve-api-mcp/`（宪法 → 规格 → 研究 → 契约 → 任务）

## 许可证

代码：MIT。默认嵌入模型 [Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)：Apache-2.0，商用自由（模型卡：查询侧免指令话术约有 1-5% 召回折损）。
