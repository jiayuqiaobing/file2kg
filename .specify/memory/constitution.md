# file2kg Constitution

> Zero API keys. Lazy by design. Every byte accountable.
> （零 API key。懒惰即设计。每字节都有交代。）

## Core Principles

### I. 本地优先，零 Key 默认（Local-First, Zero-Key by Default）

默认路径 MUST 在无任何 API key、无账号注册的前提下完成端到端摄取与检索：本地嵌入模型
（Qwen3-Embedding-0.6B）+ 本地 LanceDB，装完即用。

- API 模式（OpenAI 兼容端点）是**显式可选后门**，仅当用户主动提供 `FILE2KG_API_KEY`
  时才启用；默认值 MUST NOT 依赖网络。
- 密钥 MUST NOT 进入命令行参数，或任何会被写入 shell history / 日志 / 审计 / 错误信息的位置
  ——只走环境变量。
- 任何新入口（HTTP API / MCP / 未来接口）MUST 保持同样的默认：不配置即全本地。
- 新增依赖 MUST 允许本地可跑；MUST NOT 引入"必须联网或注册才能工作"的默认路径。

**Rationale**: 隐私敏感用户与离线环境是核心受众，"零设置"是与同类工具的第一差异点。
默认值是产品承诺，不是可随意调换的配置细节。

### II. 懒惰即设计（Lazy by Design）

工具只在需要时用力：不用的东西 MUST NOT 被加载、计算或重算。

- import 零成本、构造零成本：模型与网络句柄 MUST 延迟到首次真正需要时才加载。
- 只读命令（`--help`、列表、状态、审计查阅）MUST NOT 触碰嵌入模型。
- 增量判定 MUST 走「mtime+size 粗筛 → 内容 hash 终裁」双层门；无变化即 0 秒返回。
- 常驻服务 MUST 提供预热能力以消除冷启动，但预热 MUST NOT 成为默认副作用。

**Rationale**: 冷启动税是本地 RAG 工具最大的体验杀手；懒惰既是"零设置"成立的工程前提，
也是增量正确性的来源（没有变化就不加载模型）。

### III. 写操作默认只读（Read-Only by Default）（NON-NEGOTIABLE）

任何对外接口（CLI / HTTP API / MCP / 未来入口）默认 MUST 只暴露读操作。

- 写操作（摄取、删除、改配置、重建）MUST 由用户显式开启后才存在；开启方式 MUST 显式且可审计。
- MCP 与 HTTP 服务的默认权限 MUST 为只读；写权限是 opt-in，MUST NOT 经由隐式默认获得。
- 破坏性操作 MUST 可预演（dry-run），或至少先给出将被影响的范围。
- 库与审计文件的写入 MUST 只经管道内的 `Store` / `Auditor` 单点入口；
  接口层 MUST NOT 绕过它们直接写文件或目录。

**Rationale**: 常驻服务意味着长期暴露面。把安全做成默认值，比指望用户去关掉危险开关可靠得多
——一次误写毁掉的是用户长期积累的库。

### IV. 审计诚实：每字节都有交代（Every Byte Accountable）（NON-NEGOTIABLE）

每个 chunk MUST 可回溯源文件与位置；每次作业 MUST 产出自审计清单。诚实指：宁可暴露缺陷，
绝不静默吞掉。

- 审计日志 MUST 只追加、单写者；作业重跑续写同一文件，MUST NOT 复写历史。
- 尾部撕裂（崩溃半行）MUST 打开即截断修复并记录字节数；中间坏行 MUST 抛硬错误，
  MUST NOT 在坏日志上继续追加。
- 事件序列号 MUST 连续（`seq = 尾行 + 1`）；断档 MUST 可被读端发现。
- 存储与写入失败 MUST 直接抛出；MUST NOT 静默改址、静默写到别处、或让运行"看起来成功"。
- 已知的不完美（扫描件跳过、清洗丢弃、连续失败需人工）MUST 落入审计并出现在作业报告里，
  MUST NOT 只存在于内存或瞬时日志。
- 低质内容 MUST NOT 入库（垃圾进垃圾出）；检索宁可无返回，也不返回坏结果。

**Rationale**: 审计与溯源是本项目对社区的唯一深入差异点，也是"可信"的全部来源。
一个会说谎的审计，比没有审计更危险。

### V. 一库一模（One Store, One Model）（NON-NEGOTIABLE）

一个库（表）MUST 只绑定一个嵌入模型与一个向量维度，绑定关系 MUST 记入库元数据。

- 打开库时 MUST 自检模型与维度指纹；不匹配 MUST 拒绝打开，
  报错 MUST 同时说清两边的模型名与维度，MUST NOT 哑开。
- 每个 chunk MUST 携带 `emb_model` 与 `text_hash` 标签，为过滤与增量留钥匙。
- 换模型或改分块参数后 MUST 全量重建（`--force`）；检测到 config 指纹变更 MUST 警告，
  MUST NOT 静默沿用旧产物。
- 跨模型的向量 MUST NOT 混入同一语义空间；需要多模型时按模型分库分表。

**Rationale**: 不同模型（甚至同模型不同版本）的语义空间不互通。混库产出的检索结果是
"用旧尺子量新地图"——错得静默、错得不可逆。

## 技术约束与边界（Additional Constraints）

### 技术栈

- Python ≥ 3.10；CLI 用 Typer + Rich；存储用 LanceDB（本地单目录，零服务器）；
  本地嵌入用 sentence-transformers + Qwen3-Embedding-0.6B。
- 新增依赖 MUST 具备可商用许可证，且 MUST NOT 破坏"默认零联网即可用"。
- 项目代码许可证：MIT；默认模型许可证 MUST 在发布前核准并保持可商用。

### 数据契约

- `types.py` 中的 `RawDoc` / `Chunk` / `JobReport` 是唯一跨模块数据定义；
  MUST NOT 私造 dict 跨模块传递数据。
- 新增字段 MUST 落入契约文件，MUST NOT 散落在调用方就地拼装。

### 边界（明确不做）

以下方向 MUST NOT 进入本项目，除非先修订本宪法并给出数据/成本论证：

- GraphRAG / LightRAG 图谱（索引成本 2-5x，与「零设置 + 增量」立身之本冲突）
- OCR 图片识别（只做检测与标记，不做识别）
- 查询改写 / HyDE（生成端的事，管道不越界）
- Web 爬取、定时 cron、面向人的 Web UI、LRU 缓存

> 注：机器可读的 HTTP API 与 MCP 服务**不属于** Web UI 之列——它们是本项目的既定入口形态。
> 新增"不做"清单项只需在 feature 提案中说明；移除某项 MUST 走宪法修订。

## 开发工作流与质量门（Development Workflow & Quality Gates）

### 测试即合同

- 每个模块完成时 MUST 同时交付其测试（先写代码后补测试 = 测试会变摆设）。
- 测试红了先改代码；只有"契约本身变了"才允许改测试。
- 测试 MUST NOT 依赖真文件、真模型或网络：用临时目录与假嵌入模型，保证秒级可跑。
- 交付前 MUST 跑通全量测试。

### 实测优先于文档

- 动任何第三方依赖的核心行为前，MUST 先写一次性验证脚本（`scripts/smoke_*.py`）实测该行为，
  MUST NOT 依赖文档印象或记忆。
- 实测得到的反直觉结论 MUST 记录在对应模块的 docstring 中
  （如 RRF 参数是大写 K、谓词用单引号、索引新 API）。

### 接口契约

- stdout = 数据，stderr = UI（进度、错误、渲染结果）。
- 退出码：0 = 成功，1 = 用户输入错，2 = 运行失败。
- 错误信息 MUST 给出修复指引；MUST NOT 把裸 traceback 抛给用户。

### 事务不变量

- 状态 MUST 只在批提交成功后推进；崩溃后库与状态 MUST 回到上一提交点。
- 幂等：重跑 MUST NOT 产生重复行（同 `chunk_id` 覆盖）。
- 源文件删除 MUST 连带清理其向量（僵尸 chunk 防线）。

### 复杂度必须辩护

- 新抽象、新分层、新依赖 MUST 在 feature 的 plan 中说明必要性；
  YAGNI 优先，MUST NOT 为假想需求铺路。

## Governance

- 本宪法 MUST 优先于其他实践与习惯；与之冲突的实现 MUST 被修改，或提案修订本宪法。
- 修订程序：提案 → 说明理由与迁移方案 → 更新本文件并递增版本 →
  在文件顶部留下 Sync Impact Report 供审阅（提交前删除）。
- 版本策略（语义化）：
  - MAJOR：不兼容的治理变更、原则删除或重定义；
  - MINOR：新增原则或章节、实质性扩展既有指引；
  - PATCH：措辞澄清、错别字、非语义微调。
- 合规审查：每个 feature 的 spec / plan / tasks MUST 逐条核对本宪法；
  plan 阶段 MUST 通过 Constitution Check 门（逐原则给出合规结论或偏离理由），
  未通过 MUST NOT 进入实现。
- 偏离原则 MUST 显式记录在 plan 的 Complexity Tracking 中并给出理由；
  无法给出理由的偏离视为违规。
- 运行时指引文件：`README.md`（对外说明与命令参考）、`file2kg-README.md`（设计蓝图与验证标准）、
  `tests/README.md`（测试纪律）。

**Version**: 1.0.0 | **Ratified**: 2026-10-04 | **Last Amended**: 2026-10-04
