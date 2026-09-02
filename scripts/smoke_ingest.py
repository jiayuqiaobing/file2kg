"""ingest.py 全链路冒烟：全部状态机分支实测一遍，再定 test_ingest.py 断言。

不碰模型：直接把 ingest 模块里的 Embedder 换成假后端（dim=4 固定向量）。
覆盖：首次摄入 / 全跳过 / 修改先删后增 / 源删除僵尸防线 / mtime 误报 /
坏文件 error 计数 / 超限跳过 / 指纹 warning / 空目录。
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import file2kg.ingest as ing
from file2kg.ingest import ingest

INGESTED_TEXT = "这是安装文档的说明内容。第二句用于凑长度。第三句把字数加够。第四句重复填充。" * 4  # >30 字


class FakeEmbedder:
    def __init__(self, **kw):
        self.model_name = kw.get("model") or "Qwen/Qwen3-Embedding-0.6B"

    def embed(self, chunks):
        for c in chunks:
            c.embedding = [0.1, 0.2, 0.3, 0.4]
            c.emb_model = self.model_name
        return chunks


ing.Embedder = FakeEmbedder  # 模块内替换生效（from .embedder import Embedder 绑定在本模块）

def show(tag, report, events, db_dir, table="docs"):
    import lancedb
    db = lancedb.connect(db_dir)
    print(f"\n### {tag}")
    print(f"  report: 文件{report.files_total} 摄入{report.ingested} 跳过{report.dupes} 失败{report.failed} 块{len(report.chunks)}")
    print(f"  events: {[e['type'] + ':' + e.get('source', '') for e in events]}")
    tables = [t for t in db.list_tables().tables]
    if table in tables:
        t = db.open_table(table)
        print(f"  库行数: {t.count_rows()}, sources: {t.to_arrow().column('source').to_pylist()}")


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="f2k_ingest_"))
    docs = tmp / "docs"; docs.mkdir()
    db = tmp / "db"; db.mkdir()
    (docs / "a.md").write_text(INGESTED_TEXT, encoding="utf-8")
    (docs / "b.md").write_text("# 标题\n端口配置说明。第二句。第三句。第四句。第五句。第六句。" * 2, encoding="utf-8")
    (docs / "bad.md").write_bytes(b"\xc3\x28\x00")  # 四编码全挂的坏文件

    r, ev = ingest(str(docs), str(db))
    show("首次（a+b 摄入、bad 失败）", r, ev, db)

    r, ev = ingest(str(docs), str(db))
    show("二次运行（应该全 skip 或 bad 再失败一次）", r, ev, db)

    (docs / "a.md").write_text(INGESTED_TEXT + "更新后才有的新句子。往右边续写。", encoding="utf-8")
    r, ev = ingest(str(docs), str(db))
    show("修改 a.md（update：先删后增）", r, ev, db)

    (docs / "b.md").unlink()
    r, ev = ingest(str(docs), str(db))
    show("删除 b.md（僵尸防线 delete）", r, ev, db)

    old = (docs / "a.md").stat().st_mtime
    os.utime(docs / "a.md", (old + 5, old + 5))  # 内容没变、mtime 变了
    r, ev = ingest(str(docs), str(db))
    show("touch a.md（mtime 误报 → hash 相同 skip）", r, ev, db)

    r, ev = ingest(str(docs), str(db))
    show("第 3 次跑 bad.md（error_count 到 3）", r, ev, db)
    r, ev = ingest(str(docs), str(db))
    show("第 4 次跑 bad.md（超限 → 不读直接跳过）", r, ev, db)

    r, ev = ingest(str(docs), str(db), chunk_size=256)
    show("改 chunk_size（指纹 warning）", r, ev, db)

    empty = tmp / "empty"; empty.mkdir()
    r, ev = ingest(str(empty), str(db))
    show("空目录（无事发生）", r, ev, db)


if __name__ == "__main__":
    main()
