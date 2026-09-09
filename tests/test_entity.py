"""实体层单元测试(ADR-0015):三层漏斗消歧 / verdict 应用 / 合并执行 / 别名识别。"""

from __future__ import annotations

import pytest

from app.db.database import init_db
from app.memory.entity import EntityService
from app.memory.repository import AgentContext, Repository
from app.memory.schemas import EntityRow


@pytest.fixture()
def repo(tmp_path):
    conn = init_db(tmp_path / "entity.db")
    r = Repository(conn)
    yield r
    conn.close()


@pytest.fixture()
def sid(repo) -> str:
    """已存在的 story id(entities.story_id 外键指向 stories)。"""
    story_id, _ = repo.create_story("实体测试书", "测试")
    return story_id


@pytest.fixture()
def svc(repo):
    # 确定性向量:[1,0] 与 [0.99,0.14] 近乎同向(cos≈0.99),与 [-1,0] 反向
    return EntityService(repo, embed_fn=lambda texts: [[1.0, 0.0]] * len(texts))


def _mk_entity(repo, ctx, name, type="character", content="旧角色", embedding=None):
    from app.memory.retrieval import encode_embedding
    return repo.upsert_entity(ctx, EntityRow(
        id="", story_id="", type=type, name=name, content=content,
        embedding=encode_embedding(embedding) if embedding else None))


# ---------- 漏斗①:确定性层 ----------

def test_exact_name_and_alias_hit_merge(repo, svc, sid):
    ctx = AgentContext("entity_manager", sid)
    eid = _mk_entity(repo, ctx, "李剑尘", embedding=[0.99, 0.14])
    repo.conn.execute(
        "INSERT INTO entity_aliases (alias, story_id, entity_id, created_at)"
        " VALUES (?,?,?,datetime('now'))", ("剑尘子", sid, eid))
    repo.conn.commit()

    res = svc.resolve_candidates(ctx, [
        {"name": "李剑尘", "type": "character", "description": ""},
        {"name": "剑尘子", "type": "character", "description": ""},
    ])
    assert all(r["action"] == "merge" and r["target_id"] == eid for r in res)


def test_historical_decisions_persist(repo, svc, sid):
    """人工裁决持久生效:merged 的候选名自动并;new 的自动新建。"""
    ctx = AgentContext("entity_manager", sid)
    eid = _mk_entity(repo, ctx, "李剑尘")
    repo.conn.executemany(
        "INSERT INTO entity_merge_proposals (id, story_id, candidate_name,"
        " target_entity_id, status, created_at) VALUES (?,?,?,?,?,datetime('now'))",
        [("p1", sid, "剑尘真人", eid, "merged"),
         ("p2", sid, "林家四少", eid, "new")])
    repo.conn.commit()

    res = {r["candidate"]["name"]: r for r in svc.resolve_candidates(ctx, [
        {"name": "剑尘真人", "type": "character", "description": ""},
        {"name": "林家四少", "type": "character", "description": ""},
    ])}
    assert res["剑尘真人"]["action"] == "merge"
    assert res["林家四少"]["action"] == "new"


# ---------- 漏斗②:向量层 ----------

def test_vector_layer_adjudicate_vs_new(repo, svc, sid):
    ctx = AgentContext("entity_manager", sid)
    _mk_entity(repo, ctx, "李剑尘", embedding=[0.99, 0.14])   # 与查询向量近乎同向
    _mk_entity(repo, ctx, "黑风寨", type="faction", embedding=[-1.0, 0.0])

    res = {r["candidate"]["name"]: r for r in svc.resolve_candidates(ctx, [
        {"name": "剑尘真人", "type": "character", "description": "道号"},
        {"name": "青云剑", "type": "item", "description": "无相似实体"},
    ])}
    adj = res["剑尘真人"]
    assert adj["action"] == "adjudicate"
    assert adj["topk"] and adj["topk"][0]["name"] == "李剑尘"
    assert res["青云剑"]["action"] == "adjudicate"   # 仍有实体池:交语义裁决


def test_no_entities_at_all_is_new(repo, svc, sid):
    ctx = AgentContext("entity_manager", sid)
    res = svc.resolve_candidates(ctx, [{"name": "第一个", "type": "item", "description": ""}])
    assert res[0]["action"] == "new"


def test_embed_failure_degrades_to_adjudicate(repo, sid):
    def boom(texts):
        raise RuntimeError("embed down")
    svc = EntityService(repo, embed_fn=boom)
    ctx = AgentContext("entity_manager", sid)
    _mk_entity(repo, ctx, "李剑尘")
    res = svc.resolve_candidates(ctx, [{"name": "剑尘真人", "type": "character",
                                        "description": ""}])
    assert res[0]["action"] == "adjudicate"    # 不阻塞:降级为纯语义裁决


# ---------- 漏斗③:verdict 应用 ----------

def test_apply_verdicts_same_new_uncertain(repo, svc, sid):
    ctx = AgentContext("entity_manager", sid)
    target = _mk_entity(repo, ctx, "李剑尘")
    resolutions = svc.resolve_candidates(ctx, [
        {"name": "剑尘真人", "type": "character", "description": "道号"},
        {"name": "黑风寨", "type": "faction", "description": "马匪"},
        {"name": "守碑人", "type": "character", "description": "神秘老者"},
    ])
    changes = svc.apply_verdicts(ctx, resolutions, {
        "剑尘真人": {"decision": "same", "target_id": target},
        "黑风寨": {"decision": "new"},
        "守碑人": {"decision": "uncertain", "target_id": target,
                    "similarity": 0.9, "evidence": "都在古碑旁"},
    }, chapter_no=3)

    names = {e["name"] for e in changes["new_entities"]}
    assert names == {"黑风寨", "守碑人"}                    # same 不建新条目
    assert {"alias": "剑尘真人", "entity_id": target} in changes["aliases"]
    assert len(changes["proposals"]) == 1
    p = changes["proposals"][0]
    assert p["candidate_name"] == "守碑人" and p["target_entity_id"] == target
    # 先写后合并:uncertain 候选也有自己的条目 id
    shoubei = next(e for e in changes["new_entities"] if e["name"] == "守碑人")
    assert p["candidate_entity_id"] == shoubei["id"]
    assert shoubei["chapter_no"] == 3

    # 链接解析:别名与新建实体都可作为端点;解析不了的丢弃
    links = svc.resolve_links(ctx, [
        {"from": "剑尘真人", "to": "黑风寨", "relation": "荡平"},
        {"from": "守碑人", "to": "不存在的人", "relation": "无效"},
    ], {a["alias"]: a["entity_id"] for a in changes["aliases"]}
       | {e["name"]: e["id"] for e in changes["new_entities"]}, chapter_no=3)
    assert len(links) == 1
    assert links[0]["from_entity"] == target
    assert links[0]["to_entity"] == next(
        e["id"] for e in changes["new_entities"] if e["name"] == "黑风寨")


# ---------- 合并执行(API 裁决)----------

def test_execute_merge_redirects_and_absorbs(repo, svc, sid):
    ctx = AgentContext("entity_manager", sid)
    target = _mk_entity(repo, ctx, "李剑尘")
    cand = _mk_entity(repo, ctx, "剑尘真人", content="候选条目")
    third = _mk_entity(repo, ctx, "古碑", type="item")
    repo.conn.executemany(
        "INSERT INTO entity_links (id, story_id, from_entity, to_entity, relation)"
        " VALUES (?,?,?,?,?)",
        [("l1", sid, cand, third, "守碑"),
         ("l2", sid, third, cand, "被守")])
    repo.conn.execute(
        "INSERT INTO entity_merge_proposals (id, story_id, candidate_name,"
        " candidate_entity_id, target_entity_id, status, created_at)"
        " VALUES ('p1',?,?,?,?,'pending',datetime('now'))", (sid, "剑尘真人", cand, target))
    repo.conn.commit()

    svc.execute_merge(repo.conn, dict(repo.conn.execute(
        "SELECT * FROM entity_merge_proposals WHERE id='p1'").fetchone()))
    repo.conn.commit()

    links = {(r["from_entity"], r["to_entity"]) for r in repo.conn.execute(
        "SELECT from_entity, to_entity FROM entity_links").fetchall()}
    assert links == {(target, third), (third, target)}       # 候选链接已重定向
    alias = repo.conn.execute(
        "SELECT entity_id FROM entity_aliases WHERE alias='剑尘真人'").fetchone()
    assert alias["entity_id"] == target                       # 名字吸收
    status = repo.conn.execute(
        "SELECT status FROM entities WHERE id=?", (cand,)).fetchone()["status"]
    assert status == "merged"                                 # 候选保留审计
    prop = repo.conn.execute(
        "SELECT status FROM entity_merge_proposals WHERE id='p1'").fetchone()["status"]
    assert prop == "merged"


# ---------- 阶段滚动 ----------

def test_stage_touched_by_chapter_range(repo, svc, sid):
    ctx = AgentContext("entity_manager", sid)
    seed = _mk_entity(repo, ctx, "青岩宗", type="faction")               # 共创种子:无章号
    in_stage = _mk_entity(repo, ctx, "黑风寨", type="faction")
    repo.conn.execute("UPDATE entities SET chapter_no=2 WHERE id=?", (in_stage,))
    repo.conn.execute(
        "INSERT INTO entity_links (id, story_id, from_entity, to_entity, relation,"
        " chapter_no) VALUES ('l1',?,?,?,'出现',5)", (sid, seed, in_stage))
    repo.conn.commit()

    touched = svc.stage_touched(ctx, 1, 6)
    names = {t["name"] for t in touched}
    assert "黑风寨" in names          # 章号 2 落在阶段内
    assert "青岩宗" in names          # 链接章号 5 落在阶段内(种子也被触达)
