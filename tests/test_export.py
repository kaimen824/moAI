"""全文导出 TXT 验收。

- 内容:书名开头 + active 章节按 chapter_no 序拼接(标题+正文),软删书/无章节 404;
- 权限:成员可导(与章节读同口径);无关用户 404(不泄露存在性)。
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

TS = "2026-10-07T00:00:00Z"


@pytest.fixture()
def client(tmp_path):
    """未带默认 token 的客户端(无关用户用独立 header)。"""
    facade = LLMFacade(response_override=replay.override)
    deps, conn = build_engine(tmp_path / "export.db", llm=facade)
    main.install_engine(deps, conn, build_graph(deps, checkpointer=deps.checkpointer))
    with TestClient(main.app) as c:
        yield c, deps
    main.reset_engine()


_seq = iter(range(1, 1000))


def add_chapter(deps, sid, no, title, content, status="active"):
    """直插章节(最小必填列);no 故意乱序插入验证 ORDER BY。"""
    branch = deps.conn.execute(
        "SELECT main_branch_id FROM stories WHERE id=?", (sid,)).fetchone()["main_branch_id"]
    deps.conn.execute(
        "INSERT INTO chapters (id, story_id, chapter_no, version_no, status,"
        " branch_id, title, content, created_at, updated_at)"
        " VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?)",
        (f"ch{next(_seq)}", sid, no, status, branch, title, content, TS, TS))
    deps.conn.commit()


def test_export_full_text(client):
    """书名 + 章节标题/正文按序拼接;Content-Disposition 为 RFC 5987 编码。"""
    c, deps = client
    admin_h = {"Authorization": f"Bearer {login(c)}"}
    sid = c.post("/stories", json={"title": "古真神"}, headers=admin_h).json()["story_id"]
    add_chapter(deps, sid, 2, "第2章", "第二章正文。")
    add_chapter(deps, sid, 1, "第1章", "第一章正文。")
    add_chapter(deps, sid, 1, "第1章草稿", "被替换的旧稿", status="superseded")

    r = c.get(f"/stories/{sid}/export", headers=admin_h)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert "filename*=UTF-8''" in r.headers["content-disposition"]
    body = r.text
    assert body.startswith("古真神")                       # 书名开头
    assert body.index("第1章") < body.index("第2章")        # 章节按序
    assert "第一章正文。" in body and "第二章正文。" in body
    assert "旧稿" not in body                              # 仅 active
    assert "草稿" not in body


def test_export_not_found_cases(client):
    """书不存在 / 空书无章节 均 404(文案区分)。"""
    c, deps = client
    admin_h = {"Authorization": f"Bearer {login(c)}"}
    assert c.get("/stories/nonexistent/export", headers=admin_h).status_code == 404

    sid = c.post("/stories", json={"title": "空书"}, headers=admin_h).json()["story_id"]
    r = c.get(f"/stories/{sid}/export", headers=admin_h)
    assert r.status_code == 404 and "no chapters" in r.json()["detail"]


def test_export_permission(client):
    """无关用户 404(不泄露存在性);未登录 401。"""
    c, _ = client
    admin_h = {"Authorization": f"Bearer {login(c)}"}
    sid = c.post("/stories", json={"title": "权限书"}, headers=admin_h).json()["story_id"]
    c.post("/admin/users", headers=admin_h,
           json={"username": "stranger", "password": "strangerpass1", "role": "user"})
    stranger_h = {"Authorization":
                  f"Bearer {login(c, 'stranger', 'strangerpass1')}"}
    assert c.get(f"/stories/{sid}/export", headers=stranger_h).status_code == 404
    assert c.get(f"/stories/{sid}/export").status_code == 401


def test_export_soft_deleted_story(client):
    """软删书(ADR-0032)导出 404。"""
    c, _ = client
    admin_h = {"Authorization": f"Bearer {login(c)}"}
    sid = c.post("/stories", json={"title": "已删书"}, headers=admin_h).json()["story_id"]
    assert c.delete(f"/stories/{sid}", headers=admin_h).status_code == 200
    assert c.get(f"/stories/{sid}/export", headers=admin_h).status_code == 404
