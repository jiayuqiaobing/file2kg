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
