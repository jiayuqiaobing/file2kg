"""cli.py 质检单：命令整合、退出码、审计联动。

约定：注入假 Embedder 不碰模型，库/状态/审计全走 tmp_path；
断言富文本只看关键词子串（rich 标记精确匹配是已知坑，不做）。
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from file2kg.cli import app

TEXT_A = "这是安装文档的说明内容。第二句用于凑长度。第三句把字数加够。第四句重复填充。" * 4


@pytest.fixture
def fake_embedder(monkeypatch):
    class _Fake:
        def __init__(self, **kw):
            self.model_name = kw.get("model") or "Qwen/Qwen3-Embedding-0.6B"

        def embed(self, chunks):
            for c in chunks:
                c.embedding = [0.1, 0.2, 0.3, 0.4]
                c.emb_model = self.model_name
            return chunks

    monkeypatch.setattr("file2kg.ingest.Embedder", _Fake)
    monkeypatch.setattr("file2kg.cli.Embedder", _Fake)
    return _Fake


@pytest.fixture
def docs(tmp_path):
    d = tmp_path / "docs"
    d.mkdir()
    (d / "a.md").write_text(TEXT_A, encoding="utf-8")
    return d


def _runner() -> CliRunner:
    return CliRunner()


# ---------- help ----------

def test_help_lists_commands():
    result = _runner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "ingest" in result.output
    assert "query" in result.output


# ---------- ingest 成功路径 ----------

def test_ingest_end_to_end(docs, tmp_path, fake_embedder):
    db = str(tmp_path / "db")
    result = _runner().invoke(app, ["ingest", str(docs), "--db", db, "--audit", str(tmp_path / "audit")])
    assert result.exit_code == 0, result.output
    assert "作业号" in result.output
    assert "新摄/更新" in result.output and "1" in result.output
    # 审计文件真实生成了
    audits = list((tmp_path / "audit").glob("*.jsonl"))
    assert audits and audits[0].read_text(encoding="utf-8").count("job_end") == 1


def test_ingest_second_run_shows_skip(docs, tmp_path, fake_embedder):
    db = str(tmp_path / "db")
    r = _runner()
    assert r.invoke(app, ["ingest", str(docs), "--db", db]).exit_code == 0
    result = r.invoke(app, ["ingest", str(docs), "--db", db])
    assert result.exit_code == 0
    assert "跳过" in result.output  # 增量：第二次全 skip


def test_ingest_missing_dir_fails_with_guide(tmp_path):
    """docs 不存在：typer 层 exists=True 提前拦截（消息为 click 英文，行为为先拒绝）。"""
    result = _runner().invoke(app, ["ingest", str(tmp_path / "nope")])
    assert result.exit_code == 2  # typer 参数校验失败（click 约定）
    assert "does not exist" in result.output.lower() or "不存在" in result.output


# ---------- query ----------

def test_query_returns_hits(docs, tmp_path, fake_embedder):
    db = str(tmp_path / "db")
    r = _runner()
    assert r.invoke(app, ["ingest", str(docs), "--db", db, "--audit", str(tmp_path / "audit")]).exit_code == 0
    result = r.invoke(app, ["query", "端口", "--db", db])
    assert result.exit_code == 0, result.output
    assert "top" in result.output
    assert "a.md" in result.output


def test_query_empty_db_guides_to_ingest(tmp_path, fake_embedder):
    result = _runner().invoke(app, ["query", "任何问题", "--db", str(tmp_path / "db")])
    assert result.exit_code == 1
    assert "先运行" in result.output


# ---------- 配置 ----------

def test_cli_config_defaults_align(docs, tmp_path, fake_embedder):
    """CLI 默认值与 IngestConfig 默认值同源（config.py 是默认值的家）。"""
    from file2kg.config import DEFAULT_DB_DIR, DEFAULT_TABLE, IngestConfig

    assert DEFAULT_DB_DIR == "file2kg-db"
    cfg = IngestConfig(docs_dir=str(docs))
    assert cfg.table_name == DEFAULT_TABLE
    assert cfg.chunk_size == 512 and cfg.overlap == 64 and cfg.window == 32
    assert cfg.force is False and cfg.api_key is None and cfg.api_url is None
