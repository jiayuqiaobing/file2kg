"""Loaders 质检单：读文件对不对、编码容错、未知格式报错清晰。

覆盖契约点：md/txt/markdown 都能读；UTF-8 与 GBK 容错；
不支持的扩展名不静默装死；文件不存在报自然错误。
"""

import pytest

from file2kg.loaders import load
from file2kg.loaders.base import REGISTRY


def test_md_loaded_as_rawdoc(tmp_path):
    """utf-8 中文 md：读出正确的 RawDoc。"""
    p = tmp_path / "手册.md"
    p.write_text("# 软件手册\n\n## 安装\n", encoding="utf-8")
    docs = load(p)
    assert len(docs) == 1
    assert docs[0].text.startswith("# 软件手册")
    assert docs[0].source == str(p)
    assert docs[0].meta == {"format": "md"}


def test_txt_loaded(tmp_path):
    """txt 走同一条路。"""
    p = tmp_path / "笔记.txt"
    p.write_text("今天学 RAG", encoding="utf-8")
    docs = load(p)
    assert docs[0].text == "今天学 RAG"
    assert docs[0].meta == {"format": "txt"}


def test_gbk_fallback(tmp_path):
    """GBK 老文档也能读出来（容错的核心场景）。"""
    p = tmp_path / "旧文档.md"
    p.write_bytes("中国互联网老文档编码".encode("gbk"))
    docs = load(p)
    assert docs[0].text == "中国互联网老文档编码"


def test_unsupported_extension_raises(tmp_path):
    """不支持的格式：报错里写清支持了哪些，不静默。"""
    p = tmp_path / "乱码文件.xyz"
    p.write_text("x", encoding="utf-8")
    with pytest.raises(NotImplementedError, match="暂不支持"):
        load(p)


def test_missing_file_errors(tmp_path):
    """文件不存在：自然抛 FileNotFoundError，吞错误会死在后面。"""
    with pytest.raises(FileNotFoundError):
        load(tmp_path / "不存在.md")


def test_empty_file_still_returns_doc(tmp_path):
    """空文件：能读出（text 为空），删不删交给 cleaner 判断。"""
    p = tmp_path / "空.md"
    p.write_text("", encoding="utf-8")
    docs = load(p)
    assert docs[0].text == ""


def test_registry_has_text_extensions():
    """注册表里有 md/markdown/txt 三格。"""
    assert {"md", "markdown", "txt"} <= set(REGISTRY)


def test_get_loader_handles_uppercase(tmp_path):
    """扩展名大写 .MD 也能命中（Windows 用户常出的情况）。"""
    p = tmp_path / "手册.MD"
    p.write_text("标题", encoding="utf-8")
    docs = load(p)
    assert docs[0].text == "标题"


# ---------- 编码链其余档位（以前绕开的） ----------

def test_bom_utf8_sig_stripped(tmp_path):
    """utf-8-sig 头（Windows 记事本产物）：BOM 必须剥掉，正文开箱即净。"""
    p = tmp_path / "带帽.md"
    p.write_bytes(b"\xef\xbb\xbf" + "# BOM 标题".encode("utf-8"))
    docs = load(p)
    assert docs[0].text == "# BOM 标题"


def test_utf16_fallback(tmp_path):
    """容错链最后一档：utf-16 也能读出来。"""
    p = tmp_path / "老系统.md"
    p.write_bytes("十六位元编码确实能用".encode("utf-16"))
    docs = load(p)
    assert docs[0].text == "十六位元编码确实能用"


def test_undecodable_reports_encodings(tmp_path):
    """四编码全挂：报错要诚实列出试过什么（否则用户只能猜）。"""
    p = tmp_path / "顽皮.md"
    p.write_bytes(b"\xc3\x28\x00")  # utf-8/gbk/utf-16 全部拒绝的字节
    with pytest.raises(UnicodeDecodeError, match="无法用 utf-8-sig/utf-8/gbk/utf-16 解码"):
        load(p)
