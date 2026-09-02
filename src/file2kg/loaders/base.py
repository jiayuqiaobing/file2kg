"""Loader 插件地基：扩展名注册表 + 统一入口。

新增格式的步骤（比如以后加 docx）：
1. 复制一个 text.py 风格的插件文件
2. 用 @register("docx") 挂上
3. 在 __init__.py 里 import 它
完毕——上层代码一行不用改。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..types import RawDoc

# 加载函数签名：给一个路径，返回该文件切出的 RawDoc 列表
LoaderFn = Callable[[Path], list[RawDoc]]

# 扩展名（不带点，全小写）→ 加载函数
REGISTRY: dict[str, LoaderFn] = {}


def register(ext: str) -> Callable[[LoaderFn], LoaderFn]:
    """装饰器：把加载函数登记到扩展名上。"""

    def wrap(fn: LoaderFn) -> LoaderFn:
        REGISTRY[ext.lower()] = fn
        return fn

    return wrap


def get_loader(path: Path) -> LoaderFn:
    """按扩展名找 loader；不支持就报清楚——支持什么、你在哪。"""
    ext = path.suffix.lower().lstrip(".")
    if ext not in REGISTRY:
        raise NotImplementedError(
            f"暂不支持 .{ext} 文件；已支持: {sorted(REGISTRY)}"
        )
    return REGISTRY[ext]


def load(path: Path) -> list[RawDoc]:
    """统一入口：任何支持的格式都从这儿走。"""
    return get_loader(path)(path)
