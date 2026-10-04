"""serve/app.py：服务组装点——一处构造 Embedder/Store/能力清单，产出**可测的服务对象**。

`build_service()` 是所有测试的入口（plan.md §①② 的断言都从这里进）。把它单独放一个
模块，是为了让"组装"独立于"路由定义"——否则测试要么起真服务，要么够不着内部状态。

薄壳原则（用户技术指引 #1）：本模块**不复制、不重写**任何管道逻辑，只做装配。
唯一触碰核心模块的地方是给 `ingest()` 传常驻 Embedder（research.md C1）。

实测依据（scripts/smoke_fastmcp.py，2026-10-04，fastmcp 4.0.10）：
- Starlette 1.x 无 `on_startup`/装饰器路由，一律用 `routes=` / `exception_handlers=` 构造参数
- MCP 挂载用 `http_app(path="/")` + `Mount("/mcp")`，且 **lifespan 必接**（US2 落实）
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

import lancedb

from ..config import ServeConfig
from ..embedder import Embedder
from ..store import Store
from ..types import Chunk
from .http_api import build_app, scrub
from .mcp_tools import build_mcp

log = logging.getLogger("file2kg.serve")

READ_CAPABILITIES: tuple[str, ...] = ("service_info", "search")
WRITE_CAPABILITIES: tuple[str, ...] = ("ingest",)

_PRELOAD_PROBE = "internal:preload"  # 预热用的假来源名，结果丢弃、不入库


def _service_id() -> str:
    """服务标识取自**包元数据**，不硬编码——省得 pyproject 改了它不知道。"""
    try:
        from importlib.metadata import version as pkg_version  # noqa: PLC0415

        return f"file2kg/{pkg_version('file2kg')}"
    except Exception:  # noqa: BLE001 — 直接跑源码（未安装）时的兜底
        return "file2kg/dev"


@dataclass
class Service:
    """一个常驻服务实例的全部状态。

    **能力清单在 `build_service()` 里构造一次，此后不可变**——权限模式只能重启变更。
    这是"写能力默认不存在"可被静态断言的前提（data-model.md §2.2）。
    """

    config: ServeConfig
    embedder: Embedder
    store: Store
    model: str  # 库**绑定**的模型名（来自库元数据，不是进程配置）
    dim: int  # 库的向量维度（来自库元数据）
    capabilities: tuple[str, ...]
    app: object = None  # Starlette app（类型放宽，避免测试期强依赖 starlette）
    mcp: object | None = None  # US2 挂载后填入
    _dim_checked: bool = field(default=False, repr=False)
    _write_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def allow_write(self) -> bool:
        return self.config.allow_write

    def acquire_write(self) -> bool:
        """尝试占用写权（**非阻塞**）：同一时刻至多一个作业。

        `auditor.py` 的设计前提是"单写者 + 只追加"；两个作业并发写同一库/审计会破坏它
        （宪法原则 IV）。所以第二个请求直接被拒，而不是排队——排队会让调用方
        以为作业已经开跑。
        """
        return self._write_lock.acquire(blocking=False)

    def release_write(self) -> None:
        self._write_lock.release()

    def has(self, capability: str) -> bool:
        """能力是否存在。只读模式下 `has("ingest")` 恒为 False。"""
        return capability in self.capabilities

    def descriptor(self) -> dict:
        """服务自述（FR-015 / data-model.md §2.1）。

        `warm` MUST 由 `Embedder` 的真实加载状态回答——服务不得自持标志位，
        否则"预热非默认"没人能证伪（research.md C5）。
        """
        return {
            "service": _service_id(),
            "db_dir": str(self.config.db_dir),
            "table": self.config.table_name,
            "model": self.model,
            "dim": self.dim,
            "mode": self.config.mode,
            "warm": self.embedder.is_loaded,
            "capabilities": list(self.capabilities),
        }


def _open_existing_table(config: ServeConfig):
    """库存在性预检——照抄 `cli.py:124-129` 的既有写法。

    `Store.__init__` 在表不存在时会**建表**；服务若直接构造 `Store` 会静默建一个空库，
    违反 spec 假设「库需先存在」（research.md C2）。
    """
    if not Path(config.db_dir).exists():
        raise RuntimeError(
            f"库目录还没有创建：{config.db_dir}——先运行 file2kg ingest 建库"
        )
    try:
        return lancedb.connect(config.db_dir).open_table(config.table_name)
    except ValueError as e:
        raise RuntimeError(
            f"库里还没有表 '{config.table_name}'——先运行 file2kg ingest 建库"
        ) from e


def _read_store_metadata(table) -> tuple[str, int]:
    """读库元数据里的 (模型名, 维度)——一库一模的记账。不碰模型。"""
    raw = dict(table.schema.metadata or {})

    def get(key: str) -> str:
        v = raw.get(key.encode()) or raw.get(key) or b""
        return v.decode("utf-8") if isinstance(v, bytes) else str(v)

    return get("model"), int(get("dim") or 0)


def build_service(config: ServeConfig) -> Service:
    """装配一个服务实例。失败即抛出（不静默、不半启动）。"""
    table = _open_existing_table(config)
    store_model, store_dim = _read_store_metadata(table)

    # 一库一模（**启动期只比对模型名**，不加载模型——否则 warm 一开始就是 True，
    # 破坏"预热非默认"）。维度校验留到首次真正嵌入时（research.md C3）：
    # Store(dim=None) 只检查"元数据 vs schema"自洽，**不验证调用方**。
    if config.model is not None and config.model != store_model:
        # FR-014 要求"同时说清两边"。库这边的名字与维度都拿得到；**请求那边的维度拿不到**
        # ——除非真去加载它，而那会让 warm 一开始就是 True，破坏原则 II。所以只报其名字：
        # 名字不同这件事本身已经足够作为拒绝理由（语义空间不互通）。
        raise RuntimeError(
            f"库 '{config.table_name}' 由 {store_model}({store_dim}维) 建立，"
            f"当前请求的模型是 {config.model}——两者语义空间不互通，不兼容！"
            f"请换回原模型，或用 --force 重建库"
        )
    model_name = config.model or store_model

    embedder = Embedder(model=model_name, api_key=config.api_key, api_url=config.api_url)
    store = Store(config.db_dir, config.table_name, model_name, dim=None)

    capabilities = READ_CAPABILITIES + (WRITE_CAPABILITIES if config.allow_write else ())
    service = Service(
        config=config,
        embedder=embedder,
        store=store,
        model=model_name,
        dim=store_dim,
        capabilities=capabilities,
    )

    if config.preload:
        _preload(service)

    service.mcp = build_mcp(service)  # 必须先于 build_app：父 app 要挂它
    service.app = build_app(service)
    return service


def _preload(service: Service) -> None:
    """显式预热：用一次性 Chunk 触发加载，结果丢弃、不入库、不写审计。

    失败**不得**导致启动失败，也**不得**谎报 warm=True —— 如实报告原因与镜像指引，
    服务以懒加载方式继续（spec Edge Case）。
    """
    try:
        service.embedder.embed([Chunk.from_text(_PRELOAD_PROBE, _PRELOAD_PROBE)])
        log.info("预热完成：模型已常驻（warm=True）")
    except Exception as e:  # noqa: BLE001 — 预热失败是可选路径失败，不该拖垮启动
        log.warning(
            "预热失败，服务将以懒加载方式继续（首次检索会慢一次）。"
            "大陆网络可设 HF_ENDPOINT=https://hf-mirror.com 后重试。原因: %s",
            scrub(f"{type(e).__name__}: {e}"),
        )


def run(service: Service, *, host: str | None = None, port: int | None = None) -> None:
    """单进程跑起来（阻塞）。用 `uvicorn.Server` 形式以便控制生命周期与干净退出。"""
    import uvicorn  # 延迟导入：不带 serve 依赖时不该在这里炸

    cfg = uvicorn.Config(
        service.app,
        host=host or service.config.host,
        port=port or service.config.port,
        log_level="warning",
    )
    uvicorn.Server(cfg).run()
