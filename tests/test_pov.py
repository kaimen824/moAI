"""P2 验收 ①:POV 查询正确性(ADR-0003:投影、partial detail、误信、揭穿、时间窗)。"""

from __future__ import annotations

import pytest

from app.memory.repository import AgentContext, Repository
from app.memory.schemas import Belief, CharacterRow, Fact, VisibilityEntry
from app.memory.world import get_pov_memory, replay_world


@pytest.fixture()
def setup(db):
    """场景:张三"死亡"(fact1,第2章)→ 第4章被推翻(fact2:假死)。
    李四 known_full + 误信(第2章建立,第5章揭穿);
    王五 known_partial(带 detail,第3章才知晓);
    赵六不知情(无 visibility 行)。"""
    repo = Repository(db)
    story_id, branch = repo.create_story("测试小说")
    event = AgentContext("event_manager", story_id)

    char_ctx = AgentContext("character_manager", story_id)
    li = repo.upsert_character(char_ctx, CharacterRow(id="", story_id="", name="李四"))
    wang = repo.upsert_character(char_ctx, CharacterRow(id="", story_id="", name="王五"))
    repo.insert_facts(event, [Fact(
        id="fact1", story_id="", type="event", content="张三在第2章遇袭身亡",
        chapter_established=2, branch_id=branch,
    )], visibility=[
        VisibilityEntry("fact1", li, "known_full", None, 2, branch),
        VisibilityEntry("fact1", wang, "known_partial", "知道有袭击,不知结果", 3, branch),
    ])
    repo.insert_facts(event, [Fact(
        id="fact2", story_id="", type="event", content="真相:张三假死(第4章揭示)",
        chapter_established=4, branch_id=branch, prev_version_id="fact1",
    )])
    repo.insert_beliefs(event, [Belief(
        id="belief1", story_id="", character_id=li, content="李四以为张三被山贼所害",
        established_chapter=2, branch_id=branch, source_fact_id="fact1",
        status="dispelled", dispelled_chapter=5,
    )])
    return {"repo": repo, "story": story_id, "branch": branch, "li": li, "wang": wang}


def pov(setup, char_id, upto):
    return get_pov_memory(
        setup["repo"].conn if hasattr(setup["repo"], "conn") else None,
        setup["story"], setup["branch"], char_id, upto,
    )


def test_known_full_within_window(setup):
    r = pov(setup, setup["li"], 3)
    assert any(f["id"] == "fact1" for f in r["facts"])
    assert any(b["id"] == "belief1" for b in r["beliefs"])   # 误信在第3章仍 believed


def test_partial_has_detail(setup):
    r = pov(setup, setup["wang"], 3)
    f1 = [f for f in r["facts"] if f["id"] == "fact1"][0]
    assert f1["knowledge_level"] == "known_partial"
    assert f1["detail"] == "知道有袭击,不知结果"


def test_partial_not_yet_learned(setup):
    """王五第3章才知晓:第2章 POV 不含该事实。"""
    r = pov(setup, setup["wang"], 2)
    assert not any(f["id"] == "fact1" for f in r["facts"])


def test_unknown_character_sees_nothing(setup):
    """赵六无 visibility 行 = 未知(R3:缺省即未知)。"""
    r = pov(setup, "zhaoliu", 3)
    assert r["facts"] == [] and r["beliefs"] == []


def test_overridden_fact_excluded_after_reversal(setup):
    """第4章 fact2 推翻 fact1:第6章 POV 不再含 fact1;
    fact2 未给李四 visibility,李四仍不知道真相(POV 严格投影)。"""
    r = pov(setup, setup["li"], 6)
    assert not any(f["id"] == "fact1" for f in r["facts"])
    assert not any(f["id"] == "fact2" for f in r["facts"])


def test_dispelled_belief_excluded(setup):
    """误信第5章被揭穿:第6章 POV 不含 belief1。"""
    r = pov(setup, setup["li"], 6)
    assert not any(b["id"] == "belief1" for b in r["beliefs"])


def test_world_replay_respects_timepoint(setup):
    """世界回放:第3章 fact1 有效;第6章 fact2 有效、fact1 失效。"""
    conn = setup["repo"].conn
    snap3 = replay_world(conn, setup["story"], setup["branch"], 3)
    snap6 = replay_world(conn, setup["story"], setup["branch"], 6)
    assert {f["id"] for f in snap3.facts} == {"fact1"}
    assert {f["id"] for f in snap6.facts} == {"fact2"}


def test_version_chain_early_timepoint_keeps_old_fact(setup):
    """旧时点回放不受后续推翻影响(历史可回溯)。"""
    conn = setup["repo"].conn
    snap = replay_world(conn, setup["story"], setup["branch"], 3)
    assert any(f["id"] == "fact1" for f in snap.facts)
