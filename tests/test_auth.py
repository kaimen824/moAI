"""认证与多租户边界(ADR-0022,评审 6.1 P0)验收。

- 未认证全端点 401;伪造/篡改 token 401;
- 跨用户 story 访问 404(不泄露存在性);审核队列按归属过滤;
- admin 专用端点(config/models、/admin/*)非 admin 403;
- 管理员开户/禁用;禁用后既有 token 立即失效。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.main as main
import tests.test_graph_e2e as replay
from app.core.llm.facade import LLMFacade
from app.graph.build import build_graph
from app.graph.runtime import build_engine
from tests.test_api import ADMIN, login

READ_ENDPOINTS = [
    ("get", "/stories"),
    ("get", "/facts/pending"),
    ("get", "/entities/pending"),
    ("get", "/config/models"),
]


@pytest.fixture()
def anon_client(tmp_path):
    """未带 token 的客户端(引擎已注入)。"""
    facade = LLMFacade(response_override=replay.override)
    deps, conn = build_engine(tmp_path / "auth.db", llm=facade)
    main.install_engine(deps, conn, build_graph(deps, checkpointer=deps.checkpointer))
    with TestClient(main.app) as c:
        yield c, deps
    main.reset_engine()


def test_unauthenticated_requests_rejected(anon_client):
    c, _ = anon_client
    for method, url in READ_ENDPOINTS:
        assert getattr(c, method)(url).status_code == 401, url
    assert c.post("/stories", json={"title": "x"}).status_code == 401
    assert c.post("/stories/s1/generate", json={}).status_code == 401
    assert c.get("/stories/s1").status_code == 401
    assert c.get("/stories/s1/chapters/1").status_code == 401
    assert c.post("/facts/xxx/review", json={"approve": True}).status_code == 401


def test_login_rejects_bad_credentials(anon_client):
    c, _ = anon_client
    assert c.post("/auth/login", json={"username": "admin",
                                       "password": "wrong"}).status_code == 401
    assert c.post("/auth/login", json={"username": "nobody",
                                       "password": "admin123"}).status_code == 401


def test_tampered_token_rejected(anon_client):
    c, _ = anon_client
    token = login(c)
    for bad in (token[:-3] + "aaa", token + "x", "not.a.jwt"):
        r = c.get("/stories", headers={"Authorization": f"Bearer {bad}"})
        assert r.status_code == 401
    r = c.get("/stories", headers={"Authorization": "Basic abc"})
    assert r.status_code == 401


def test_cross_user_story_access_404(anon_client):
    """用户 B 不能看到/访问用户 A 的 story;404 而非 403(不泄露存在性)。"""
    c, deps = anon_client
    admin_h = {"Authorization": f"Bearer {login(c)}"}
    sid = c.post("/stories", json={"title": "A 的书"}, headers=admin_h).json()["story_id"]

    c.post("/admin/users", headers=admin_h,
           json={"username": "alice", "password": "alicepass1", "role": "user"})
    alice_h = {"Authorization": f"Bearer {login(c, 'alice', 'alicepass1')}"}

    assert c.get("/stories", headers=alice_h).json() == []           # 列表不含他人书
    assert c.get(f"/stories/{sid}", headers=alice_h).status_code == 404
    assert c.get(f"/stories/{sid}/chapters/1", headers=alice_h).status_code == 404
    assert c.get(f"/stories/{sid}/codex", headers=alice_h).status_code == 404
    assert c.get(f"/stories/{sid}/run-state", headers=alice_h).status_code == 404
    assert c.post(f"/stories/{sid}/generate", json={}, headers=alice_h).status_code == 404
    assert c.post(f"/stories/{sid}/stop", headers=alice_h).status_code == 404
    assert c.post(f"/stories/{sid}/directive", json={"text": "x"},
                  headers=alice_h).status_code == 404


def test_review_queues_scoped_to_owner(anon_client):
    """审核队列只返回归属 story 的条目;跨租户审核 404。"""
    c, deps = anon_client
    admin_h = {"Authorization": f"Bearer {login(c)}"}
    sid = c.post("/stories", json={"title": "A 的书"}, headers=admin_h).json()["story_id"]
    branch_id = deps.conn.execute(
        "SELECT main_branch_id FROM stories WHERE id=?", (sid,)).fetchone()["main_branch_id"]
    fact_id = "f_pending_test"
    deps.conn.execute(
        "INSERT INTO facts (id, story_id, type, content, branch_id, status, created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (fact_id, sid, "event", "测试待审事实",
         branch_id, "pending_review", "2026-09-14T00:00:00Z"))
    deps.conn.commit()

    c.post("/admin/users", headers=admin_h,
           json={"username": "bob", "password": "bobpass123", "role": "user"})
    bob_h = {"Authorization": f"Bearer {login(c, 'bob', 'bobpass123')}"}

    assert c.get("/facts/pending", headers=bob_h).json() == []       # bob 看不到
    admin_queue = c.get("/facts/pending", headers=admin_h).json()
    assert [f["id"] for f in admin_queue] == [fact_id]               # admin 全量
    assert c.post(f"/facts/{fact_id}/review", json={"approve": True},
                  headers=bob_h).status_code == 404                  # 跨租户审核拒绝


def test_model_config_admin_only(anon_client):
    c, deps = anon_client
    admin_h = {"Authorization": f"Bearer {login(c)}"}
    sid = c.post("/stories", json={"title": "书"}, headers=admin_h).json()["story_id"]

    c.post("/admin/users", headers=admin_h,
           json={"username": "carol", "password": "carolpass1", "role": "user"})
    carol_h = {"Authorization": f"Bearer {login(c, 'carol', 'carolpass1')}"}

    assert c.get("/config/models", headers=carol_h).status_code == 403
    assert c.post("/config/models", headers=carol_h,
                  json={"role": "WRITER", "model": "glm-5"}).status_code == 403
    assert c.post("/config/models", headers=admin_h,
                  json={"role": "WRITER", "model": "glm-5"}).status_code == 200


def test_admin_endpoints_require_admin(anon_client):
    c, _ = anon_client
    admin_h = {"Authorization": f"Bearer {login(c)}"}
    c.post("/admin/users", headers=admin_h,
           json={"username": "dave", "password": "davepass12", "role": "user"})
    dave_h = {"Authorization": f"Bearer {login(c, 'dave', 'davepass12')}"}

    assert c.get("/admin/users", headers=dave_h).status_code == 403
    assert c.post("/admin/users", headers=dave_h,
                  json={"username": "eve", "password": "evepass123"}).status_code == 403
    # admin 正常开户;短密码/重复用户名拒绝
    assert c.post("/admin/users", headers=admin_h,
                  json={"username": "eve", "password": "short"}).status_code == 400
    assert c.post("/admin/users", headers=admin_h,
                  json={"username": "dave", "password": "otherpass1"}).status_code == 400
    users = {u["username"] for u in c.get("/admin/users", headers=admin_h).json()}
    assert users == {"admin", "dave"}   # eve 未被创建


def test_disabled_user_token_invalidated_immediately(anon_client):
    """禁用即时生效:既有 token 下一请求即 401(每请求校验 status)。"""
    c, _ = anon_client
    admin_h = {"Authorization": f"Bearer {login(c)}"}
    c.post("/admin/users", headers=admin_h,
           json={"username": "frank", "password": "frankpass", "role": "user"})
    frank_h = {"Authorization": f"Bearer {login(c, 'frank', 'frankpass')}"}
    assert c.get("/stories", headers=frank_h).status_code == 200

    uid = [u for u in c.get("/admin/users", headers=admin_h).json()
           if u["username"] == "frank"][0]["id"]
    assert c.patch(f"/admin/users/{uid}", headers=admin_h,
                   json={"status": "disabled"}).status_code == 200
    assert c.get("/stories", headers=frank_h).status_code == 401
    # 禁用账户无法再登录
    assert c.post("/auth/login", json={"username": "frank",
                                       "password": "frankpass"}).status_code == 401


def test_change_password_flow(anon_client):
    c, _ = anon_client
    admin_h = {"Authorization": f"Bearer {login(c)}"}
    c.post("/admin/users", headers=admin_h,
           json={"username": "gina", "password": "ginapass1", "role": "user"})

    # 旧密码错 -> 401;新密码过短 -> 400;正确 -> ok 且新密码可登录
    gina_h = {"Authorization": f"Bearer {login(c, 'gina', 'ginapass1')}"}
    assert c.post("/auth/change-password", headers=gina_h,
                  json={"old_password": "wrong", "new_password": "newpass123"}).status_code == 401
    assert c.post("/auth/change-password", headers=gina_h,
                  json={"old_password": "ginapass1", "new_password": "short"}).status_code == 400
    assert c.post("/auth/change-password", headers=gina_h,
                  json={"old_password": "ginapass1", "new_password": "newpass123"}).status_code == 200
    assert c.post("/auth/login", json={"username": "gina",
                                       "password": "newpass123"}).status_code == 200


# ---- refresh token(ADR-0029):双 token 类型互斥 + 滑动续期 ----

def _do_login(c, username="admin", password="admin123") -> dict:
    r = c.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200
    return r.json()


def test_login_returns_refresh_pair_and_refresh_flow(anon_client):
    """login 返回双 token;refresh 换新对,新 access 可正常访问业务端点。"""
    c, _ = anon_client
    data = _do_login(c)
    assert data["access_token"] and data["refresh_token"]

    r = c.post("/auth/refresh", headers={"Authorization": f"Bearer {data['refresh_token']}"})
    assert r.status_code == 200
    pair = r.json()
    assert pair["access_token"] and pair["refresh_token"]
    # 新 access 可访问业务端点(滑动续期闭环)
    assert c.get("/stories", headers={
        "Authorization": f"Bearer {pair['access_token']}"}).status_code == 200


def test_token_type_mutual_exclusion(anon_client):
    """类型互斥:refresh 不能当 access 用,access 不能当 refresh 用(防降级)。"""
    c, _ = anon_client
    data = _do_login(c)
    # refresh token 顶替 access -> 401
    assert c.get("/stories", headers={
        "Authorization": f"Bearer {data['refresh_token']}"}).status_code == 401
    # access token 顶替 refresh -> 401
    assert c.post("/auth/refresh", headers={
        "Authorization": f"Bearer {data['access_token']}"}).status_code == 401


def test_refresh_rejected_for_disabled_or_bad(anon_client):
    """禁用用户 refresh 即时拒绝;坏 token / 缺凭证 401。"""
    c, _ = anon_client
    admin = _do_login(c)
    c.post("/admin/users", headers={"Authorization": f"Bearer {admin['access_token']}"},
           json={"username": "hank", "password": "hankpass1", "role": "user"})
    hank = _do_login(c, "hank", "hankpass1")
    uid = [u for u in c.get("/admin/users", headers={
        "Authorization": f"Bearer {admin['access_token']}"}).json()
        if u["username"] == "hank"][0]["id"]
    c.patch(f"/admin/users/{uid}", headers={
        "Authorization": f"Bearer {admin['access_token']}"},
        json={"status": "disabled"})
    assert c.post("/auth/refresh", headers={
        "Authorization": f"Bearer {hank['refresh_token']}"}).status_code == 401

    assert c.post("/auth/refresh", headers={
        "Authorization": "Bearer not.a.jwt"}).status_code == 401
    assert c.post("/auth/refresh").status_code == 401          # 无凭证
