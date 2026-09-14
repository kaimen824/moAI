"""Schema 迁移版本化(ADR-0021):三路径升级 + 幂等 + 失败回滚。

- 全新库:baseline 建全表,增量版本探测后全跳过;
- 存量库(无版本表):baseline 幂等重放补缺表,增量版本补列,数据保真;
- 半迁移中断库:版本记录缺失即重跑,探测幂等保证不炸;
- 迁移失败:单事务回滚,版本记录不写,下次启动自动重试。
"""

from __future__ import annotations

import sqlite3

import pytest

from app.db import ddl
from app.db.database import init_db


def _open(path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _versions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT version, name, applied_at FROM schema_migrations ORDER BY version"
    ).fetchall()


def test_fresh_db_records_baseline_and_migrations(db):
    rows = _versions(db)
    assert [r["version"] for r in rows] == [1, 2]
    assert rows[0]["name"] == "baseline" and rows[1]["name"] == "legacy_backfill"
    assert all(r["applied_at"] for r in rows)


def test_reinit_is_idempotent(tmp_path):
    path = tmp_path / "again.db"
    init_db(path)
    first = _versions(init_db(path))
    second = _versions(init_db(path))
    assert [(r["version"], r["name"]) for r in first] == \
           [(r["version"], r["name"]) for r in second] == \
           [(1, "baseline"), (2, "legacy_backfill")]


def test_legacy_db_upgrade_preserves_data(tmp_path):
    """v1 之前的存量库:无版本表 + entities 缺新列 -> 补列、保数据、补版本记录。"""
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE stories (id TEXT PRIMARY KEY, title TEXT NOT NULL, premise TEXT,
          status TEXT NOT NULL DEFAULT 'draft', main_branch_id TEXT,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE entities (
          id TEXT PRIMARY KEY, story_id TEXT NOT NULL, type TEXT NOT NULL,
          name TEXT NOT NULL, content TEXT, embedding BLOB,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        INSERT INTO entities VALUES ('e1','s1','faction','旧门派','旧条目',NULL,
          '2026-01-01','2026-01-01');
    """)
    conn.commit()
    conn.close()

    conn = init_db(path)
    rows = _versions(conn)
    assert [r["version"] for r in rows] == [1, 2]   # 存量库标记 baseline 后补增量
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(entities)")}
    assert {"chapter_no", "status"} <= cols
    row = conn.execute("SELECT status, chapter_no, name FROM entities WHERE id='e1'").fetchone()
    assert row["status"] == "active" and row["chapter_no"] is None   # 既有数据保真
    conn.close()


def test_partial_migration_resumes(tmp_path, monkeypatch):
    """迁移中断:v2 执行到一半崩溃 -> DDL 整体回滚(SQLite DDL 事务性),
    版本记录不写;重跑该版本完整执行(探测幂等兜外部工具半改库的场景)。"""
    path = tmp_path / "half.db"
    # 先造一个 v1 之前的存量老库(entities 缺新列)
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE entities (
          id TEXT PRIMARY KEY, story_id TEXT NOT NULL, type TEXT NOT NULL,
          name TEXT NOT NULL, content TEXT, embedding BLOB,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    """)
    conn.commit()
    conn.close()

    # v2 只补一半就炸:chapter_no 成功,status 失败
    def _half_v2(conn):
        tables = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "entities" in tables:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(entities)")}
            if "chapter_no" not in cols:
                conn.execute("ALTER TABLE entities ADD COLUMN chapter_no INTEGER")
        raise RuntimeError("模拟迁移中断")

    monkeypatch.setattr(ddl, "MIGRATIONS", [(2, "half", _half_v2)])
    with pytest.raises(RuntimeError):
        init_db(path)

    conn = _open(path)
    assert {r["version"] for r in _versions(conn)} == {1}   # v2 记录未写
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(entities)")}
    assert "chapter_no" not in cols   # 半成品 DDL 已随事务回滚
    conn.close()

    # 恢复正常迁移重跑:完整执行,两列补齐(自愈)
    monkeypatch.undo()
    conn = init_db(path)
    assert {r["version"] for r in _versions(conn)} == {1, 2}
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(entities)")}
    assert {"chapter_no", "status"} <= cols
    conn.close()


def test_failed_migration_rolls_back(tmp_path, monkeypatch):
    """迁移失败:该版本单事务回滚、版本记录不写;修复后同库继续升级(自愈)。"""
    def _broken_v3(conn):
        conn.execute("CREATE TABLE tmp_rollback_probe (id INTEGER)")   # 应被回滚
        conn.execute("THIS IS NOT SQL")   # 必炸

    real_m2 = (2, "legacy_backfill", ddl._m2_legacy_backfill)
    monkeypatch.setattr(ddl, "MIGRATIONS", [real_m2, (3, "broken", _broken_v3)])
    path = tmp_path / "boom.db"
    with pytest.raises(Exception):
        init_db(path)

    conn = _open(path)
    versions = {r["version"] for r in _versions(conn)}
    assert versions == {1, 2}   # v3 记录未写
    probe = conn.execute(
        "SELECT name FROM sqlite_master WHERE name='tmp_rollback_probe'").fetchone()
    assert probe is None   # 事务内半成品被回滚
    conn.close()

    # 发布修复版 v3 后重跑:同库补上该版本(自愈)
    monkeypatch.setattr(ddl, "MIGRATIONS", [real_m2, (3, "fixed", lambda c: None)])
    conn = init_db(path)
    assert {r["version"] for r in _versions(conn)} == {1, 2, 3}
    conn.close()


def test_future_migration_applies_on_existing_db(db, monkeypatch):
    """已升级的库注册新版本 -> 只跑新版本(增量语义)。"""

    def _v4(conn):
        conn.execute("ALTER TABLE stories ADD COLUMN _probe_v4 INTEGER")

    monkeypatch.setattr(ddl, "MIGRATIONS",
                        [(2, "legacy_backfill", ddl._m2_legacy_backfill),
                         (4, "probe_v4", _v4)])
    ddl.migrate(db)   # 已初始化的库上重跑:只补新版本(等价重启升级)
    applied = {r["version"]: r["name"] for r in _versions(db)}
    assert applied[2] == "legacy_backfill" and applied[4] == "probe_v4"
    assert {r["name"] for r in db.execute("PRAGMA table_info(stories)")} >= {"_probe_v4"}
