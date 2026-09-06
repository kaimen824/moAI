"""测试夹具:临时数据库、隔离的 Settings。"""

from __future__ import annotations

import pytest

from app.core.config import reset_settings
from app.db.database import init_db


@pytest.fixture()
def db(tmp_path):
    """每个测试一个全新初始化的数据库(含 ACL 种子)。"""
    conn = init_db(tmp_path / "test.db")
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    """隔离环境变量与单例,防止宿主机 .env 泄入测试。"""
    monkeypatch.delenv("GLM_API_KEY", raising=False)
    monkeypatch.delenv("NOVEL_DB_PATH", raising=False)
    for key in list(__import__("os").environ):
        if key.startswith("MODEL__"):
            monkeypatch.delenv(key, raising=False)
    reset_settings()
    yield
    reset_settings()
