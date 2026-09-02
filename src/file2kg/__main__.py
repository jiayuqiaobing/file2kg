"""python -m file2kg 入口（等价于 file2kg 命令）。"""

from .cli import app

if __name__ == "__main__":
    app(prog_name="file2kg")
