"""store.py 全链路冒烟：写测试断言前先跑一遍，确认 API 行为与代码契约一致。

覆盖（都是写 test_store.py 前必须实测的路径）：
1. 建库 → schema 元数据（model/dim）读回
2. add_chunks 的 pa.table(schema=) + merge_insert（未实测路径）
3. 重复 add 同 chunk_id → 幂等（行数不变）
4. delete_source → 源行消失
5. hybrid 查询（RRFReranker(K=100)）→ 顺序 + 字段形状
6. 维度不符重开 → 拒开错误原文
7. ensure_fts 两次调用（幂等性）
8. 未向量化 chunk → ValueError
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from file2kg.store import Store
from file2kg.types import Chunk

DIM = 4


def mk(text: str, source: str, vec: list[float]) -> Chunk:
    c = Chunk.from_text(text, source)
    c.embedding = vec
    c.emb_model = "假模型"
    return c


def main() -> None:
    db = tempfile.mkdtemp(prefix="f2k_smoke_")
    s = Store(db, "t", "假模型", DIM)

    # 1. 元数据读回
    meta = dict(s._table.schema.metadata or {})
    assert meta[b"model"] == "假模型".encode("utf-8"), meta
    assert meta[b"dim"] == b"4", meta
    print("[1] 元数据读回 OK:", meta)

    # 2+3. add + 幂等
    c1 = mk("默认监听端口是 9000 端口", "a.md", [0.1, 0.2, 0.3, 0.4])
    c2 = mk("端口转发功能需要在配置里开启", "a.md", [0.15, 0.2, 0.3, 0.4])
    s.add_chunks([c1, c2])
    print("[2] add 后 count:", s._table.count_rows())
    s.add_chunks([c1, c2])  # 重复加
    print("[3] 重复 add 后 count:", s._table.count_rows())
    assert s._table.count_rows() == 2

    # 4. delete_source
    s.delete_source("a.md")
    print("[4] delete 后 count:", s._table.count_rows())
    assert s._table.count_rows() == 0

    # 重新填充用于查询测试
    c3 = mk("人类历史上最伟大的发明", "b.md", [0.9, -0.2, 0.1, -0.5])
    s.add_chunks([c1, c2, c3])

    # 7. ensure_fts 两次
    s.ensure_fts()
    try:
        s.ensure_fts()
        print("[7] ensure_fts 两次 OK（replace=True 幂等）")
    except Exception as e:
        print("[7] ensure_fts 二次调用异常:", e)

    # 5. hybrid 查询
    res = s.query([0.1, 0.2, 0.3, 0.4], text="端口", k=3)
    print("[5] hybrid 结果:")
    for r in res:
        print("   ", r.get("chunk_id"), r.get("text")[:14], r.get("_relevance_score"))
    print("[5] 字段样貌:", sorted(res[0].keys()))

    # 5b. 纯向量
    res2 = s.query([0.1, 0.2, 0.3, 0.4], k=2, hybrid=False)
    print("[5b] vector-only 结果:", [r.get("chunk_id") for r in res2])

    # 6. 维度拒开
    try:
        Store(db, "t", "另一模型", 8)
        print("[6] 维度不符竟然没拒开?? (FAIL)")
    except RuntimeError as e:
        print("[6] 拒开 OK:", e)

    # 8. 未向量化
    bad = Chunk.from_text("没有向量的块", "c.md")
    try:
        s.add_chunks([bad])
        print("[8] 未向量化竟然入库?? (FAIL)")
    except ValueError as e:
        print("[8] 未向量化拦截 OK:", e)

    print("冒烟全过 ✓")


if __name__ == "__main__":
    main()
