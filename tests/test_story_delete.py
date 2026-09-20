"""书籍删除(ADR-0032,软删除)验收。

- 语义:stories.deleted_at 置位 + 5 张观测表物理清除;子表/checkpoint 保留;
- 权限:owner/admin 可删;editor/viewer 403;无关用户/已删书 404;未登录 401;
- 运行中(_active)409 + X-Error-Code;读路径全端点对软删书 404。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as main
import tests.test_graph_e2e as replay
from app.core.llm.facade import LLMFacade
from app.graph.build import build_graph
from app.graph.runtime import build_engine
from tests.test_api import login

TS = "2026-09-20T00:00:00Z"


@pytest.fixture()
def anon_client(tmp_path):
    """未带默认 token 的客户端(多角色按用户切换 header)。"""
    facade = LLMFacade(response_override=replay.override)
    deps, conn = build_engine(tmp_path / "del.db", llm=facade)
    main.install_engine(deps, conn, build_graph(deps, checkpointer=deps.checkpointer))
    with TestClient(main.app) as c:
        yield c, deps
    main.reset_engine()
    with main._active_lock:
        main._active.clear()


def make_user(c, admin_h, username):
    c.post("/admin/users", headers=admin_h,
           json={"username": username, "password": f"{username}pass1", "role": "user"})
    return {"Authorization": f"Bearer {login(c, username, f'{username}pass1')}"}


def add_member(deps, sid, user_header_username, c, admin_h, role):
    """直插 story_members(无成员管理 API):把普通用户加为 editor/viewer。"""
    uid = [u for u in c.get("/admin/users", headers=admin_h).json()
           if u["username"] == user_header_username][0]["id"]
    deps.conn.execute(
        "INSERT INTO story_members (story_id, user_id, role, created_at) VALUES (?,?,?,?)",
        (sid, uid, role, TS))
    deps.conn.commit()


def insert_observability_rows(deps, sid):
    """5 张观测表各插 1 行(最小必填列;均无 FK,story_id 可指向任意书)。"""
    conn = deps.conn
    conn.execute(
        "INSERT INTO usage_log (id, story_id, agent, model, created_at)"
        " VALUES ('u1', ?, 'writer', 'test-model', ?)", (sid, TS))
    conn.execute(
        "INSERT INTO agent_traces (id, story_id, agent, model, created_at)"
        " VALUES ('t1', ?, 'writer', 'test-model', ?)", (sid, TS))
    conn.execute(
        "INSERT INTO review_results (id, story_id, round_no, reviewer, verdict, created_at)"
        " VALUES ('r1', ?, 1, 'reviewer', 'pass', ?)", (sid, TS))
    conn.execute(
        "INSERT INTO retrieval_audit (id, story_id, caller, created_at)"
        " VALUES ('a1', ?, 'retriever', ?)", (sid, TS))
    conn.execute(
        "INSERT INTO llm_failures (id, story_id, created_at) VALUES ('f1', ?, ?)", (sid, TS))
    conn.commit()


def count(deps, table, sid) -> int:
    return deps.conn.execute(
        f"SELECT COUNT(*) n FROM {table} WHERE story_id=?", (sid,)).fetchone()["n"]


def test_owner_delete_hides_story_everywhere(anon_client):
    """owner 删除 → 200;列表/详情/全部子资源端点对软删书 404。"""
    c, deps = anon_client
    admin_h = {"Authorization": f"Bearer {login(c)}"}
    sid = c.post("/stories", json={"title": "待删书"}, headers=admin_h).json()["story_id"]

    r = c.delete(f"/stories/{sid}", headers=admin_h)
    assert r.status_code == 200
    assert r.json()["ok"] is True and r.json()["deleted_at"]

    assert all(s["id"] != sid for s in c.get("/stories", headers=admin_h).json())
    for url in (f"/stories/{sid}", f"/stories/{sid}/chapters/1", f"/stories/{sid}/codex",
                f"/stories/{sid}/run-state", f"/stories/{sid}/usage",
                f"/stories/{sid}/traces", f"/stories/{sid}/reviews",
                f"/stories/{sid}/chat/history"):
        assert c.get(url, headers=admin_h).status_code == 404, url
    # 写路径同样拒绝:软删书不能再生成/停止/发指令/发消息
    for url, payload in ((f"/stories/{sid}/generate", {"target_chapters": 1}),
                         (f"/stories/{sid}/stop", None),
                         (f"/stories/{sid}/directive", {"text": "x"}),
                         (f"/stories/{sid}/chat", {"message": "hi"})):
        assert c.post(url, json=payload, headers=admin_h).status_code == 404, url


def test_delete_idempotent(anon_client):
    """重复删除已删书 404(story_role 对软删书返回 None)。"""
    c, _ = anon_client
    admin_h = {"Authorization": f"Bearer {login(c)}"}
    sid = c.post("/stories", json={"title": "再删一次"}, headers=admin_h).json()["story_id"]
    assert c.delete(f"/stories/{sid}", headers=admin_h).status_code == 200
    assert c.delete(f"/stories/{sid}", headers=admin_h).status_code == 404


def test_delete_permission_matrix(anon_client):
    """editor/viewer 403;无关普通用户 404(不泄露存在性);未登录 401。"""
    c, deps = anon_client
    admin_h = {"Authorization": f"Bearer {login(c)}"}
    alice_h = make_user(c, admin_h, "alice")
    sid = c.post("/stories", json={"title": "权限书"}, headers=alice_h).json()["story_id"]
    for username, role in (("bob", "editor"), ("carol", "viewer")):
        h = make_user(c, admin_h, username)
        add_member(deps, sid, username, c, admin_h, role)
        assert c.delete(f"/stories/{sid}", headers=h).status_code == 403, role
    dave_h = make_user(c, admin_h, "dave")                      # 无关用户
    assert c.delete(f"/stories/{sid}", headers=dave_h).status_code == 404
    assert c.delete(f"/stories/{sid}").status_code == 401       # 未登录
    assert c.delete(f"/stories/{sid}", headers=alice_h).status_code == 200   # owner 本人可删


def test_admin_can_delete_others_book(anon_client):
    """admin 全域:可删普通用户的书。"""
    c, _ = anon_client
    admin_h = {"Authorization": f"Bearer {login(c)}"}
    eve_h = make_user(c, admin_h, "eve")
    sid = c.post("/stories", json={"title": "别人的书"}, headers=eve_h).json()["story_id"]
    assert c.delete(f"/stories/{sid}", headers=admin_h).status_code == 200


def test_delete_rejected_while_running(anon_client):
    """运行中(_active)删除 409 + X-Error-Code: story_running。"""
    c, _ = anon_client
    admin_h = {"Authorization": f"Bearer {login(c)}"}
    sid = c.post("/stories", json={"title": "运行中"}, headers=admin_h).json()["story_id"]
    with main._active_lock:
        main._active.add(sid)
    try:
        r = c.delete(f"/stories/{sid}", headers=admin_h)
        assert r.status_code == 409
        assert r.headers["X-Error-Code"] == "story_running"
        assert c.get(f"/stories/{sid}", headers=admin_h).status_code == 200  # 未删,仍可见
    finally:
        with main._active_lock:
            main._active.discard(sid)
    assert c.delete(f"/stories/{sid}", headers=admin_h).status_code == 200   # 停止后可删


def test_delete_purges_observability_keeps_content(anon_client):
    """观测表 5 张物理清除;章节等子表与 stories 行(deleted_at)保留。"""
    c, deps = anon_client
    admin_h = {"Authorization": f"Bearer {login(c)}"}
    sid = c.post("/stories", json={"title": "留痕书"}, headers=admin_h).json()["story_id"]
    branch = deps.conn.execute(
        "SELECT main_branch_id FROM stories WHERE id=?", (sid,)).fetchone()["main_branch_id"]
    deps.conn.execute(
        "INSERT INTO chapters (id, story_id, chapter_no, version_no, status,"
        " branch_id, created_at, updated_at)"
        " VALUES ('ch1', ?, 1, 1, 'active', ?, ?, ?)", (sid, branch, TS, TS))
    insert_observability_rows(deps, sid)

    assert c.delete(f"/stories/{sid}", headers=admin_h).status_code == 200
    for table in ("usage_log", "agent_traces", "review_results",
                  "retrieval_audit", "llm_failures"):
        assert count(deps, table, sid) == 0, table
    assert count(deps, "chapters", sid) == 1                    # 子表保留
    row = deps.conn.execute(
        "SELECT deleted_at FROM stories WHERE id=?", (sid,)).fetchone()
    assert row["deleted_at"] is not None                        # 软删标记在库


def test_review_queue_excludes_deleted(anon_client):
    """软删书的 pending 事实不再进入审核队列(visible_story_ids 过滤)。"""
    c, deps = anon_client
    admin_h = {"Authorization": f"Bearer {login(c)}"}
    sid = c.post("/stories", json={"title": "队列书"}, headers=admin_h).json()["story_id"]
    branch = deps.conn.execute(
        "SELECT main_branch_id FROM stories WHERE id=?", (sid,)).fetchone()["main_branch_id"]
    deps.conn.execute(
        "INSERT INTO facts (id, story_id, type, content, branch_id, status, created_at)"
        " VALUES ('fdel', ?, 'event', '软删书的待审事实', ?, 'pending_review', ?)",
        (sid, branch, TS))
    deps.conn.commit()
    assert [f["id"] for f in c.get("/facts/pending", headers=admin_h).json()] == ["fdel"]

    assert c.delete(f"/stories/{sid}", headers=admin_h).status_code == 200
    assert c.get("/facts/pending", headers=admin_h).json() == []
    assert count(deps, "facts", sid) == 1                       # 事实行本身保留
