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
    assert len(ALL_TABLES) == 28   # 23(ADR-0021)+users/story_members(ADR-0022)+llm_failures(ADR-0026)+story_run_state(ADR-0027)+chat_messages(ADR-0031)


def test_init_is_idempotent(tmp_path):
    path = tmp_path / "again.db"
    init_db(path)
    init_db(path)  # 不应抛异常


def test_init_migrates_legacy_entity_tables(tmp_path):
    """老库升级:entities 无 status/chapter_no 列时,init_db 补列+补索引不炸。

    回归:no such column: status——SCHEMA_SQL 中引用新列的索引在老库上
    (表已存在被跳过、列还不存在)曾导致启动失败。
    """
    path = tmp_path / "legacy.db"
    import sqlite3
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE stories (id TEXT PRIMARY KEY, title TEXT NOT NULL, premise TEXT,
          status TEXT NOT NULL DEFAULT 'draft', main_branch_id TEXT,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE entities (
          id TEXT PRIMARY KEY, story_id TEXT NOT NULL, type TEXT NOT NULL,
          name TEXT NOT NULL, content TEXT, embedding BLOB,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE entity_links (
          id TEXT PRIMARY KEY, story_id TEXT NOT NULL, from_entity TEXT NOT NULL,
          to_entity TEXT NOT NULL, relation TEXT);
        INSERT INTO entities VALUES ('e1','s1','faction','旧门派','旧条目',NULL,
          '2026-01-01','2026-01-01');
    """)
    conn.commit()
    conn.close()

    conn = init_db(path)   # 老库上重建:迁移补列,不再 no such column
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(entities)")}
    assert {"status", "chapter_no"} <= cols
    idx = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='entities'")}
    assert "idx_entities_story" in idx
    row = conn.execute("SELECT status, chapter_no, name FROM entities WHERE id='e1'").fetchone()
    assert row["status"] == "active" and row["chapter_no"] is None   # 既有数据保真
    conn.close()


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
    # 伏笔评审:plot_threads 写(ADR-0020 自审校移交,单写者不变式不变)
    assert can("thread_reviewer", "plot_threads", "write") == 1
    assert can("reviewer", "plot_threads", "write") == 0
    assert can("reviewer", "facts", "write") == 0
    assert can("reviewer", "facts", "read") == 1
    # 角色管理:characters 写(ADR-0015 起实体族移交 entity_manager,单写者)
    assert can("character_manager", "characters", "write") == 1
    assert can("character_manager", "entities", "read") == 1
    assert can("character_manager", "entities", "write") == 0
    # 实体管理:实体族全域写(ADR-0015)
    assert can("entity_manager", "entities", "write") == 1
    assert can("entity_manager", "entity_links", "write") == 1
    assert can("entity_manager", "entity_aliases", "write") == 1
    assert can("entity_manager", "entity_merge_proposals", "write") == 1
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
