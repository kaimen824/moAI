"""P0 验收:DB 可初始化、全表存在、ACL 种子符合 ADR-0006。"""

from __future__ import annotations

from app.db.database import init_db
from app.db.ddl import ALL_TABLES


def test_init_creates_all_tables(db):
    names = {
        r["name"]
        for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    missing = set(ALL_TABLES) - names
    assert not missing, f"缺表: {missing}"
    assert len(ALL_TABLES) == 19


def test_init_is_idempotent(tmp_path):
    path = tmp_path / "again.db"
    init_db(path)
    init_db(path)  # 不应抛异常


def test_wal_mode(db):
    mode = db.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


def test_acl_seed_writer_assignments(db):
    """单写者不变式(ADR-0006)。"""
    def can(agent: str, domain: str, op: str) -> int:
        row = db.execute(
            "SELECT can_read, can_write FROM agent_acl WHERE agent_name=? AND data_domain=?",
            (agent, domain),
        ).fetchone()
        assert row is not None, f"缺 ACL: {agent}/{domain}"
        return row[f"can_{op}"]

    # 事件管理:facts/beliefs/temporal_relations 写
    assert can("event_manager", "facts", "write") == 1
    assert can("event_manager", "beliefs", "write") == 1
    assert can("event_manager", "temporal_relations", "write") == 1
    # 审校:plot_threads 写,但 facts 不可写
    assert can("reviewer", "plot_threads", "write") == 1
    assert can("reviewer", "facts", "write") == 0
    assert can("reviewer", "facts", "read") == 1
    # 角色管理:characters/entities 写
    assert can("character_manager", "characters", "write") == 1
    assert can("character_manager", "entities", "write") == 1
    assert can("character_manager", "entity_links", "write") == 1
    # 大纲 Agent:只读,无任何写权限(裁判员独立性)
    for domain in ("outlines", "facts", "beliefs"):
        assert can("outline_agent", domain, "read") == 1
        assert can("outline_agent", domain, "write") == 0
    # 写作 Agent:只读 + 无大纲写权限
    assert can("writer", "facts", "read") == 1
    assert can("writer", "facts", "write") == 0
    assert can("writer", "outlines", "write") == 0
    # 主控:全域写(含大纲)
    assert can("supervisor", "outlines", "write") == 1
    assert can("supervisor", "stories", "write") == 1
    # 检索服务:只读,无写
    assert can("retrieval_service", "facts", "read") == 1
    assert can("retrieval_service", "facts", "write") == 0


def test_acl_fail_closed_for_unknown_agent(db):
    """未注册 Agent 无任何权限(fail-closed)。"""
    row = db.execute(
        "SELECT COUNT(*) AS n FROM agent_acl WHERE agent_name='stranger'"
    ).fetchone()
    assert row["n"] == 0
