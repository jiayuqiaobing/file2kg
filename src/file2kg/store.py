"""Store：LanceDB 写读层（管道第 ⑤ 环 + 对外提供查询口）。

薄接口 5 个：
- open：库不存在→建（schema 元数据记 model/dim + chunk_id 标量索引）；
  库存在→**维度自检**（前车之鉴 9.2：维度不符拒开，绝不哑开）
- add_chunks：Chunk → Arrow，merge_insert(on=chunk_id) 幂等 upsert（重跑不重复入库）
- delete_source：源文件"内容变了"时的旧块清退
- ensure_fts：作业末建/重建中文 FTS 索引（hybrid 的前置）
- query：统一混合检索（向量 + 关键词 + RRF(K=100)）

lancedb 0.38.0 实测结论（写代码前逐条探过，防文档印象偏差）：
- hybrid 必须 search(query_type='hybrid') + 显式 .vector().text()（把 query 塞 search() 会抛
  ValueError"two ways"）；RRFReranker 参数是大写 K（k=100 直接 TypeError）
- create_table 不收 metadata → 模型名/维度记在 pa.schema.with_metadata（已实测可读回）
- merge_insert 必须先有 chunk_id 索引（否则全表扫描、>10000 行还会 HTTP 400）
- 索引统一走 create_index(config=BTree()/FTS())——老 API（create_scalar_index /
  create_fts_index）0.25 起标记弃用，本模块全用新 API（另：language='Chinese' 实测不支持）
- delete 谓词里字符串字面量必须单引号（双引号被解析成列名：No field named ...）
"""

from __future__ import annotations

import pyarrow as pa

import lancedb
from lancedb.index import BTree, FTS
from lancedb.rerankers import RRFReranker

from .types import Chunk


def _fts_config() -> FTS:
    """中文 FTS 配置：0.38 无 jieba（缺语言模型目录，记 v0.2），ngram(2,2) 平替；
    language='Chinese' 实测不支持（lang 白名单里没有），ngram 不分词故不受影响。"""
    return FTS(base_tokenizer="ngram", ngram_min_length=2, ngram_max_length=2)


def _chunks_to_data(chunks: list[Chunk]) -> dict:
    """Chunk 列表 → Arrow 数据（不走 pandas——传 nullable 错配会崩，实锤）。"""
    if any(c.embedding is None for c in chunks):
        raise ValueError("存在未向量化的 chunk——管道顺序：chunker → embedder → store")
    return {
        "chunk_id": [c.chunk_id for c in chunks],
        "text": [c.text for c in chunks],
        "source": [c.source for c in chunks],
        "heading": [c.heading for c in chunks],
        "page": [c.page for c in chunks],
        "emb_model": [c.emb_model for c in chunks],
        "text_hash": [c.text_hash for c in chunks],
        "vector": [c.embedding for c in chunks],
    }


class Store:
    """一个 LanceDB 库读写器。一库一模：open 时校验绑定模型维度。"""

    def __init__(
        self,
        db_dir: str,
        table_name: str,
        model_name: str,
        dim: int | None = None,  # 模型维度；库已存在时可省略（从库元数据读，如"仅删除"场景）
    ) -> None:
        self._db = lancedb.connect(db_dir)
        self.table_name = table_name
        self.model_name = model_name
        self.dim = dim
        self._table = self._open_or_create(table_name, model_name, dim)

    # ---------- 建/开 ----------

    def _open_or_create(self, name: str, model_name: str, dim: int):
        # 用 open_table 探测替代 list_tables()——后者 0.38 返回分页对象（.tables 列表），
        # "t in list_tables()" 永远 False（实测坑）；try/except 无分页语义负担
        try:
            t = self._db.open_table(name)
            if dim is None:  # 仅删除/清理场景：维度从库元数据读（一库一模的记账）
                meta = dict(t.schema.metadata or {})
                dim = int(meta.get(b"dim", b"0"))
            self._assert_dim(t, name, model_name, dim)  # 拒开，不哑开
            return t
        except ValueError:
            pass  # 表不存在 → 走新建
        if dim is None:
            raise ValueError("建新库必须给 dim（模型实际向量维度）")
        schema = self._make_schema(model_name, dim)
        t = self._db.create_table(name, schema=schema)
        t.create_index("chunk_id", config=BTree())  # merge_insert 的前提（实测坑）
        return t

    @staticmethod
    def _make_schema(model_name: str, dim: int) -> pa.Schema:
        fields = pa.schema([
            ("chunk_id", pa.string()),
            ("text", pa.string()),
            ("source", pa.string()),
            ("heading", pa.string()),
            ("page", pa.int32()),
            ("emb_model", pa.string()),
            ("text_hash", pa.string()),
            ("vector", pa.list_(pa.float32(), dim)),
        ])
        return fields.with_metadata({"model": model_name, "dim": str(dim)})

    @staticmethod
    def _assert_dim(t, name: str, model_name: str, dim: int) -> None:
        """前车之鉴 9.2：打开即自检维度；不符抛错，把两边维度/模型名都说清楚。"""
        meta = dict(t.schema.metadata or {})
        old_model = meta.get(b"model", b"?") or b"?"
        table_dim = t.schema.field("vector").type.list_size
        if table_dim != dim:
            raise RuntimeError(
                f"库 '{name}' 由 {old_model.decode()}({table_dim}维) 建立，"
                f"当前模型 {model_name}({dim}维)——不兼容！请换回原模型或重建库"
            )

    # ---------- 写 ----------

    def add_chunks(self, chunks: list[Chunk]) -> None:
        """幂等追加：同 chunk_id 覆盖（断点重跑、重复摄入都不会产生重复行）。"""
        if not chunks:
            return
        # 按表 schema 组表：list[float] 默认会被推断成 float64，这里显式对齐 float32 列宽
        data = pa.table(_chunks_to_data(chunks), schema=self._table.schema)
        self._table.merge_insert("chunk_id").when_matched_update_all().when_not_matched_insert_all().execute(data)

    def delete_source(self, source: str) -> None:
        """清退某个源文件的所有旧块（文件变更后旧内容不再留在库里）。"""
        # 实测（2026-09-02）：谓词里字符串字面量必须用单引号——
        # 双引号会被解析成标识符（"No field named ..."），单引号才是字符串值
        where = "source = '" + source.replace("'", "''") + "'"
        self._table.delete(where)

    def ensure_fts(self) -> None:
        """建/重建中文 FTS 索引（create_index 默认 replace=True，重建=幂等；作业末调用一次）。"""
        self._table.create_index("text", config=_fts_config())

    # ---------- 查 ----------

    def query(self, vector: list[float], text: str | None = None, k: int = 10, hybrid: bool = True) -> list[dict]:
        """检索：hybrid=True 用向量+关键词+RRF；False 纯向量。返回行 dict 列表。"""
        q = self._table.search
        if hybrid:
            if text is None:
                raise ValueError("hybrid 检索必须同时给向量与关键词（FTS 缺路会 NotImplementedError）")
            results = (
                q(query_type="hybrid")
                .vector(vector)
                .text(text)
                .rerank(RRFReranker(K=100))  # 实测：参数是大写 K
                .limit(k)
                .to_list()
            )
        else:
            results = q(vector).limit(k).to_list()
        return results
