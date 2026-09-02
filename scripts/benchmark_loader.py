"""Loader 压力测试：生成 N 个文档 → 全量 load → 报告耗时/内存/单文件均耗。

用法（项目根目录）：
    python scripts/bench_loaders.py 10000

结论会记录到《项目文档.md》"大规模压测实录"。
"""

from __future__ import annotations

import sys
import time
import tracemalloc
from pathlib import Path
import tempfile

# 保证能找到 src 下的包
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from file2kg.loaders import load  # noqa: E402

# 构造一个有标题+正文的小文档（贴近真实 md，约 1KB）
SAMPLE = """# 软件手册

## 安装
前置条件：Windows 11、8GB 内存。安装包请在官网下载，双击下一步即可完成。

## 配置
### Docker 部署
docker run -p 8000:8000 myapp

> 注意端口冲突与防火墙放行。生产环境请设置环境变量后再启动。
"""


def gen_files(dir_path: Path, n: int) -> None:
    """生成 n 个微小 md 文件（内容相同不影响 load 基准结论）。"""
    for i in range(n):
        (dir_path / f"doc_{i:06d}.md").write_text(SAMPLE, encoding="utf-8")


def run(n: int) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        dir_path = Path(tmp) / "docs"
        dir_path.mkdir()

        print(f"[1/3] 生成 {n} 个测试文档 ...")
        gen_files(dir_path, n)
        files = sorted(dir_path.glob("*.md"))

        print("[2/3] 全量 load（单线程,现状实现）...")
        tracemalloc.start()
        t0 = time.perf_counter()
        docs = []
        for f in files:
            docs.extend(load(f))
        elapsed = time.perf_counter() - t0
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        print("[3/3] 报告：")
        print(f"  文件数          : {len(files):,}")
        print(f"  总耗时          : {elapsed:.2f}s")
        print(f"  单文件均耗      : {elapsed / len(files) * 1000:.2f} ms")
        print(f"  吞吐            : {len(files) / elapsed:,.0f} 文件/秒")
        print(f"  内存峰值        : {peak / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    run(n)
