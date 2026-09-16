"""日志落盘(logsetup)验收:真实写入、幂等安装、NOVEL_LOG_DIR/LEVEL 可覆盖。"""

from __future__ import annotations

import logging

import pytest

from app.core.config import reset_settings
from app.core.logsetup import _MARKER, reset_logging, setup_logging


@pytest.fixture()
def fresh_logging(tmp_path, monkeypatch):
    """隔离环境:文件 handler 摘除 + 日志目录指向 tmp。"""
    monkeypatch.setenv("NOVEL_LOG_DIR", str(tmp_path))
    reset_settings()
    reset_logging()
    yield tmp_path
    reset_logging()
    reset_settings()


def _file_handlers(logger_name="novel.agent"):
    return [h for h in logging.getLogger(logger_name).handlers
            if getattr(h, _MARKER, False)]


def test_log_written_to_file(fresh_logging):
    setup_logging()
    logging.getLogger("novel.agent").info("落盘探针 %s", "ok")
    for h in _file_handlers():
        h.flush()
    text = (fresh_logging / "novel.log").read_text(encoding="utf-8")
    assert "落盘探针 ok" in text
    assert " INFO novel.agent" in text          # 带级别与 logger 名,可检索


def test_setup_idempotent(fresh_logging):
    setup_logging()
    setup_logging()
    assert len(_file_handlers()) == 1           # 重复调用不叠加 handler


def test_uvicorn_error_channel_attached(fresh_logging):
    """服务层错误(uvicorn.error)同写一个文件——崩溃现场双通道。"""
    setup_logging()
    assert len(_file_handlers("uvicorn.error")) == 1
    fh = _file_handlers("uvicorn.error")[0]
    assert fh is _file_handlers("novel.agent")[0]   # 同一 handler,同一文件


def test_log_level_from_env(fresh_logging, monkeypatch):
    monkeypatch.setenv("NOVEL_LOG_LEVEL", "WARNING")
    reset_settings()
    setup_logging()
    assert logging.getLogger("novel.agent").level == logging.WARNING
    logging.getLogger("novel.agent").info("不应落盘")
    for h in _file_handlers():
        h.flush()
    assert "不应落盘" not in (fresh_logging / "novel.log").read_text(encoding="utf-8")
