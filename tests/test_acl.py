"""P2 验收 ②:权限拒绝(fail-closed)与多租户隔离。"""

from __future__ import annotations

import pytest

from app.memory.repository import AgentContext, PermissionError_, Repository
from app.memory.schemas import CharacterRow, Fact


@pytest.fixture()
def env(db):
    repo = Repository(db)
    story_id, branch = repo.create_story("A 书")
    story2, _ = repo.create_story("B 书")
    return repo, story_id, story2, branch


def test_context_requires_identity():
    with pytest.raises(PermissionError_):
        AgentContext("", "story-1")          # 无 agent 名 -> 构造即拒
    with pytest.raises(PermissionError_):
        AgentContext("writer", "")           # 无 story -> 构造即拒


def test_writer_cannot_write_facts(env):
    repo, story, _, branch = env
    ctx = AgentContext("writer", story)
    with pytest.raises(PermissionError_):
        repo.insert_facts(ctx, [Fact(id="", story_id="", type="event",
                                     content="x", chapter_established=1, branch_id=branch)])


def test_outline_agent_readonly_everywhere(env):
    repo, story, _, _ = env
    ctx = AgentContext("outline_agent", story)
    with pytest.raises(PermissionError_):
        repo.upsert_plot_thread(ctx, __import__("app.memory.schemas", fromlist=["PlotThread"]).PlotThread(
            id="", story_id="", description="x", branch_id=""))


def test_unknown_agent_denied(env):
    repo, story, _, _ = env
    with pytest.raises(PermissionError_):
        repo.check_access(AgentContext("stranger", story), "facts", "read")


def test_story_id_forced_on_write(env):
    """写入强制对齐 ctx.story_id:即使行对象携带 B 书 id,也只会写进 A 书(多租户)。"""
    repo, story, story2, branch = env
    ctx = AgentContext("event_manager", story)
    [fact_id] = repo.insert_facts(ctx, [Fact(
        id="", story_id=story2, type="event", content="带错 story_id 的行",
        chapter_established=1, branch_id=branch,
    )])
    row = repo.conn.execute("SELECT story_id FROM facts WHERE id=?", (fact_id,)).fetchone()
    assert row["story_id"] == story          # 强制改写为 ctx.story_id


def test_event_manager_can_write_facts(env):
    repo, story, _, branch = env
    ctx = AgentContext("event_manager", story)
    [fact_id] = repo.insert_facts(ctx, [Fact(
        id="", story_id="", type="event", content="正常写入",
        chapter_established=1, branch_id=branch,
    )])
    assert fact_id


def test_cross_story_read_isolated(env):
    """A 书的 ctx 查不到 B 书的角色。"""
    repo, story, story2, _ = env
    repo.upsert_character(AgentContext("character_manager", story2),
                          CharacterRow(id="", story_id="", name="B书角色"))
    chars_a = repo.get_characters(AgentContext("writer", story))
    assert all(c.name != "B书角色" for c in chars_a)
