"""Loader 包：把磁盘文件读成统一 RawDoc 的插件们。

用法：`from file2kg.loaders import load` —— 按扩展名自动分发。

分文件规则：应对方式不同才分文件。
- text.py：直读文本类（md/txt/markdown 走同一条路）
- pdf.py / docx.py ...：结构解析类，各自一个文件
"""

from .base import REGISTRY, get_loader, load
from . import text  # noqa: F401 —— 导入即注册（插件模式的标准写法）
