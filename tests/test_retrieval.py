"""P2 验收 ③/④:检索服务(主路 + 链接扩展 + 向量兜底/桩降级 + 审计)。"""

from __future__ import annotations

from app.memory.repository import AgentContext, Repository
from app.memory.retrieval import (
    RetrievalService,
    cosine,
    decode_embedding,
    encode_embedding,
)
from app.memory.schemas import (
    CharacterRow,
    EntityLink,
    EntityRow,
    Fact,
    PlotThread,
    VisibilityEntry,
)


def make_env(db, embed_fn=None):
    repo = Repository(db)
    story_id, branch = repo.create_story("检索测试")
    char_ctx = AgentContext("character_manager", story_id)
    event = AgentContext("event_manager", story_id)

    hero = repo.upsert_character(char_ctx, CharacterRow(id="", story_id="", name="主角"))
    # 实体与链接:主角 ↔ 秘密组织(一跳邻居)(ADR-0015:实体族写权在 entity_manager)
    ent_ctx = AgentContext("entity_manager", story_id)
    org = repo.upsert_entity(ent_ctx, EntityRow(
        id="ent_org", story_id="", type="faction", name="暗影组织", content="反派势力"))
    hero_ent = repo.upsert_entity(ent_ctx, EntityRow(
        id="ent_hero", story_id="", type="character", name="主角", content="主角条目"))
    repo.add_entity_links(ent_ctx, [EntityLink(id="", story_id="",
                                               from_entity=hero_ent, to_entity=org, relation="敌对")])
    repo.conn.execute("UPDATE characters SET entity_id=? WHERE id=?", (hero_ent, hero))
    repo.conn.commit()

    repo.insert_facts(event, [Fact(
        id="fact_dead", story_id="", type="event", content="师父被害(第1章)",
        chapter_established=1, branch_id=branch,
    )], visibility=[VisibilityEntry("fact_dead", hero, "known_full", None, 1, branch)])
    repo.upsert_plot_thread(AgentContext("reviewer", story_id), PlotThread(
        id="", story_id="", description="师父之死的真凶", branch_id=branch,
        planted_chapter=1, status="open"))

    # 向量兜底数据:一条"长尾"事实(无 visibility,主路不会带回)
    repo.insert_facts(event, [Fact(
        id="fact_tail", story_id="", type="setting", content="村口的石碑刻着古文字",
        chapter_established=1, branch_id=branch,
        embedding=encode_embedding([0.9, 0.1, 0.0]),
    )])
    svc = RetrievalService(repo, embed_fn=embed_fn)
    return svc, repo, story_id, branch, hero


def test_structured_main_path(db):
    svc, repo, story, branch, hero = make_env(db)
    ctx = AgentContext("writer", story)
    r = svc.retrieve_for_chapter(ctx, 2, [hero])
    assert any(f["id"] == "fact_dead" for f in r.pov_facts)       # POV 事实
    assert any(c["name"] == "主角" for c in r.characters)          # 角色卡
    assert any(t["description"] == "师父之死的真凶" for t in r.active_threads)
    assert r.expanded_entities and r.expanded_entities[0]["name"] == "暗影组织"  # 链接一跳
    assert not any(f["id"] == "fact_tail" for f in r.pov_facts)    # 无 visibility 不进主路


def test_vector_fallback_finds_longtail(db):
    """向量兜底:query 命中长尾事实(主路未覆盖)。"""
    def fake_embed(texts):
        return [[0.9, 0.1, 0.0] for _ in texts]     # 与 fact_tail 同向

    svc, repo, story, branch, hero = make_env(db, embed_fn=fake_embed)
    ctx = AgentContext("writer", story)
    r = svc.retrieve_for_chapter(ctx, 2, [hero], query_text="村口石碑")
    assert any(f["id"] == "fact_tail" for f in r.vector_hits)
    assert not r.degraded_vector


def test_vector_fallback_degrades_without_embed(db):
    svc, repo, story, branch, hero = make_env(db)   # embed_fn=None(桩)
    ctx = AgentContext("writer", story)
    r = svc.retrieve_for_chapter(ctx, 2, [hero], query_text="村口石碑")
    assert r.degraded_vector and r.vector_hits == []


def test_retrieval_audit_logged(db):
    svc, repo, story, branch, hero = make_env(db)
    svc.retrieve_for_chapter(AgentContext("writer", story), 2, [hero], query_text="q")
    row = repo.conn.execute("SELECT * FROM retrieval_audit").fetchone()
    assert row["caller"] == "writer"
    assert row["returned_count"] >= 1


def test_unknown_agent_rejected_by_service(db):
    svc, repo, story, branch, hero = make_env(db)
    from app.memory.repository import PermissionError_
    import pytest
    with pytest.raises(PermissionError_):
        svc.retrieve_for_chapter(AgentContext("stranger", story), 2, [hero])


def test_cosine_roundtrip():
    vec = [0.1, 0.2, 0.3]
    assert decode_embedding(encode_embedding(vec)) == vec
    assert abs(cosine([1, 0], [1, 0]) - 1.0) < 1e-9
    assert abs(cosine([1, 0], [0, 1])) < 1e-9
