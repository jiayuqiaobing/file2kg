# file2kg（暂定名）—— 文件 → 知识库摄入管道

> **Zero API keys. Lazy by design. Every byte accountable.**
>
> 中文草稿 v0.1（正式发布前转英文）。2026-09-01 定稿：独立通用项目，不绑定任何 Agent 或业务。2026-09-02 融合面经工程实践 + 2026 最新评测更新（embedding 换代 Qwen3、质量卫生、明确边界）。

---

## 一句话

**RAG 全链路最上游的「数据摄入（Ingestion）管道」：输入文件，输出知识库。不绑 Agent、不绑业务、不绑厂商——两端都是标准接口。**

```
你的文档（md/txt/pdf）──▶ file2kg ──▶ 知识库（LanceDB）+ 审计清单
```

任何 Agent、任何项目、任何人都能用：CLI 一条命令、HTTP API、MCP 三种入口。

## 设计理念：三个词

**1. 零设置（Zero-friction）** —— 默认体验是"装完即用"：
不用注册任何平台、不用复制 API key、不用看 30 页文档。高级功能是可选后门，不强迫任何人。

**2. 懒惰（Lazy by design）** —— 工具只在需要时用力：
模型懒加载，空闲时内存 <50MB；`list`/`status`/`audit` 等命令根本不碰模型，秒开响应；常驻服务支持一键预热消除冷启动。

**3. 诚实（Every byte accountable）** —— 每个字节都有交代：
每个 chunk 都带来源（文件/页码/标题/模型）；每次作业（job）都产出自审计 JSON 清单；换嵌入模型会明确警告"需全量重建"——用旧尺子量新地图的事我们不做。

## 核心能力（差异化七件套）

| # | 能力 | 说明 |
|---|------|------|
| 1 | 默认零 API key | 全本地：Qwen3-Embedding-0.6B（639MB）+ LanceDB，装完即用；API 模式可选后门 |
| 2 | 增量更新 + 去重 | 文件 hash+mtime 双查只重算变更；重复文件跳过；删除文件连带清向量；chunk 带 text_hash，为 v0.2 细分增量铺路 |
| 3 | 审计清单 + 溯源 | 社区唯一做深入的：job_id + 每 chunk 的 source/page/heading/emb_model |
| 4 | 本地/API 双兼容 | 默认 Qwen3-0.6B 本地，可切 bge-large-zh-v1.5 或 OpenAI 兼容平台（智谱） |
| 5 | 中文优化的混合检索 | 关键词(FTS,内建 jieba 分词) + 向量 + RRF 融合，LanceDB 原生 |
| 6 | 可选重排（默认关） | 粗筛 50 篇 → Qwen3-Reranker/bge-reranker 精排（中文优化），低于阈值宁可不返回 |
| 7 | 质量卫生 | 扫描件检测→audit 标记；页眉页脚/页码噪声清洗；低质空块不入库——垃圾进垃圾出 |

## 架构

```
              ┌─── watcher（watchdog，文件监看）
              ▼
文件 ──▶ Loader ──▶ Cleaner ──▶ Chunker ──▶ Filter(可选,默认关)
        插件化      规则去噪     标题感知+面包屑   LLM 甄别
                                               │
                                               ▼
              ┌─── CLI / HTTP API / MCP（FastMCP 三大入口）
              ▼
        Embedder ──▶ Store(LanceDB) ──▶ 审计器 ──▶ job JSON 清单
      本地/API 双兼容   薄接口:add/query/delete        可审计、可回放
```

- **Loader**：md/txt/pdf 起步，每格式一个插件，返回统一中间格式；PDF 文本层提取 + 扫描件检测（<50 字符/页 → audit 标记 `skipped`，不做 OCR），表格规则提取转 Markdown（文字层）
- **Chunker**：标题感知 + 句子边界保护，面包屑 `["安装","前置条件","Docker"]` 注入正文前缀 `[Topic: ...]`（就是 Anthropic Contextual Retrieval 的规则版——chunk 带文档上下文前缀，官方口径检索失败率 -49%，我们零成本实现）
- **Embedder**：默认 Qwen3-Embedding-0.6B（2026 MTEB 中文榜第一梯队，639MB，32K 上下文）；批量嵌入（32-64 条/批）提吞吐；API 模式自动指数退避重试；chunk 元数据带 `emb_model`（一库一模原则）
- **Store**：薄接口，只暴露 add/query/delete——换存储不动上层

## 快速开始

```bash
pip install file2kg

file2kg ingest ./docs     # 摄入:一切都在本地,零 key,零费用
file2kg search "怎么配置"  # 混合检索:关键词+向量,亚 100ms
file2kg list              # 不加载模型,秒回
file2kg audit --job 2c3f  # 追查一次作业的完整足迹
file2kg mcp               # 任何 Agent 接入
```

## 审计与溯源（差异化核心,示例）

```json
{
  "job_id": "2c3f4a01...",
  "started_at": "2026-09-01T10:00:00Z",
  "files_total": 42, "ingested": 40, "dupes": 1, "failed": 1,
  "chunks": [
    { "chunk_id": "...", "source": "./docs/指南.md",
      "page": 3, "heading": "安装 > 前置条件",
      "emb_model": "Qwen3-Embedding-0.6B", "text_hash": "sha256:..." }
  ]
}
```

- 每个 chunk 回溯源文件与位置；失败的文件下次重算；每次作业可回放。

## 用户选项:默认即最优(L0-L3 分层)

| 层 | 选项 | 默认值 |
|---|---|---|
| L1 常用开关 | 本地 / API 模式、预热、重排（Qwen3-Reranker 默认）、增量/全量 | local / 关 / 关 / 增量 |
| L2 配置值 | chunk_size / overlap、search_alpha、worker 数 | 512 / 64 / 0.5 / 8 |
| L3 安全项 | MCP 写操作权限 | 只读 |

> 原则：**只给用户他们需要的开关；任何一个值不改也能跑得好用。**

## 对标（既有项目一栏,全表见对比：各家强检索、弱摄入,详见调研笔记）

| 项目 | 零 key | 增量 | 审计/溯源 | MCP | 中文 | 混合检索 |
|---|---|---|---|---|---|---|
| **file2kg** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Rag-Kit | ✅ | ✅ | ❌ | ❌ | ✅ | ✅ |
| vidilearn | ✅ | ❌ | ⚠️ | ✅ | ⚠️ | ✅ |
| doc2vec | ⚠️（默认 API） | ✅ | ❌ | ❌ | ❌ | ❌ |
| CocoIndex | ✅ | ✅（框架级） | ✅ | ❌ | ✅ | ⚠️ |
| LLM Wiki | ⚠️（LLM 强依赖） | ✅ | ⚠️ | ✅ | ✅ | ✅ |

## FAQ

**Q：为什么换 embedding 模型要全量重建？**
向量在同一语义空间里才能互相度量。不同模型甚至不同版本（Qwen3 v1/v2 句子向量抽取点都变了）的语义空间都不同——换模型后旧向量还在库里、却不被检索有意义。用旧尺子量新地图的事情我们不做：检测到与 `emb_model` 标签不一致时直接警告并给出重建命令。

**Q：Qwen3-Embedding 的许可安全吗？**
Qwen 家族以 Apache-2.0 为主，但社区有"自定义商用许可"的说法——发布前以官方仓库为准核实官方许可，本地自用无风险。默认模型可在配置一行切回 bge-large-zh-v1.5。

**Q：遇到扫描件（图片 PDF）怎么办？**
不做 OCR——那是另一条产品线。本工具检测文字量（<50 字符/页）判定扫描件，在审计清单里标记 `skipped`，让缺失对你诚实可见。需要 OCR 的文档请先用现成工具（Marker / PaddleOCR）转文本再喂进来。

**Q：为什么不用语义切分（LLM/embedding 智能分块）？**
2026 年基准评测：递归 512 token 切分端到端准确率 69%，语义切分只有 54%（块太小、生成器没法在碎片上推理）。语义切分贵且不稳，结合我们的标题感知 + 句子边界保护，递归切分就是当前最优解——不买花哨的账。

**Q：为什么不做 GraphRAG / LightRAG？**
建图 token 成本是 Embedding 的 2-5 倍、增量更新要重跑社区检测——和"零设置 + 增量"的立身之本正面冲突。全局结构化问题等评估数据说话，v0.2 后按需再议。

**Q：查询改写 / HyDE 呢？**
那是生成端（检索→回答）的事，我们是"文件→知识库"摄入管道，管道里不做语义改写。接入方是 Agent/应用，它们自己改写——两端标准接口，各司其职。

**Q：本地模型跑不动怎么办？**
切 API 后门：配置里选 OpenAI 兼容平台（如智谱），零代码改动，自动批量嵌入 + 指数退避重试。注意 **DeepSeek 没有 embedding API**——别踩这个坑。

**Q：一个库能混着放多个模型的向量吗？**
不建议（一库一模）。真要多模型就按模型分集合——这也是每个 chunk 都带 `emb_model` 标签的原因：为保险过滤留一手。

## 路线图

**v0.1（约 3-4 天）**
1. types + loader(md/txt) + chunker + store(LanceDB) + Qwen3-0.6B embedding —— 1 天
2. PDF 文本层 + 扫描件检测 + 表格转 Markdown + CLI 骨架（Typer + Rich）—— 0.5 天
3. 增量去重 + hash/mtime + 审计器（chunk 带 text_hash 预留）—— 0.5 天
4. API + FastMCP 服务 + 懒加载/预热开关 —— 1 天
5. README + 测试 + LICENSE(MIT) + 发 GitHub —— 0.5 天

**v0.2（进行中）**
- ✅ **`serve` 常驻服务（HTTP API + MCP）** —— 模型常驻内存，实测冷 26.1s → 热 0.078s（360×）。
  三条默认值直接对应宪法：默认只读（写能力需显式开启，只读时 `/ingest` **不在路由表里**）、
  默认不预热（首次检索才加载）、默认只绑回环地址。设计记录见 `specs/001-serve-api-mcp/`。
- ⬜ 父子文档/句子窗口检索完整版、SimHash 内容级近似去重、元数据过滤（时间/来源）、
  查询端语义缓存、保留历史版本模式、RAGAS/自建评估脚本

**明确不做（防铺开）**：GraphRAG/LightRAG 图谱（增量难、索引成本 2-5x，与零设置+增量立身冲突）、OCR 图片识别（只检测标记）、查询改写/HyDE（生成端的事，管道不越界）、Web 爬取、定时 cron、Web UI、LRU 缓存。

## License

MIT（计划）
