"""并发与运行完整性(ADR-0023,评审 6.2/6.3)验收。

- active run 互斥:同 story 双击 generate/resume,第二个 409;
- 事件归属:节点内 emit 经运行上下文(ContextVar)归属各自 run,不再串台;
- active chapter 唯一约束:同 story 同章号仅一条 active(v4 迁移含存量去重)。
"""

from __future__ import annotations

import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient

import app.main as main
import tests.test_graph_e2e as replay
from app.core.llm.facade import LLMFacade
from app.graph.build import build_graph
from app.graph.runtime import Deps, build_engine, run_ctx
from tests.test_api import login


@pytest.fixture()
def client(tmp_path):
    facade = LLMFacade(response_override=replay.override)
    deps, conn = build_engine(tmp_path / "conc.db", llm=facade)
    main.install_engine(deps, conn, build_graph(deps, checkpointer=deps.checkpointer))
    with TestClient(main.app) as c:
        c.headers.update({"Authorization": f"Bearer {login(c)}"})
        yield c, deps
    main._engine, main._graph = None, None
    with main._active_lock:
        main._active.clear()


def test_emit_scoped_by_run_context(db):
    """两个"run"各自 set 运行上下文:无显式 thread_id 的 emit 归属各自 thread,
    事件体带 run_id——多 story 并发事件不再串台(评审 6.3)。"""
    deps = Deps(conn=db, repo=None, retrieval=None, llm=None)   # type: ignore[arg-type]
    deps.subscribe("story-A")
    deps.subscribe("story-B")

    def run_a():
        run_ctx.set(("story-A", "run-a"))
        deps.emit("token", {"text": "A"})          # 无显式 thread_id
        deps.emit("note", {"text": "A2"}, "story-A")  # 显式

    def run_b():
        run_ctx.set(("story-B", "run-b"))
        deps.emit("token", {"text": "B"})

    ta, tb = threading.Thread(target=run_a), threading.Thread(target=run_b)
    ta.start(); tb.start(); ta.join(); tb.join()

    snap_a = deps.snapshot("story-A")
    snap_b = deps.snapshot("story-B")
    assert [d["text"] for k, d in snap_a if k in ("token", "note")] == ["A", "A2"]
    assert [d["text"] for k, d in snap_b if k == "token"] == ["B"]
    # 事件体带 run_id(emit 统一注入)
    assert [d for k, d in snap_a if k == "token"][0]["run_id"] == "run-a"
    assert [d for k, d in snap_b if k == "token"][0]["run_id"] == "run-b"


def test_emit_without_context_has_empty_thread(db):
    """不在任何 run 上下文中(如管理端点误 emit):事件 thread 为空串,不误归属。"""
    deps = Deps(conn=db, repo=None, retrieval=None, llm=None)   # type: ignore[arg-type]
    deps.emit("orphan", {})
    assert deps.snapshot("story-X") == []
    assert deps.snapshot("") != []   # 留在空 thread 下,不出现在任何 story


def test_generate_rejected_when_story_already_active(client):
    """同 story 已有 active run 时,第二次 generate 409(评审 6.2 互斥)。"""
    c, deps = client
    sid = c.post("/stories", json={"title": "互斥测试"}).json()["story_id"]
    with main._active_lock:
        main._active.add(sid)   # 模拟运行中的第一个 run
    try:
        r = c.post(f"/stories/{sid}/generate", json={"target_chapters": 1})
        assert r.status_code == 409
        assert c.post(f"/stories/{sid}/resume",
                      json={"action": "confirm"}).status_code == 409
    finally:
        with main._active_lock:
            main._active.discard(sid)


def test_concurrent_resume_second_gets_409(client):
    """真并发:两个线程同时触发 resume,恰好一个成功一个 409。"""
    c, deps = client
    sid = c.post("/stories", json={"title": "并发测试", "premise": "并发"}).json()["story_id"]
    from tests.test_api import parse_sse

    def sse_post(url, payload):
        r = c.post(url, json=payload)
        return r.status_code

    # 先推进到总大纲确认中断(worker 在后台跑到 interrupt)
    assert c.post(f"/stories/{sid}/generate",
                  json={"target_chapters": 1, "initial_input": "并发奇幻"}).status_code == 200

    barrier = threading.Barrier(2)
    results: list[int] = []

    def do_resume():
        barrier.wait()
        results.append(sse_post(f"/stories/{sid}/resume", {"action": "confirm"}))

    t1 = threading.Thread(target=do_resume)
    t2 = threading.Thread(target=do_resume)
    t1.start(); t2.start(); t1.join(); t2.join()
    assert sorted(results) == [200, 409], results


def test_duplicate_active_chapter_rejected(db):
    """同 story 同章号第二条 active 章节:唯一约束硬拒绝(评审 6.2 数据闸)。"""
    db.execute(
        "INSERT INTO stories (id, title, status, created_at, updated_at)"
        " VALUES ('s1', '书', 'active', '2026-01-01', '2026-01-01')")
    db.execute(
        "INSERT INTO branches (id, story_id, kind, status, created_at)"
        " VALUES ('b1', 's1', 'main', 'active', '2026-01-01')")
    db.execute(
        "INSERT INTO chapters (id, story_id, chapter_no, version_no, status,"
        " branch_id, created_at, updated_at)"
        " VALUES ('c1', 's1', 1, 1, 'active', 'b1', '2026-01-01', '2026-01-01')")
    db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO chapters (id, story_id, chapter_no, version_no, status,"
            " branch_id, created_at, updated_at)"
            " VALUES ('c2', 's1', 1, 1, 'active', 'b1', '2026-01-02', '2026-01-02')")


def test_v4_migration_dedupes_legacy_active_chapters(tmp_path):
    """存量库重复 active 章节:v4 迁移保留最新一条,其余 archived(数据不丢)。"""
    from app.db.database import init_db

    path = tmp_path / "legacy_dupe.db"
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE stories (id TEXT PRIMARY KEY, title TEXT NOT NULL, premise TEXT,
          status TEXT NOT NULL DEFAULT 'draft', main_branch_id TEXT,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE chapters (id TEXT PRIMARY KEY, story_id TEXT NOT NULL,
          chapter_no INTEGER NOT NULL, version_no INTEGER NOT NULL DEFAULT 1,
          prev_version_id TEXT, title TEXT, content TEXT,
          status TEXT NOT NULL DEFAULT 'draft', branch_id TEXT NOT NULL,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        INSERT INTO stories VALUES ('s1', '旧书', NULL, 'active', NULL, '2026-01-01', '2026-01-01');
        INSERT INTO chapters VALUES ('old1', 's1', 1, 1, NULL, NULL, '旧稿', 'active', 'b1', '2026-01-01', '2026-01-01');
        INSERT INTO chapters VALUES ('old2', 's1', 1, 1, NULL, NULL, '新稿', 'active', 'b1', '2026-01-02', '2026-01-02');
    """)
    conn.commit()
    conn.close()

    conn = init_db(path)
    rows = conn.execute(
        "SELECT id, status FROM chapters WHERE story_id='s1'"
        " ORDER BY created_at").fetchall()
    by_id = {r["id"]: r["status"] for r in rows}
    assert by_id["old1"] == "archived"   # 旧的去重保留为 archived
    assert by_id["old2"] == "active"     # 最新一条保持 active
    conn.close()
