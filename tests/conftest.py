"""测试公共配置：让 pytest 能找到 src 下的 file2kg 包。"""

import sys
from pathlib import Path

# 项目根目录（tests/ 的上一级）下的 src 加入模块搜索路径
SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))
