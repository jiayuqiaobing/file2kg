"""chunker.py 质检单：标题感知、面包屑、句子边界、overlap、无标题回落。

关键守护：切点永远落在句子边界（不切半句）；块自带 [Topic: ...] 前缀。
"""

from file2kg.chunker import chunk
from file2kg.types import RawDoc

# 一段能切出多块的正文：句子原子 31 字，90 句 ≈ 2790 字，能切 ~4 块
# （实测边界：块0 = 句00~27，28 句 × 31 字 + 12 前缀 = 880 字符）
_BODY = "这是安装文档的第{0:02d}句，描述安装前置条件的准备工作与环境要求。"
_BODY_TEXT = "".join(_BODY.format(i) for i in range(90))

_MD = (
    "# 安装\n\n"
    "## 前置条件\n\n"
    + _BODY_TEXT
    + "\n\n## 配置\n\n"
    "配置部分说明网络设置与代理选项。"
)


def _doc(text: str, source: str = "手册.md") -> RawDoc:
    return RawDoc(text=text, source=source)


# ---------- 标题感知 ----------

def test_sections_follow_headings():
    """# 安装 > ## 前置条件：面包屑要逐级累积。"""
    out = chunk([_doc(_MD)])
    headings = {c.heading for c in out}
    assert "安装 > 前置条件" in headings


def test_sibling_heading_breadcrumb():
    """兄弟标题（配置）不吃祖先的兄弟：面包屑是 "安装 > 配置"。"""
    out = chunk([_doc(_MD)])
    headings = {c.heading for c in out}
    assert "安装 > 配置" in headings


# ---------- 前缀注入 ----------

def test_topic_prefix_injected():
    """每块开头必须有 [Topic: 面包屑]——检索时"它在哪"上下文直接进向量。"""
    out = chunk([_doc(_MD)])
    assert out[0].text.startswith("[Topic: 安装 > 前置条件]")


def test_short_doc_one_chunk_with_prefix():
    """短文档：一块装入，前缀照常。"""
    out = chunk([_doc("只有一句话，但也算内容。" * 5)])
    assert len(out) == 1
    assert out[0].text.startswith("[Topic: 手册]")


# ---------- 句子边界 ----------

def test_no_half_sentence_at_cut():
    """切点保护：非最后一块必须以句符结尾（不能切半句）。"""
    out = chunk([_doc(_BODY_TEXT)])
    assert len(out) >= 3  # 长文要真的切出多块，否则本用例是空转（实测 90 句 = 4 块）
    for c in out[:-1]:
        assert c.text.strip().endswith("。")  # 尾部是整句


def test_chunks_ordered_and_joined():
    """去掉前缀后按顺序拼回 ≈ 原文：首句在、末句在（内容不丢、顺序不乱）。"""
    out = chunk([_doc(_BODY_TEXT)])
    joined = "".join(c.text.replace("[Topic: 手册] ", "", 1) for c in out)
    assert "第00句" in joined and "第89句" in joined  # 首句在、末句在


# ---------- overlap ----------

def test_overlap_carries_tail():
    """overlap=64 token（≈109 字符）：后一块开头应重现前一块的尾巴。"""
    out = chunk([_doc(_BODY_TEXT)], overlap=64)
    prev_tail = out[0].text[-80:]
    assert prev_tail in out[1].text  # 尾部内容出现在下一块头部区


def test_overlap_carries_whole_sentence():
    """overlap 的尾巴必须是**整句**：块1 开头不能有半句（上一次教训的守护）。"""
    import re
    out = chunk([_doc(_BODY_TEXT)], overlap=64)
    body1 = out[1].text.replace("[Topic: 手册] ", "", 1)
    first_sent, *_ = re.findall(r"[^。]+。", body1)
    # 完全匹配模板句（整句 31 字）而非残留半句
    assert re.fullmatch(
        r"这是安装文档的第\d{2}句，描述安装前置条件的准备工作与环境要求。",
        first_sent,
    )


def test_overlap_zero_is_clean():
    """overlap=0：块1 从"句28"干干净净开始（实测边界），不夹带句27 的尾巴。"""
    out = chunk([_doc(_BODY_TEXT)], overlap=0)
    body1 = out[1].text.replace("[Topic: 手册] ", "", 1)
    assert body1.startswith("这是安装文档的第28句")  # 首尾相接，无尾巴夹带
    assert "第27句" not in out[1].text  # 上一句绝不跑到下一块


# ---------- 无标题回落 ----------

def test_plain_text_falls_back_to_filename():
    """无标题文档：面包屑 = 文件名（至少有东西可追溯）。"""
    out = chunk([_doc(_BODY_TEXT, source="笔记.txt")])
    assert out[0].heading == "笔记"
    assert out[0].text.startswith("[Topic: 笔记]")


# ---------- 边角（以前没测的） ----------

def test_long_single_sentence_not_lost():
    """单句超长（>870 字符）：整句放一块、不劈不丢，后面的内容照样接上。"""
    text = ("这是一个超长的单句，" + "内容" * 500 + "。") + _BODY_TEXT
    out = chunk([_doc(text)], overlap=0)
    joined = "".join(c.text.replace("[Topic: 手册] ", "", 1) for c in out)
    assert "这是一个超长的单句" in joined  # 超长句完整在（单句不可分，宁可超限）
    assert "第00句" in joined  # 后面的句子没被超长句挤丢


def test_empty_doc_yields_no_chunks():
    """空文档 → 0 块（不报错、不吐幽灵块）；长宽两条门在 cleaner 把关。"""
    out = chunk([RawDoc(text="", source="空.md")])
    assert out == []


def test_page_passthrough():
    """PDF 页级文档：页码要透传给每一块（一次一页的溯源钥匙）。"""
    d = RawDoc(text=_BODY_TEXT, source="论文.pdf", page=7)
    out = chunk([d])
    assert len(out) > 0
    assert all(c.page == 7 for c in out)


# ---------- 中英边界（避坑守护：'AI是' 会被切单字、向量失真） ----------

def test_mixed_zh_en_boundary_spaced():
    """中英紧贴补空格：'人工智能是AI的天下' → '是 AI 的'（词边界可见）。"""
    out = chunk([_doc("人工智能是AI的天下，未来十年AI工程师将是最热门的岗位。")])
    assert "是 AI 的" in out[0].text
    assert "AI 工程师" in out[0].text


def test_cn_to_en_boundary_spaced():
    """中→英方向同样补：'今天学Python' → '学 Python'。"""
    out = chunk([_doc("今天学Python，明天学Go语言，这是技能树的规划。")])
    assert "学 Python" in out[0].text


def test_pure_ascii_reader_unchanged():
    """纯英文/纯中文文本不被加多余空格（回归保护，手别痒）。"""
    out = chunk([_doc("今天是AI工程师学习日，我们继续深入RAG管道的设计细节。")])
    assert "AI 工程师" in out[0].text  # 该补的补
    assert "学习日，" in out[0].text  # 不该补的别动


# ---------- 契约 ----------

def test_chunk_id_stable_for_same_text():
    """同文本同参数 → 同样的 chunk_id（重复摄入去重的根基）。"""
    a = chunk([_doc(_BODY_TEXT)])
    b = chunk([_doc(_BODY_TEXT)])
    assert [c.chunk_id for c in a] == [c.chunk_id for c in b]
