"""file2kg 统一中间格式 —— 全管道的数据契约。

本文件是唯一被所有模块共享的数据定义：
- RawDoc   ：Loader 的输出，文件的原始形态
- Chunk     ：Chunker 的输出、Embedder 的输入/输出，检索的最小单位
- JobReport ：Auditor 的输出，一次作业的审计清单

约束：跨模块传数据必须使用这里的结构，不允许私造 dict。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime


def make_hash(text: str) -> str:
    """文本 SHA256 —— 去重与增量比对的"身份证"。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_chunk_id(text: str) -> str:
    """chunk_id = 内容哈希前 16 位：同一内容的 chunk 永远是同一个 id。"""
    return make_hash(text)[:16]


@dataclass
class RawDoc:
    """Loader 输出的统一文档形态。

    一个 RawDoc 代表"一个来源的一段原文"：
    - md/txt：整个文件 = 一个 RawDoc（page 为 None）
    - pdf：一页 = 一个 RawDoc（page 记页码，从 1 开始）
    """

    text: str  # 提取出的正文
    source: str  # 来源：文件路径（或未来 URL）
    page: int | None = None  # PDF 页码；非 PDF 为 None
    meta: dict = field(default_factory=dict)  # 兜底扩展位（如格式名）


@dataclass
class Chunk:
    """检索最小单位：Chunker 切出的文本段 + Embedder 补上的向量。

    生命周期：Chunker 产出时 embedding 为 None；
    Embedder 填充 embedding 后交给 Store。
    """

    chunk_id: str
    text: str  # 已注入面包屑前缀的正文
    source: str  # 来源文件
    heading: str | None = None  # 面包屑标题，如 "安装 > 前置条件"
    page: int | None = None  # 来源页码
    emb_model: str = ""  # 嵌入模型名（一库一模原则）
    text_hash: str = ""  # 正文 sha256（v0.2 chunk 级增量缓存的钥匙）
    embedding: list[float] | None = None  # 向量；未向量化前为 None

    @staticmethod
    def from_text(
        text: str,
        source: str,
        heading: str | None = None,
        page: int | None = None,
        emb_model: str = "",
    ) -> "Chunk":
        """从文本造 Chunk：text_hash 与 chunk_id 自动算好，调用方不必操心。"""
        return Chunk(
            chunk_id=make_chunk_id(text),
            text=text,
            source=source,
            heading=heading,
            page=page,
            emb_model=emb_model,
            text_hash=make_hash(text),
        )


@dataclass
class AuditChunk:
    """审计清单里每只 chunk 的轻量记录（不含正文与向量，避免 JSON 膨胀）。"""

    chunk_id: str
    source: str
    page: int | None
    heading: str | None
    emb_model: str
    text_hash: str


@dataclass
class JobReport:
    """一次摄取作业的审计清单（差异化核心，可回放、可追责）。"""

    job_id: str
    started_at: datetime
    files_total: int = 0  # 本次碰到几个文件
    ingested: int = 0  # 成功入库
    dupes: int = 0  # 重复跳过
    skipped: int = 0  # 主动跳过（如扫描件）
    failed: int = 0  # 失败（下次重算）
    chunks: list[AuditChunk] = field(default_factory=list)
    skipped_files: dict[str, str] = field(default_factory=dict)  # source → 原因
    errors: dict[str, str] = field(default_factory=dict)  # source → 错误信息
