"""serve：常驻服务（HTTP API + MCP），模型常驻内存消除冷启动。

依赖是**可选 extra**：`pip install "file2kg[serve]"`。
本包 MUST NOT 被顶层 import——`cli.py` 里延迟导入，缺依赖时给一行安装指引而不是 traceback
（宪法原则 II：import 零成本）。
"""

from .app import Service, build_service, run

__all__ = ["Service", "build_service", "run"]
