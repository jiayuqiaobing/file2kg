"""Config：默认值的"家"——CLI 从这里取默认，模块默认值不被四处复铸。

薄层设计：不搞配置文件/环境变量叠加引擎（v0.1 只有 CLI 一个输入端；
复杂来源组合（yaml/dotenv/多级覆盖）等真有用户要了再做，见项目文档）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .embedder import _MODEL_NAME  # 单一来源：默认模型名

# ---------- 目录与文件约定 ----------

DEFAULT_DB_DIR = "file2kg-db"  # 当前目录下的库目录（lancedb 目录 + state.json 在此）
DEFAULT_TABLE = "docs"  # 库内表名（一库一模）
DEFAULT_AUDIT_DIR = "file2kg-audit"  # 审计 JSONL 目录（每作业一个文件）


@dataclass
class IngestConfig:
    """一次 ingest 作业的完整配置。字段默认值 = CLI 选项默认值。"""

    docs_dir: str
    db_dir: str = DEFAULT_DB_DIR
    table_name: str = DEFAULT_TABLE
    model: str | None = field(default=None, metadata={"default": _MODEL_NAME})  # None=用库默认模型
    chunk_size: int = 512  # token（经 1.7 字符/token 折算）
    overlap: int = 64  # token
    window: int = 32  # 批窗口（chunk 数）
    force: bool = False  # 忽略增量状态，全量重摄
    api_key: str | None = None  # 设置即切 API 模式（本地模型零下载）
    api_url: str | None = None  # OpenAI 兼容端点（默认 dashscope）
    audit_dir: str = DEFAULT_AUDIT_DIR
    state_file: str | None = None  # None = <db_dir>/state.json（ingest 默认）


# ---------- serve 常驻服务 ----------

DEFAULT_SERVE_HOST = "127.0.0.1"  # 只绑回环：知识库默认不出本机（宪法原则 I）
DEFAULT_SERVE_PORT = 8765  # 端口被占时明确失败，不静默换端口（诚实优先）


@dataclass
class ServeConfig:
    """一次 serve 常驻服务的配置。**字段默认值 = 最安全值**。

    三个默认值直接对应宪法条款，改动它们等于改宪法：
    - `mode="read-only"`：写能力默认**不存在**（原则 III，NON-NEGOTIABLE）
    - `preload=False`：预热不是默认副作用（原则 II）
    - `host="127.0.0.1"`：默认不出本机（原则 I）
    """

    db_dir: str = DEFAULT_DB_DIR
    table_name: str = DEFAULT_TABLE
    model: str | None = None  # None = 用库绑定的模型（一库一模）
    host: str = DEFAULT_SERVE_HOST
    port: int = DEFAULT_SERVE_PORT
    mode: str = "read-only"  # "read-only" | "read-write"；运行期不可变，只能重启改
    preload: bool = False
    docs_dir: str = "."  # 写模式下 ingest 请求的默认目标目录
    audit_dir: str = DEFAULT_AUDIT_DIR
    api_key: str | None = None  # MUST 只来自环境变量（原则 I，不进 argv）
    api_url: str | None = None

    def __post_init__(self) -> None:
        """非法配置在构造时就炸——不要留到服务起来一半再发现。"""
        if self.mode not in ("read-only", "read-write"):
            raise ValueError(f"mode 只能是 'read-only' 或 'read-write'，收到: {self.mode!r}")
        if not 0 < self.port < 65536:
            raise ValueError(f"port 越界（1..65535）: {self.port}")

    @property
    def allow_write(self) -> bool:
        """写能力是否开启。只读模式下为 False ⇒ 写入路由/工具**根本不注册**。"""
        return self.mode == "read-write"
