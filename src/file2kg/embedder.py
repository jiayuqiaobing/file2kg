"""Embedder：Chunk → 向量（管道第 ④ 环，唯一碰模型的重活）。

三支柱之"懒惰"落地：
- 懒加载：import 零成本；构造零成本；首次 embed() 才碰模型/网络
- 本地/API 双模式：本地 = sentence-transformers + Qwen3-Embedding-0.6B（默认，零 API key）；
  API = OpenAI 兼容端点（传 api_key 即切换），供无 GPU/懒部署场景
- 一库一模：一次作业固定一个模型名，emb_model 写进每个 chunk（跨库混用会炸语义空间）

避坑经验（开工前网搜核实，2026-09-02）：
1. CPU 批量建议 8-16，32+ 可能因 cache miss 更慢 → 默认 16
2. sentence-transformers 3.0+ 不显式 device 就**静默全程 CPU** → 显式 cuda/cpu
3. 本地 encode 显式 normalize_embeddings=True（漏掉=余弦排序静默坏了）
4. TEI 之类端点默认归一化行为不一致 → API 返回后自行 L2 归一化（幂等无害）
5. show_progress_bar 非交互环境静默坏 → 显式 False，进度交给 CLI 层
6. 单句循环 pattern 返回 1D 数组列表 → 统一批量、统一转 list
7. macOS SDPA NaN（eager 可解）——本项目跑 Windows，仅留档
8. 长文本（>4096 token）模型静默截断——管道有 chunker 兜底（块 ~870 字符）
"""

from __future__ import annotations

from .types import Chunk

_MODEL_NAME = "Qwen/Qwen3-Embedding-0.6B"  # 默认本地模型（1024 维，以实际输出为准）
_BATCH_SIZE = 16  # CPU 推荐区间 8-16（避坑 #1）；GPU 用户可显式传大
_DEFAULT_API_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"  # OpenAI 兼容


def _l2_normalize(vec: list[float]) -> list[float]:
    """按 L2 归一化（幂等：已归一化的向量再归一化结果不变）。"""
    norm = sum(x * x for x in vec) ** 0.5
    return [x / norm for x in vec] if norm else vec


class Embedder:
    """一次作业一个实例。后端句柄懒创建：_backend 为 None 即"还没碰过模型"。"""

    def __init__(
        self,
        model: str | None = None,
        batch_size: int = _BATCH_SIZE,
        api_key: str | None = None,
        api_url: str | None = None,
    ) -> None:
        self.model_name = model or _MODEL_NAME
        self.batch_size = batch_size
        self.api_key = api_key
        self.api_url = api_url or _DEFAULT_API_URL
        self._mode = "api" if api_key else "local"  # 显式判断，不靠猜
        self._backend = None  # None = 未加载（懒加载哨兵）

    @property
    def is_loaded(self) -> bool:
        """模型是否已加载常驻（懒加载哨兵是否已被填充）。只读、无副作用。

        查询它 MUST NOT 触发加载。serve 的 warm 口径由它回答——服务层不得自持标志位：
        自持的标志位可以撒谎，"预热非默认"就没人能证伪了。
        """
        return self._backend is not None

    # ---------- 对外接口 ----------

    def embed(self, chunks: list[Chunk]) -> list[Chunk]:
        """给一片 Chunk 补上 embedding 与 emb_model；就地修改并返回同一列表。

        空列表直接返回，不触发加载（file2kg --help 永远不碰模型）。
        """
        if not chunks:
            return chunks
        self._ensure_backend()
        texts = [c.text for c in chunks]
        if self._mode == "local":
            vectors = self._backend.encode(
                texts,
                batch_size=self.batch_size,
                normalize_embeddings=True,  # 避坑 #3
                show_progress_bar=False,  # 避坑 #5
            )
        else:
            vectors = self._embed_api(texts)
        for c, v in zip(chunks, vectors):
            c.embedding = v.tolist() if hasattr(v, "tolist") else list(v)  # np/list 双兼容
            c.emb_model = self.model_name  # 一库一模标签
        return chunks

    # ---------- 后端装载（懒） ----------

    def _ensure_backend(self) -> None:
        if self._backend is not None:
            return
        self._backend = self._load_api() if self._mode == "api" else self._load_local()

    def _load_local(self):
        # 延迟 import=加载成本后移；torch 随 sentence-transformers 一起进
        import torch  # noqa: PLC0415
        from sentence_transformers import SentenceTransformer

        # 实战记录：缓存命中但直连 HF 时会 NET 重试（实测 120s+ 空耗）；
        # 设 HF_HUB_OFFLINE=1 后 9.9s 完成——CLI 层对"已下载"场景推荐设置
        # （首次下载无缓存时不要设，需要真网）
        try:
            # 避坑 #2：不显式 device 会静默全程 CPU（哪怕有 GPU）
            device = "cuda" if torch.cuda.is_available() else "cpu"
            return SentenceTransformer(self.model_name, device=device)
        except OSError as e:
            raise RuntimeError(
                f"模型 {self.model_name} 下载/加载失败。大陆网络请先设置环境变量 "
                f"HF_ENDPOINT=https://hf-mirror.com 后重试（模型首次使用需下载几百 MB）。"
                f"原始原因: {e}"
            ) from e

    def _load_api(self):
        try:
            from openai import OpenAI
        except ImportError as e:
            raise RuntimeError("API 模式需要 openai 包：pip install openai") from e
        client = OpenAI(base_url=self.api_url, api_key=self.api_key)
        # precheck：真实调用一次拿维度——维度以实际输出为准（诚实，不硬编码）
        resp = client.embeddings.create(model=self.model_name, input="维度自检")
        self._dim = len(resp.data[0].embedding)
        return client

    def _embed_api(self, texts: list[str]) -> list[list[float]]:
        """API 分批嵌入 + 统一 L2 归一化（避坑 #4：端点默认归一化不一致）。"""
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            resp = self._backend.embeddings.create(model=self.model_name, input=batch)
            out.extend(_l2_normalize(list(d.embedding)) for d in resp.data)
        return out
