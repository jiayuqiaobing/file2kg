"""直读文本类 loader：md / markdown / txt 的应对完全一样，都在这里。

一个文件 = 一个 RawDoc（标题结构留给 chunker 感知，加载阶段不掺和）。
"""

from __future__ import annotations

from pathlib import Path

from ..types import RawDoc
from .base import register

# 容错顺序：utf-8-sig（BOM 带帽）> utf-8 > gbk（国内老文档）> utf-16（最后试，最少见）
_ENCODINGS = ("utf-8-sig", "utf-8", "gbk", "utf-16")


def _read_text(path: Path) -> str:
    """逐编码尝试读文件；全部失败给出贴心报错。"""
    for enc in _ENCODINGS:
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError(
        "无法解码", path.read_bytes(), 0, 1,
        f"无法用 {'/'.join(_ENCODINGS)} 解码",
    )


@register("md")
@register("markdown")
@register("txt")
def load_text(path: Path) -> list[RawDoc]:
    """文本类格式加载：读字符串 → 装进 RawDoc（扩展名分发由 base.load 完成）。"""
    text = _read_text(path)
    return [
        RawDoc(
            text=text,
            source=str(path),
            meta={"format": path.suffix.lower().lstrip(".")},
        )
    ]
