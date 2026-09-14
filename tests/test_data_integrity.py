"""批次 3(数据正确性)回归:评审 6.5/6.6/6.7/6.8/6.11/6.12。

- 6.5  POV 记忆 status 口径:rejected 一律排除;pending_review 以低置信线索进入,
       writer 渲染层显式分节标注
- 6.6  向量兜底分级硬过滤:信息差事实在检索层滤除,无 visibility 行的背景事实保留
- 6.7  伏笔 thread_id 全链路:定稿优先按 id 精确命中,失配降级描述 LIKE 并留痕
- 6.8  共创落库单事务:大纲归档/落库与角色卡/实体种子原子,失败整体回滚
- 6.11 阶段末章自身摘要并入聚合输入(未落库先于 finalize)
- 6.12 用户指令延迟消费:peek 只读,定稿事务内标记,回滚不丢指令
"""

from __future__ import annotations

import sqlite3
import uuid

import pytest

from app.core.llm.facade import LLMFacade
from app.graph.agents.writer import render_context
from app.graph.runtime import build_engine
from app.memory.repository import AgentContext
from app.memory.retrieval import RetrievalService, encode_embedding
from app.memory.world import get_pov_memory

_TS = "2026-01-01T00:00:00+00:00"


@pytest.fixture()
def eng(tmp_path):
    facade = LLMFacade(response_override=lambda stage: None)
    return build_engine(tmp_path / "data.db", llm=facade)


# ---------- 夹具辅助(直插最小行)----------

def _add_char(conn, story_id: str, name: str) -> str:
    cid = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO characters (id, story_id, name, profile, entity_id,"
        " created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
        (cid, story_id, name, "", None, _TS, _TS))
    conn.commit()
    return cid


def _add_fact(conn, story_id: str, branch: str, content: str, *, chapter: int = 1,
              status: str = "confirmed", embedding: list[float] | None = None,
              visible: list[str] | None = ()) -> str:
    fid = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO facts (id, story_id, type, content, chapter_established,"
        " branch_id, prev_version_id, confidence, status, embedding, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (fid, story_id, "event", content, chapter, branch, None, "high", status,
         encode_embedding(embedding) if embedding else None, _TS))
    for cid in (visible or ()):
        conn.execute(
            "INSERT OR REPLACE INTO fact_visibility (fact_id, character_id,"
            " knowledge_level, detail, learned_chapter, branch_id) VALUES (?,?,?,?,?,?)",
            (fid, cid, "known_full", None, chapter, branch))
    conn.commit()
    return fid


def _add_thread(conn, story_id: str, branch: str, desc: str, *,
                planted: int = 1, tier: str = "short") -> str:
    tid = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO plot_threads (id, story_id, description, planted_chapter,"
        " resolved_chapter, status, tier, basis, branch_id, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (tid, story_id, desc, planted, None, "open", tier, "", branch, _TS, _TS))
    conn.commit()
    return tid


# ---------- 6.5 POV status 口径 ----------

def test_pov_memory_excludes_rejected_keeps_pending(eng):
    deps, conn = eng
    story_id, branch = deps.repo.create_story("POV 口径", "测试")
    c1 = _add_char(conn, story_id, "沈砚")
    _add_fact(conn, story_id, branch, "已确认事实", visible=[c1])
    _add_fact(conn, story_id, branch, "待审线索事实",
              status="pending_review", visible=[c1])
    _add_fact(conn, story_id, branch, "被否决事实", status="rejected", visible=[c1])

    pov = get_pov_memory(conn, story_id, branch, c1, upto_chapter=5)
    contents = {f["content"] for f in pov["facts"]}
    assert "已确认事实" in contents
    assert "待审线索事实" in contents          # pending 以低置信线索进入
    assert "被否决事实" not in contents        # rejected 一律排除(审核裁决即权威)


def test_render_context_splits_pending_section():
    state = {"context_bundle": {"pov_facts": [
        {"content": "确知事实甲", "chapter_established": 1, "status": "confirmed"},
        {"content": "待审线索乙", "chapter_established": 2, "status": "pending_review"},
    ]}}
    text = render_context(state)
    head, tail = text.split("[低置信线索", 1)
    assert "确知事实甲" in head                       # 已知事实分节
    assert "待审线索乙" not in head                   # pending 不混入已知分节
    assert "待审线索乙" in tail                       # 独立低置信分节
    assert "严禁作为确定事实" in tail


# ---------- 6.6 向量兜底分级硬过滤 ----------

def test_vector_fallback_filters_information_gap(eng):
    deps, conn = eng
    story_id, branch = deps.repo.create_story("信息差", "测试")
    c1 = _add_char(conn, story_id, "沈砚")
    c2 = _add_char(conn, story_id, "白芷")
    vec = [1.0, 0.0]
    f_own = _add_fact(conn, story_id, branch, "沈砚亲历的事实",
                      embedding=vec, visible=[c1])
    f_gap = _add_fact(conn, story_id, branch, "白芷独知的秘密",
                      embedding=vec, visible=[c2])
    f_bg = _add_fact(conn, story_id, branch, "无归属的世界背景",
                     embedding=vec, visible=[])

    svc = RetrievalService(deps.repo,
                           embed_fn=lambda texts: [vec for _ in texts])
    res = svc.retrieve_for_chapter(AgentContext("writer", story_id), 5, [c1],
                                   query_text="线索")
    hit_ids = {h["id"] for h in res.vector_hits}
    assert f_bg in hit_ids             # 无 visibility 行的背景事实:保留
    assert f_gap not in hit_ids        # 信息差事实:检索层硬滤除(非 prompt 约束)
    assert f_own not in hit_ids        # 已在 pov_facts,进排除集
    assert {f["id"] for f in res.pov_facts} == {f_own}


# ---------- 6.7 伏笔 thread_id 全链路 ----------

def test_thread_action_hits_by_thread_id(eng):
    deps, conn = eng
    story_id, branch = deps.repo.create_story("伏笔定向", "测试")
    t1 = _add_thread(conn, story_id, branch, "古碑的来历之谜")
    t2 = _add_thread(conn, story_id, branch, "古碑的来历之秘")

    deps.commit_finalize({"story_id": story_id, "branch_id": branch,
                          "chapter_no": 2, "draft": "正文",
                          "thread_changes": [
                              {"action": "resolve", "thread_id": t2,
                               "description": "古碑的来历"}]})
    st = {r["id"]: r["status"] for r in
          conn.execute("SELECT id, status FROM plot_threads")}
    assert st[t1] == "open"            # 近似描述不串线
    assert st[t2] == "resolved"        # thread_id 精确命中
    assert conn.execute("SELECT COUNT(*) c FROM retrieval_audit"
                        " WHERE caller='thread_fallback'").fetchone()["c"] == 0


def test_thread_action_falls_back_to_like_with_audit(eng):
    deps, conn = eng
    story_id, branch = deps.repo.create_story("伏笔降级", "测试")
    t1 = _add_thread(conn, story_id, branch, "古碑的来历之谜")

    deps.commit_finalize({"story_id": story_id, "branch_id": branch,
                          "chapter_no": 2, "draft": "正文",
                          "thread_changes": [
                              {"action": "drop",
                               "description": "古碑的来历之谜"}]})   # 无 thread_id
    assert conn.execute("SELECT status FROM plot_threads WHERE id=?",
                        (t1,)).fetchone()["status"] == "dropped"
    audit = conn.execute("SELECT query FROM retrieval_audit"
                         " WHERE caller='thread_fallback'").fetchall()
    assert len(audit) == 1 and "thread_id=(缺失)" in audit[0]["query"]


def test_escalate_review_hits_by_thread_id(eng):
    deps, conn = eng
    story_id, branch = deps.repo.create_story("伏笔升格", "测试")
    t_short = _add_thread(conn, story_id, branch, "黑衣人身份", tier="short")
    t_long = _add_thread(conn, story_id, branch, "旧神复苏主线", tier="long")

    deps.commit_finalize({"story_id": story_id, "branch_id": branch,
                          "chapter_no": 3, "draft": "正文",
                          "thread_review": {"reviews": [
                              {"thread_id": t_short, "description": "黑衣人身份",
                               "verdict": "escalate", "reason": "绑定主线"},
                              {"thread_id": t_long, "description": "旧神复苏主线",
                               "verdict": "escalate", "reason": "已是 long 应被跳过"}]}})
    rows = {r["id"]: r for r in conn.execute(
        "SELECT id, tier, escalated_chapter FROM plot_threads")}
    assert rows[t_short]["tier"] == "long"
    assert rows[t_short]["escalated_chapter"] == 3
    assert rows[t_long]["tier"] == "long"          # long 原样(条件不含它,未被误改)
    assert rows[t_long]["escalated_chapter"] is None


# ---------- 6.8 共创落库单事务 ----------

def _confirm_state(story_id: str) -> dict:
    return {"story_id": story_id,
            "character_drafts": [{"name": "沈砚", "profile": "主角;坚韧"}],
            "entity_drafts": {
                "entities": [{"name": "青岩宗", "type": "faction",
                              "content": "山村所属宗门", "aliases": ["宗门"]}],
                "character_aliases": [{"name": "沈砚", "aliases": ["砚小子"]}],
                "links": [{"from": "沈砚", "to": "青岩宗", "relation": "隶属"}]}}


def _count(conn, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]


def test_character_seeds_prepare_is_pure(eng):
    deps, conn = eng
    story_id, _branch = deps.repo.create_story("共创准备", "测试")
    state = _confirm_state(story_id)

    plan = deps.prepare_character_seeds(state)
    assert _count(conn, "characters") == 0           # 准备阶段零 DB 写入
    assert _count(conn, "entities") == 0
    assert len(plan["chars"]) == 1 and len(plan["ents"]) == 2
    assert state["character_drafts"][0]["id"] == plan["chars"][0]["id"]
    assert state["character_drafts"][0]["entity_id"] == plan["chars"][0]["entity_id"]


def test_outline_and_seeds_rollback_together(eng):
    deps, conn = eng
    story_id, _branch = deps.repo.create_story("共创原子", "测试")
    plan = deps.prepare_character_seeds(_confirm_state(story_id))

    def _begin_outline():
        conn.execute("BEGIN")
        conn.execute(
            "INSERT INTO outlines (id, story_id, version_no, content, status, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (uuid.uuid4().hex, story_id, 1, "总大纲", "confirmed", _TS))

    # 中途失败(实体 id 冲突)→ 大纲与角色卡整体回滚,零半成品
    bad = {**plan, "ents": plan["ents"] + [dict(plan["ents"][0])]}
    _begin_outline()
    with pytest.raises(sqlite3.IntegrityError):
        deps.commit_character_seeds(bad, commit=False)
    conn.rollback()
    assert _count(conn, "outlines") == 0
    assert _count(conn, "characters") == 0
    assert _count(conn, "entities") == 0

    # 重试(完好 plan)→ 大纲 + 角色卡 + 实体 + 别名 + 链接一次成型
    _begin_outline()
    ids = deps.commit_character_seeds(plan, commit=False)
    conn.commit()
    assert len(ids) == 1
    assert _count(conn, "outlines") == 1
    char = conn.execute("SELECT id, entity_id FROM characters").fetchone()
    assert char["entity_id"] == plan["chars"][0]["entity_id"]
    assert _count(conn, "entities") == 2
    aliases = {r["alias"] for r in conn.execute("SELECT alias FROM entity_aliases")}
    assert aliases == {"砚小子", "宗门"}
    assert _count(conn, "entity_links") == 1


# ---------- 6.11 阶段末章摘要并入聚合输入 ----------

def test_stage_summary_includes_current_chapter(tmp_path):
    from app.graph.agents.supervisor import SummaryNode

    captured: list[dict] = []

    class _Entities:
        def stage_touched(self, ctx, start, end):
            return []

    class _FakeDeps:
        entities = _Entities()

        def stage_chapter_summaries(self, story_id, start, upto=0):
            return [(1, "第一章:沈砚发现古碑")]

    def _fake_ask_text(self, system, user, stage, story_id=""):
        captured.append({"stage": stage, "user": user})
        return f"摘要({stage})"

    node = SummaryNode.__new__(SummaryNode)   # 免 LLM 注入,ask_text 下挂桩
    node.ask_text = _fake_ask_text.__get__(node)
    update = node.__call__({"story_id": "s1", "chapter_no": 2, "draft": "正文",
                            "stage_start_chapter": 1, "stage_end_chapter": 2},
                           _FakeDeps())

    assert update["chapter_summary"] == "摘要(chapter_summary)"
    assert update["stage_summary"] == "摘要(stage_summary)"
    merged = [c for c in captured if c["stage"] == "stage_summary"]
    assert len(merged) == 1
    # 落库输入(ch1)+ 内存中的末章自身摘要(ch2)都在,且按章号有序
    assert "第1章:第一章:沈砚发现古碑" in merged[0]["user"]
    assert "第2章:摘要(chapter_summary)" in merged[0]["user"]
    assert merged[0]["user"].index("第1章") < merged[0]["user"].index("第2章")


# ---------- 6.12 用户指令延迟消费 ----------

def test_directives_consumed_only_on_successful_finalize(eng):
    deps, conn = eng
    story_id, branch = deps.repo.create_story("指令消费", "测试")
    d1 = deps.record_directive(story_id, "下一章加强白芷戏份")

    # peek 只读:重复 peek 一致,不落消费标记
    first = deps.peek_pending_directives(story_id)
    second = deps.peek_pending_directives(story_id)
    assert [d["id"] for d in first] == [d1] == [d["id"] for d in second]
    assert conn.execute("SELECT consumed_at FROM user_directives WHERE id=?",
                        (d1,)).fetchone()["consumed_at"] is None

    # 定稿事务中途失败(实体 id 冲突)→ 指令仍 pending,重跑本章仍生效
    with pytest.raises(sqlite3.IntegrityError):
        deps.commit_finalize({"story_id": story_id, "branch_id": branch,
                              "chapter_no": 1, "draft": "正文",
                              "context_bundle": {"user_directive_ids": [d1]},
                              "entity_changes": {"new_entities": [
                                  {"id": "dup", "name": "甲", "type": "concept",
                                   "content": ""},
                                  {"id": "dup", "name": "乙", "type": "concept",
                                   "content": ""}]}})
    assert conn.execute("SELECT consumed_at FROM user_directives WHERE id=?",
                        (d1,)).fetchone()["consumed_at"] is None
    assert _count(conn, "chapters") == 0              # 章节同样未落(整体回滚)

    # 完好定稿 → 指令在同一事务内标记消费
    deps.commit_finalize({"story_id": story_id, "branch_id": branch,
                          "chapter_no": 1, "draft": "正文",
                          "context_bundle": {"user_directive_ids": [d1]}})
    assert conn.execute("SELECT consumed_at FROM user_directives WHERE id=?",
                        (d1,)).fetchone()["consumed_at"] is not None
    assert deps.peek_pending_directives(story_id) == []
