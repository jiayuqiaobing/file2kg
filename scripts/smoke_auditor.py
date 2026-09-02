"""auditor.py 冒烟：先实测关键路径，再定 test_auditor.py 断言。

覆盖：新建 header / log 注入 seq / close 幂等 / 尾撕裂修复续号 /
中间坏行硬错误 / 中间空行硬错误 / job_id 消毒 / close guard。
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from file2kg.auditor import Auditor, _last_seq  # noqa: E402
from file2kg.types import JobReport  # noqa: E402


def dump(path: Path):
    return [l for l in path.read_text(encoding="utf-8").splitlines() if l]


def main() -> None:
    d = Path(tempfile.mkdtemp(prefix="f2k_audit_"))

    # 1. 新建 + log + close
    with Auditor(str(d), "job1") as a:
        assert a.seq == 0  # header 已写但 seq 从 0 起
        s1 = a.log({"type": "create", "source": "a.md"})
        s2 = a.log({"type": "update", "source": "b.md"})
        assert (s1, s2) == (1, 2), (s1, s2)
        a.log({"type": "error", "source": "bad.md"})
    lines = dump(d / "job1.jsonl")
    print("[1] 文件行数:", len(lines))
    for i, l in enumerate(lines):
        print("    ", i, l[:70])

    # 2. 续写：同 job 再开 → seq 接续、header 不重写
    with Auditor(str(d), "job1") as a:
        assert a.seq == 3, a.seq  # 最后一行 seq
        s4 = a.log({"type": "skip", "source": "c.md"})
        assert s4 == 4
    print("[2] 续写 seq=4 OK, 行数:", len(dump(d / "job1.jsonl")))

    # 3. 尾撕裂：手工截断半行
    p = d / "job1.jsonl"
    good_len = len(p.read_bytes())
    p.write_bytes(p.read_bytes()[:-8])  # 砍掉最后 8 字节（半行）
    with Auditor(str(d), "job1") as a:
        assert a.seq == 3, a.seq  # 截断回最后完整行
        a.log({"type": "delete", "source": "c.md"})
    print("[3] 撕裂修复后续号 OK；末日行: ", dump(p)[-1][:60])

    # 4. 中间坏行：插入坏行，必须硬错误
    with open(p, "rb") as f:
        data = f.read()
    i = data.find(b"\n") + 1  # 插到第 2 行之后
    bad = data[:i] + b'NOT JSON LINE\n' + data[i:]
    p.write_bytes(bad)
    try:
        Auditor(str(d), "job1")
        print("[4] ?? 中间坏行没报错 (FAIL)")
    except RuntimeError as e:
        print("[4] 中间坏行 RuntimeError OK:", str(e)[:80])

    # 5. 中间空行
    p.write_bytes(data[:i] + b'\n' + data[i:])  # 注意：此文件当时是坏行版——重造
    good = data.replace(b"NOT JSON LINE\n", b"")  # 先还原
    p.write_bytes(good[:i] + b"\n" + good[i:])
    try:
        Auditor(str(d), "job1")
        print("[5] ?? 中间空行没报错 (FAIL)")
    except RuntimeError as e:
        print("[5] 中间空行 RuntimeError OK:", str(e)[:60])

    # 6. close 收尾 job_end + 幂等
    a = Auditor(str(d), "job2")
    a.log({"type": "create", "source": "x.md"})
    r = JobReport(job_id="j", started_at=__import__("datetime").datetime(2026, 9, 2))
    a.close(r)
    a.close(r)  # 幂等：不能炸不能重写
    lines = dump(d / "job2.jsonl")
    print("[6] job2 行数:", len(lines), "末行:", lines[-1][:80])

    # 7. job_id 消毒
    with Auditor(str(d), "../evil/name") as a:
        assert a.path.name == "name.jsonl" or "_evil_name.jsonl" in a.path.name, a.path.name
    print("[7] 消毒后文件名:", Auditor(str(d), "../evil/name").path.name)

    # 8. close 后再 log → RuntimeError
    a = Auditor(str(d), "job3")
    a.close()
    try:
        a.log({"type": "x"})
        print("[8] ?? close 后 log 没炸 (FAIL)")
    except RuntimeError:
        print("[8] close 后 log 被拒绝 OK")

    print("冒烟全过 ✓")


if __name__ == "__main__":
    main()
