"""Chunker：标题感知切分 + 面包屑注入 + 句子边界保护（管道第 ③ 环）。

RawDoc 进、Chunk 出。三步：
1. 标题感知：按 md 标题层级把文档切成"节"，面包屑（标题链）随节生成
   —— 这就是 Anthropic Contextual Retrieval 的规则版：每块自带"它在哪"的上下文
2. 节内句子级切分：把文本切成"句子原子块"（句号/换行为边界）
3. 贪心合并：句子原子块合并到目标长度，切点只会落在句子边界；相邻块重叠尾部

token 计数说明：不引入 tokenizer（那是模型的事）；中文平均 1 token ≈ 1.7 字符，
用字符近似做"软件目标值"——够准、零依赖、可复现。
"""

from __future__ import annotations

import re
from pathlib import Path

from .types import Chunk, RawDoc

# ---------- 规则常量（config.py 建好后由 ingest 传入，此处为默认值） ----------

_CHARS_PER_TOKEN = 1.7  # 中文近似：1 token ≈ 1.7 字符
_DEFAULT_CHUNK_SIZE = 512  # token
_DEFAULT_OVERLAP = 64  # token，"防切点"的保险带

_HEADING_RE = re.compile(r"^(#{1,4})\s*(.+?)\s*$")  # md 标题行
_SENT_RE = re.compile(r"[^。！？\n]+[。！？]?")  # 句子原子（中文标点或换行收尾）


def _split_sections(text: str) -> list[tuple[str, str]]:
    """按标题切节，返回 [(面包屑 "安装 > 前置条件", 节文本)]。无标题 → [("", 全文)]。"""
    sections: list[tuple[str, str]] = []
    crumbs: list[str] = []  # 标题栈
    buf: list[str] = []

    for line in text.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            depth = len(m.group(1))
            title = m.group(2).strip()
            if any(buf):
                sections.append((" > ".join(crumbs), "\n".join(buf)))
                buf = []
            crumbs = crumbs[: depth - 1] + [title]
        elif line.strip() or buf:
            buf.append(line)

    if any(buf):
        sections.append((" > ".join(crumbs), "\n".join(buf)))
    return sections or [("", text)]


def _split_sentences(text: str) -> list[str]:
    """切成句子原子块（整句，不切半句）。"""
    return [s.strip() for s in _SENT_RE.findall(text) if s.strip()]


_ASCII_TO_CJK_RE = re.compile(r"([A-Za-z])([一-鿿])")  # "是AI的" 里的 "I是"
_CJK_TO_ASCII_RE = re.compile(r"([一-鿿])([A-Za-z])")  # "学Python" 里的 "学P"
# 注意：数字排除——"第87句" 是正常惯用表达，数字+汉字没有单字化问题


def _space_zh_en(text: str) -> str:
    """中英边界补空格——必须在**文本级**做（单句内部也生效）。

    避坑经验：'AI是改变世界' 会被分词器切碎成单字、向量失真；
    'AI 是改变世界' 才是模型认识的原生形态。全角标点/括号不触发（保守）。
    """
    return _ASCII_TO_CJK_RE.sub(r"\1 \2", _CJK_TO_ASCII_RE.sub(r"\1 \2", text))


def _greedy_chunk(sents: list[str], target: int, overlap: int) -> list[str]:
    """句子贪心合并：超 target 就切；新块以旧块尾部若干**完整句子**开头（overlap）。

    注意：overlap 按整句取（凑到 >= overlap 字符为止）。
    不能用任意字符切片——那会把句子劈成两半，背叛"句子边界保护"的承诺。
    """
    chunks: list[str] = []
    cur_sents: list[str] = []
    cur_len = 0
    for s in sents:
        if cur_sents and cur_len + len(s) > target:
            chunks.append("".join(cur_sents))
            # 凑 overlap：从尾部往上收集完整句子，凑够字符数为止。
            # overlap=0 时整段跳过——禁止从满切块里顺走任何一句。
            tail: list[str] = []
            if overlap:
                acc = 0
                for x in reversed(cur_sents):
                    tail.append(x)
                    acc += len(x)
                    if acc >= overlap:
                        break
            cur_sents = list(reversed(tail)) + [s]
            cur_len = sum(len(x) for x in cur_sents)
        else:
            cur_sents.append(s)
            cur_len += len(s)
    if cur_sents:
        chunks.append("".join(cur_sents))
    return chunks


def chunk(
    docs: list[RawDoc],
    chunk_size: int = _DEFAULT_CHUNK_SIZE,
    overlap: int = _DEFAULT_OVERLAP,
) -> list[Chunk]:
    """主入口：RawDoc 列表 → Chunk 列表（containing 前缀 + 整句切分）。"""
    target = int(chunk_size * _CHARS_PER_TOKEN)
    overlap_chars = int(overlap * _CHARS_PER_TOKEN)
    out: list[Chunk] = []

    for doc in docs:
        fallback = Path(doc.source).stem  # 无标题文档用文件名当面包屑
        for breadcrumb, sec_text in _split_sections(_space_zh_en(doc.text)):
            heading = breadcrumb or fallback
            prefix = f"[Topic: {heading}]"
            for body in _greedy_chunk(_split_sentences(sec_text), target, overlap_chars):
                out.append(
                    Chunk.from_text(
                        text=f"{prefix} {body}",
                        source=doc.source,
                        heading=heading,
                        page=doc.page,
                    )
                )
    return out
