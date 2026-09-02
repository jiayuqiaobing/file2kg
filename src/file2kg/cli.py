"""File2kg：文件 → 知识库的摄取 CLI（v0.1 用户入口）。

命令：
  file2kg ingest <docs-dir>       摄取目录（增量，mtime+hash 双层判定）
  file2kg query "问题文本"         检索（hybrid 向量+关键词+RRF）

约定（CLI 设计成熟做法，2026-09-02 网搜）：
- stdout = 数据、stderr = UI（进度/错误全走 rich Console(stderr)）
- 退出码：0=成功 / 1=用户输入错 / 2=运行失败（内部异常不吞，打印指引）
- API key 不暴露在命令行（shell history 泄漏）——只走环境变量 FILE2KG_API_KEY
- 命令体只做"解析输入 + 渲染输出"，领域逻辑全部在 ingest/store/embedder/auditor
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

# Windows 终端默认 GBK，与 Python UTF-8 输出冲突（实测 file2kg --help 中文乱码）
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass  # 非实子端/重定向时可能不可 reconfig，忽略即可

from .auditor import Auditor
from .config import DEFAULT_AUDIT_DIR, DEFAULT_DB_DIR, DEFAULT_TABLE
from .embedder import Embedder
from .ingest import ingest as run_ingest, _ERROR_LIMIT
from .store import Store
from .types import Chunk

app = typer.Typer(help="文件 → 知识库：本地摄取、增量同步、混合检索", no_args_is_help=True)
err = Console(stderr=True)


def _fail(message: str, code: int = 1) -> None:
    """用户可见错误：单段说明 + 修复指引；不打印 traceback（系统异常已保留在审计里）。

    注意：必须用 SystemExit 而不是 typer.Exit——后者是 RuntimeError 子类，
    会被下面自己的 except RuntimeError 捕获（实测踩坑：exit 1 被吞成 exit 2）。
    """
    err.print(f"[red]错误:[/red] {message}")
    raise SystemExit(code)


@app.command()
def ingest(
    docs: Annotated[Path, typer.Argument(exists=True, dir_okay=True, file_okay=False,
                                         help="待摄取的文档目录（递归扫描）")],
    db: Annotated[str, typer.Option(help="向量库目录")] = DEFAULT_DB_DIR,
    table: Annotated[str, typer.Option(help="库内表名（一库一模）")] = DEFAULT_TABLE,
    model: Annotated[Optional[str], typer.Option(help="嵌入模型（默认 Qwen3-Embedding-0.6B，本地加载需下载）")] = None,
    chunk_size: Annotated[int, typer.Option(min=64, help="分块目标大小（token）")] = 512,
    overlap: Annotated[int, typer.Option(min=0, help="块间重叠（token）")] = 64,
    window: Annotated[int, typer.Option(min=1, help="批窗口：攒多少 chunk 写一次库")] = 32,
    force: Annotated[bool, typer.Option(help="忽略增量状态，全量重挂（换模型/参数后必须）")] = False,
    api_key: Annotated[Optional[str], typer.Option(envvar="FILE2KG_API_KEY",
                                                   help="API key；设置后走 API 模式（免下载，需 OPENAI 兼容端点）")] = None,
    api_url: Annotated[Optional[str], typer.Option(help="API 端点（默认阿里云 DashScope 兼容端点）")] = None,
    audit: Annotated[str, typer.Option(help="审计日志目录")] = DEFAULT_AUDIT_DIR,
) -> None:
    """把目录里所有支持的文件摄取进向量库（可重复运行：只处理变化与新增）。"""
    try:
        report, events = run_ingest(
            docs_dir=str(docs),
            db_dir=db,
            table_name=table,
            model=model,
            chunk_size=chunk_size,
            overlap=overlap,
            window=window,
            force=force,
            api_key=api_key,
            api_url=api_url,
            state_file=None,  # 默认 <db_dir>/state.json
        )
    except ValueError as e:  # 用户输入错：目录不存在等
        _fail(str(e), code=1)
    except Exception as e:  # 运行失败：库/网络/模型
        err.print(f"[red]运行失败:[/red] {e}——原因已记录，可用审计作业定位")
        raise SystemExit(2) from e

    with Auditor(audit, report.job_id) as a:
        for ev in events:
            a.log(ev)
        a.close(report)

    table_view = Table(title="作业报告", show_header=False)
    table_view.add_row("作业号", report.job_id)
    table_view.add_row("文件数", str(report.files_total))
    table_view.add_row("新摄/更新", str(report.ingested))
    table_view.add_row("跳过", str(report.dupes))
    table_view.add_row("失败", str(report.failed) + (f"（连败上限 {_ERROR_LIMIT}，--force 可重试）" if report.failed else ""))
    table_view.add_row("chunk 数", str(len(report.chunks)))
    for src, why in report.skipped_files.items():
        table_view.add_row("丢弃", f"{src}: {why}")
    table_view.add_row("审计文件", str(a.path))
    err.print(table_view)


@app.command()
def query(
    text: Annotated[str, typer.Argument(help="查询文本（会先嵌入成向量，再混合检索）")],
    db: Annotated[str, typer.Option(help="向量库目录")] = DEFAULT_DB_DIR,
    table: Annotated[str, typer.Option(help="库内表名")] = DEFAULT_TABLE,
    model: Annotated[Optional[str], typer.Option(help="嵌入模型（必须与建库时一致）")] = None,
    k: Annotated[int, typer.Option(min=1, max=50, help="返回条数")] = 10,
    hybrid: Annotated[bool, typer.Option(help="混合检索（向量+关键词+RRF）；--no-hybrid 纯向量")] = True,
    api_key: Annotated[Optional[str], typer.Option(envvar="FILE2KG_API_KEY",
                                                   help="API key（与建库时同一配置）")] = None,
    api_url: Annotated[Optional[str], typer.Option(help="API 端点")] = None,
) -> None:
    """检索知识库：返回最相关的 chunk（带来源与相似度）。"""
    try:
        import lancedb  # 先探测表存在：Store 构造会"库不存在就建"，query 不该造空表

        if not Path(db).exists():  # 目录都没有 → OSError 不是 ValueError，须先查
            _fail("库目录还没有创建——先运行 file2kg ingest", code=1)
        try:
            lancedb.connect(db).open_table(table)
        except ValueError:
            _fail("库里还没有表——先运行 file2kg ingest", code=1)
        embedder = Embedder(model=model, api_key=api_key, api_url=api_url)
        q = [Chunk.from_text(text, "query")]
        embedder.embed(q)
        store = Store(db, table, embedder.model_name, dim=len(q[0].embedding))
        results = store.query(q[0].embedding, text=text if hybrid else None, k=k, hybrid=hybrid)
    except NotImplementedError as e:  # FTS 缺失（hybrid 需索引）
        _fail(f"{e}（跑一次 ingest 会自动建 FTS 索引）", code=1)
    except RuntimeError as e:  # 模型/维度/库绑定问题 → 给指引
        _fail(str(e), code=2)
    except ValueError as e:
        _fail(str(e), code=1)
    except Exception as e:
        err.print(f"[red]查询失败:[/red] {e}")
        raise SystemExit(2) from e

    if not results:
        err.print("[yellow]没有命中。[red] 库可能为空或还没建 FTS 索引（跑一次 ingest 会自动建）[/red]")
        return
    out = Table(title=f"top {len(results)}", show_header=True)
    out.add_column("score", justify="right", style="cyan")
    out.add_column("source", style="green")
    out.add_column("text", max_width=70)  # 非 TTY 也不至于撑爆行宽
    for r in results:
        out.add_row(f"{r.get('_relevance_score', 0.0):.4f}", r["source"], r["text"])
    err.print(out)  # 数据表格走 stderr：进度/警告/结果都在一个屏幕


if __name__ == "__main__":
    app(prog_name="file2kg")  # console_scripts 入口照常指向 cli:app
