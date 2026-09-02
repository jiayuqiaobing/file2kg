"""cleaner.py 质检单：噪声清除、乱码判刑、低质丢弃、防误杀。

关键守护：正常正文提到"第 3 页"不能被杀（它不在独立行）。
"""

import pytest

from file2kg.cleaner import clean
from file2kg.types import RawDoc


def _doc(text: str) -> RawDoc:
    return RawDoc(text=text, source="x.md")


# ---------- 噪声清除 ----------

def test_page_line_removed():
    """独立成行的 "第 3 页 共 15 页" 必须删（正文是真实多行文档）。"""
    d = _doc("这里是软件手册的安装说明正文，内容足够长、值得保留完整性；\n"
             "第 3 页 共 15 页\n"
             "正文继续：下面是安装前置条件与网络配置的详细说明，请依次确认。")
    out = clean([d])
    assert len(out) == 1
    assert "第 3 页 共 15 页" not in out[0].text
    assert "安装说明正文" in out[0].text


def test_multi_blank_lines_compressed():
    """连续空行压缩：3 个空行变 1 个。"""
    d = _doc("第一段内容足够详实，介绍安装流程的前置条件与环境要求。"
             "\n\n\n\n\n\n第二段内容同样丰富，补充网络配置与防火墙放行说明，保证正文长度过门槛。")
    out = clean([d])
    assert "\n\n\n" not in out[0].text


def test_normal_text_untouched():  # 防误杀的核心用例
    """正常文档：一个字母都不动——cleaner 修零工不会冤杀好人。"""
    text = ("# 安装\n\n前置条件：Windows 11、8GB 内存。安装包请在官网下载，"
            "双击下一步即可完成，如有疑问参考第 3 页常见问题。")
    d = _doc(text)
    out = clean([d])
    assert out[0].text == text  # 完全一致


def test_inline_page_ref_not_removed():
    """正文里夹带的 "第 3 页" （不是一个独立行）必须保留——它是内容。"""
    d = _doc("请看第 3 页的常见问题说明，那里整理了安装过程最常见的三个错误处理方案，"
             "包括端口占用与镜像拉取失败的情况……")
    out = clean([d])
    assert "第 3 页" in out[0].text


# ---------- 乱码/低质 ----------

def test_garbled_doc_dropped():
    """乱码占比超标的整篇丢弃（这种文件的每一句都不可信）。"""
    d = _doc("����������" * 20)
    out = clean([d])
    assert out == []


def test_too_short_doc_dropped():
    """清洗后 <30 字：不值得占一个向量位。"""
    d = _doc("安装")
    out = clean([d])
    assert out == []


def test_empty_doc_dropped():
    """空文件：进来干净出去，无兜底。"""
    d = _doc("")
    out = clean([d])
    assert out == []


def test_mixed_batch_count():
    """账本真实：混一批 3 篇，2 过 1 丢，计数要能直接算。"""
    ok = _doc("足够长的正常内容" * 8)
    bad = _doc("短")
    out = clean([ok, bad, _doc("")])
    assert len(out) == 1
    assert len(out) == 3 - 2  # 说明丢弃数 = 输入-输出


# ---------- 边界与承诺（以前绕开的） ----------

def test_unchanged_text_returns_same_object():
    """零拷贝承诺：内容没变 → 返回**同一个对象**（谁改成一律 replace 就红）。"""
    d = _doc("长度足够的内容，" * 5 + "验证对象身份没有被偷偷替换成新对象。")
    out = clean([d])
    assert out[0] is d


def test_garble_ratio_exactly_5pct_survives():
    """阈值是 >5%：恰好 5%（60 字中 3 个乱码）必须存活——边界差一毫就误杀。"""
    d = _doc("好" * 57 + "�" * 3)  # 57+3=60 字，3/60 = 0.05
    out = clean([d])
    assert len(out) == 1


def test_min_text_len_exactly_30_survives():
    """门槛是 <30：恰好 30 字必须保留（29 死 30 活的边界）。"""
    d = _doc("三" * 30)
    out = clean([d])
    assert len(out) == 1


def test_english_page_line_removed():
    """英文页眉 "Page 5 of 6" 独立行也要删（IGNORECASE 的实测件）。"""
    d = _doc("正文部分内容长度足够，连续叙述完整且值得保存在知识库里。\n"
             "Page 5 of 6\n"
             "正文继续叙述，保证文档通过长度与乱码两道门槛。")
    out = clean([d])
    assert "Page 5 of 6" not in out[0].text
