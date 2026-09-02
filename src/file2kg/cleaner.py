"""Cleaner：规则去噪 —— 垃圾进垃圾出的第一防线。

对 RawDoc 做纯规则清洗（零 LLM、零成本、可复现）：
1. 删页眉页脚/页码等噪声行
2. 压缩连续空行
3. 乱码检测：替换字符（U+FFFD）比例过高 → 整篇丢弃
4. 低质门槛：清洗后过短 → 丢弃

设计：纯函数，输入 list[RawDoc]、输出保留的 list[RawDoc]。
丢弃数量 = len(输入) - len(输出)，由 ingest 层负责记账。
"""

from __future__ import annotations

import re
from dataclasses import replace

from .types import RawDoc

# ---------- 规则（模块级常量 = 配置的"家"，config.py 建好后从这里对接） ----------

# 独立成行的页码/页眉页脚，如 "第 3 页"、"第 3 页 共 15 页"、"Page 3 of 4"
_PAGE_LINE_RE = re.compile(
    r"^\s*(?:第\s*\d+\s*页(?:\s*共\s*\d+\s*页)?|page\s*\d+\s*(?:of\s*\d+)?)\s*$",
    re.IGNORECASE,
)
# 替换字符 U+FFFD —— 乱码解码的典型产物
_GARBLE_CHARS_RE = re.compile(r"�")
# 压缩连续空行：3 行及以上 → 1 个空行
_MULTI_BLANK_RE = re.compile(r"\n{3,}")

_MAX_GARBLE_RATIO = 0.05  # 乱码字符占比超过 5% 判整篇乱码
_MIN_TEXT_LEN = 30  # 清洗后少于 30 字判低质


def clean(docs: list[RawDoc]) -> list[RawDoc]:
    """清洗一批 RawDoc，返回合格者。数量输出 = 保留者，丢弃数算账在 ingest。"""
    result: list[RawDoc] = []
    for doc in docs:
        cleaned = _clean_text(doc.text)
        if cleaned is None:  # None = 判死刑（乱码/低质）
            continue
        if cleaned != doc.text:  # 内容变了才新建，没变零拷贝（少建对象）
            result.append(replace(doc, text=cleaned))
        else:
            result.append(doc)
    return result


def _clean_text(text: str) -> str | None:
    """单篇清洗：返回净化后文本；乱码或低质返回 None（丢弃）。"""
    lines = [
        line for line in text.splitlines() if not _PAGE_LINE_RE.match(line)
    ]
    cleaned = _MULTI_BLANK_RE.sub("\n\n", "\n".join(lines)).strip()

    # 乱码检测：整篇判刑（这种文件全篇不可信，不是局部能救的）
    if len(cleaned) > 0 and (
        len(_GARBLE_CHARS_RE.findall(cleaned)) / len(cleaned) > _MAX_GARBLE_RATIO
    ):
        return None
    # 低质门槛：太短的信息不值得浪费一个向量位
    if len(cleaned) < _MIN_TEXT_LEN:
        return None
    return cleaned
