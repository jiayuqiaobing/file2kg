"""config.py 质检单：先把"最安全默认值"钉死。

这些断言不是在测代码，是在**测宪法**——ServeConfig 的三个默认值分别对应
原则 I（只绑回环）、原则 II（预热非默认）、原则 III（写能力默认不存在）。
谁把它们改了，这里就必须红。
"""

import pytest

from file2kg.config import DEFAULT_SERVE_HOST, ServeConfig


# ---------- 宪法默认值（改这些等于修宪） ----------

def test_default_mode_is_read_only():
    """宪法原则 III（NON-NEGOTIABLE）：写能力默认不存在。"""
    cfg = ServeConfig()
    assert cfg.mode == "read-only"
    assert cfg.allow_write is False


def test_default_preload_is_off():
    """宪法原则 II：预热不是默认副作用。"""
    assert ServeConfig().preload is False


def test_default_host_is_loopback():
    """宪法原则 I：默认不出本机。"""
    cfg = ServeConfig()
    assert cfg.host == DEFAULT_SERVE_HOST
    assert cfg.host == "127.0.0.1"


def test_default_db_and_table_match_cli():
    """与 CLI 同一套默认库/表——同一条检索路径，不引入第二套口径。"""
    cfg = ServeConfig()
    assert cfg.db_dir == "file2kg-db"
    assert cfg.table_name == "docs"


# ---------- 显式开启（配对：证明默认值不是硬编码死的） ----------

def test_explicit_read_write_flips_allow_write():
    """显式写模式：allow_write 翻转。缺了这条，一个恒 False 的实现会假通过上面所有断言。"""
    assert ServeConfig(mode="read-write").allow_write is True


def test_explicit_preload_flips():
    """显式预热：True（同样是上面"默认关"的配对项）。"""
    assert ServeConfig(preload=True).preload is True


# ---------- 构造期校验（fail fast，不留到服务起来一半） ----------

def test_invalid_mode_rejected():
    with pytest.raises(ValueError, match="mode"):
        ServeConfig(mode="readonly")  # 少个连字符也不行


@pytest.mark.parametrize("bad_port", [0, -1, 65536, 99999])
def test_invalid_port_rejected(bad_port):
    with pytest.raises(ValueError, match="port"):
        ServeConfig(port=bad_port)


def test_api_key_defaults_to_none():
    """密钥默认不存在——API 模式是显式后门，不是默认路径（原则 I）。"""
    assert ServeConfig().api_key is None
