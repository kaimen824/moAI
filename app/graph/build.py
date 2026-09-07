"""图装配(ADR-0007 生产循环 + ADR-0011 共创前置)。

三类 human-in-the-loop 中断(interrupt + Command(resume=...) 恢复):
  中断点 0:总大纲确认   resume {"action": "confirm|revise", "feedback": str}
  中断点 A:阶段细纲确认 resume 同上
  中断点 B:章节审阅     resume {"action": "confirm|revise",
                                "feedback": str,
                                "threads": [被人工确认的伏笔变更]}
双评审 fan-out/fan-in;重写上限 3(可配);达上限不自动降级——
rewrite_exhausted 交用户裁决(needs_user)。
"""

from __future__ import annotations

import functools
from typing import Callable

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.graph.agents.character_manager import InitCharactersNode, UpdateCharactersNode
from app.graph.agents.event_extractor import EventExtractNode
from app.graph.agents.outline_reviewer import (
    ReviewDraftOutline,
    ReviewMasterOutline,
    ReviewStageOutline,
)
from app.graph.agents.quality_reviewer import QualityReviewNode
from app.graph.agents.supervisor import (
    ChapterSliceNode,
    CoauthorNode,
    GenMasterOutline,
    StageOutlineNode,
    SummaryNode,
)
from app.graph.agents.writer import WriterNode
from app.graph.runtime import Deps
from app.graph.state import GraphState
from app.memory.repository import AgentContext

REWRITE_LIMIT = 3   # ADR-0007 裁决 4(可配置)
STAGE_REGEN_LIMIT = 3        # 细纲重生成上限:超过自动转用户(行业惯例:escalate to human)


def _stage_escalation(state: GraphState) -> str | None:
    """细纲循环自动中断原因(None=无需中断,继续 regen)。

    注:不做相邻版本相似度检测——网文细纲重生成是"评审指出局部问题、
    保留剧情骨架做局部修订",相邻版本高度相似是正常语义,不是空转;
    空转风险由轮次上限兜底(所有者裁决)。
    """
    verdict = state.get("outline_verdict", {}).get("verdict", "revise")
    if verdict not in ("revise", "block"):
        return None
    count = state.get("stage_regen_count", 1)
    if count >= STAGE_REGEN_LIMIT:
        return f"细纲已重生成 {count} 轮仍未通过评审,自动转交你裁决"
    return None


def _node(fn: Callable, deps: Deps):
    """把 (state, deps) 节点适配为 langgraph 节点(闭包注入 deps)。

    入口统一做协作式停止检查:用户中断置位后,图在下一个节点边界
    抛 StopRequested 安全退出(checkpointer 保留断点,续跑从此恢复)。
    """
    @functools.wraps(fn)
    def wrapped(state: GraphState) -> dict:
        deps.check_stop(state.get("story_id", ""))
        return fn(state, deps)
    return wrapped


# ---------- 中断点 ----------

def confirm_master_outline(state: GraphState, deps: Deps) -> dict:
    review = state.get("outline_verdict", {})
    decision = interrupt({
        "type": "confirm_master_outline",
        "outline": state.get("master_outline", ""),
        "review": review,
    })
    if decision.get("action") == "revise":
        return {"user_input": decision, "outline_confirmed": False}
    import uuid
    from datetime import datetime, timezone
    with deps.run_lock:
        deps.repo.conn.execute(
            "UPDATE outlines SET status='archived' WHERE story_id=? AND status='confirmed'",
            (state["story_id"],))
        # 总大纲确认落库(主控写入,带审计;首版)
        deps.conn.execute(
            "INSERT OR REPLACE INTO outlines (id, story_id, version_no, content, status, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (uuid.uuid4().hex, state["story_id"],
             (deps.conn.execute("SELECT COUNT(*) c FROM outlines WHERE story_id=?",
                                (state["story_id"],)).fetchone()["c"] + 1),
             state["master_outline"], "confirmed",
             datetime.now(timezone.utc).isoformat(timespec="seconds")))
        deps.conn.commit()
    # 角色卡随总大纲确认一起落库(共创产物先暂存 state,确认前不写库——
    # 用户放弃/重来时零残留;ADR-0011 共创前置的落库时点修正)
    deps.persist_characters(state)
    return {"user_input": decision, "outline_confirmed": True,
            "chapter_no": 1, "chapters_done": 0}


def confirm_stage_outline(state: GraphState, deps: Deps) -> dict:
    decision = interrupt({
        "type": "confirm_stage_outline",
        "stage_outline": state.get("stage_outline", ""),
        "review": state.get("outline_verdict", {}),
        "regen_count": state.get("stage_regen_count", 1),
        "escalation": _stage_escalation(state),   # 自动中断原因(非空=循环转人工)
    })
    return {"user_input": decision,
            "chapter_no": (state.get("chapters_done", 0) + 1)}


def user_review_chapter(state: GraphState, deps: Deps) -> dict:
    """中断点 B:章节审阅 + 伏笔人工二次确认(ADR-0007)。

    rewrite_exhausted=True 表示已达重写上限且评审仍未通过——
    由用户裁决:带着评审意见定稿(confirm)或再改写(revise)。
    """
    decision = interrupt({
        "type": "user_review_chapter",
        "chapter_no": state.get("chapter_no"),
        "draft": state.get("draft", ""),
        "outline_review": state.get("outline_review", {}),
        "quality_review": state.get("quality_review", {}),
        "thread_changes": state.get("quality_review", {}).get("thread_changes", []),
        "conflicts": state.get("fact_changes", {}).get("conflicts", []),
        "rewrite_exhausted": state.get("rewrite_exhausted", False),
    })
    update = {"user_input": decision,
              "thread_changes": decision.get("threads", [])}   # 人工确认后的伏笔变更
    if decision.get("action") == "revise":
        # 用户意见驱动的重写独立计数:重置,不与自动重写共享上限
        update.update({"rewrite_count": 0, "rewrite_exhausted": False})
    return update


# ---------- 检索 / 合并 / 定稿 ----------

def build_context(state: GraphState, deps: Deps) -> dict:
    ctx = AgentContext("writer", state["story_id"])
    present = deps.character_name_map(state)
    # 在场角色:从本章要点提取名字(确定性优先,LLM 兜底可后置)
    brief = state.get("chapter_brief", "")
    present_ids = [cid for name, cid in present.items() if name in brief] or list(present.values())[:2]
    result = deps.retrieval.retrieve_for_chapter(
        ctx, state["chapter_no"], present_ids,
        query_text=brief[:200] or None,
    )
    bundle = {
        "characters": result.characters,
        "pov_facts": result.pov_facts,
        "beliefs": result.beliefs,
        "active_threads": result.active_threads,
        "expanded_entities": result.expanded_entities,
    }
    bundle["carryover"] = deps.recent_carryover(state)
    # 用户指令通道:消费挂起的指示,注入本章上下文(最高优先级)
    directives = deps.take_pending_directives(state["story_id"])
    if directives:
        bundle["user_directives"] = directives
    stats = {
        "present": len(present_ids),
        "pov_facts": len(result.pov_facts),
        "beliefs": len(result.beliefs),
        "threads": len(result.active_threads),
        "expanded_entities": len(result.expanded_entities),
        "user_directives": len(directives),
    }
    return {"context_bundle": bundle, "present_characters": present_ids,
            "context_stats": stats}


def merge_reviews(state: GraphState, deps: Deps) -> dict:
    """fan-in:双 pass 才通过;达重写上限不再自动通过——转交用户裁决(needs_user)。"""
    o = state.get("outline_review", {})
    q = state.get("quality_review", {})
    verdicts = [v.get("verdict", "revise") for v in (o, q)]
    if all(v == "pass" for v in verdicts):
        return {"merged_verdict": "pass", "rewrite_exhausted": False}
    count = state.get("rewrite_count", 0)
    if count + 1 >= REWRITE_LIMIT:
        deps.log_review(state, reviewer="merge", forced=True,
                        verdict={"verdict": "needs_user",
                                 "feedback": "已达重写上限,评审仍未通过,转交用户裁决"},
                        round_no=count + 1)
        return {"merged_verdict": "needs_user", "rewrite_exhausted": True,
                "rewrite_count": count + 1}
    return {"merged_verdict": "revise", "rewrite_exhausted": False,
            "rewrite_count": count + 1}


def route_after_merge(state: GraphState) -> str:
    v = state.get("merged_verdict", "revise")
    if v == "revise":
        return "rewrite"
    return "user_review"       # pass / needs_user 均交用户


def route_after_review(state: GraphState) -> str:
    decision = state.get("user_input") or {}
    if decision.get("action") == "revise":
        return "rewrite_with_feedback"
    return "finalize"


def finalize(state: GraphState, deps: Deps) -> dict:
    """定稿管道(编排原子性):抽取 -> 角色更新 -> 摘要 -> 单事务落库。"""
    chapter_id = deps.commit_finalize(state)
    done = state.get("chapters_done", 0) + 1
    return {"chapter_id": chapter_id, "chapters_done": done}


def route_next(state: GraphState) -> str:
    if state.get("chapters_done", 0) >= state.get("target_chapters", 1):
        return END
    return "next_chapter"


def next_chapter(state: GraphState, deps: Deps) -> dict:
    """进入下一章:阶段边界以细纲实际覆盖范围(stage_end_chapter)为准。

    旧版按 done % 3 硬切导致卷结构失真与剧情重排,改为:
    下一章超出当前阶段范围(或尚无细纲)才重新生成阶段细纲。
    """
    done = state.get("chapters_done", 0)
    stage_end = state.get("stage_end_chapter", 0)
    is_stage_first = (done + 1 > stage_end) or not state.get("stage_outline")
    reset = {"rewrite_count": 0, "rewrite_exhausted": False}
    if is_stage_first:
        reset["stage_regen_count"] = 0          # 新阶段:细纲轮次重新计
    return {"chapter_no": done + 1, "is_stage_first": is_stage_first, **reset}


def route_chapter_entry(state: GraphState) -> str:
    return "stage_outline" if state.get("is_stage_first") else "chapter_slice"


def route_entry(state: GraphState) -> str:
    """图入口路由:已有定稿章节或确认大纲 -> 续写(直接下一章);否则完整共创。
    判据用 chapters_done(DB 定稿数同步)优先——outline_confirmed 可能被
    历史中断的重跑污染为 False,chapters_done 只增不减,更稳。"""
    if state.get("chapters_done", 0) > 0 or state.get("outline_confirmed"):
        return "next_chapter"
    return "coauthor"


def route_after_stage_review(state: GraphState) -> str:
    decision = state.get("user_input") or {}
    if decision.get("action") == "revise":
        return "regen_stage"
    return "build_context"


def route_after_stage_agent_review(state: GraphState) -> str:
    """阶段细纲:评审不过 -> 回炉重生成;但循环空转自动中断
    (超 STAGE_REGEN_LIMIT 轮,或重生成结果与上一版几乎相同)-> 转用户裁决。"""
    if _stage_escalation(state):
        return "confirm_stage"
    verdict = state.get("outline_verdict", {}).get("verdict", "revise")
    if verdict in ("revise", "block"):
        return "regen_stage"
    return "confirm_stage"


def route_after_master_review(state: GraphState) -> str:
    """总大纲评审通过才进入用户确认;不通过回主控重生成。"""
    verdict = state.get("outline_verdict", {}).get("verdict", "revise")
    if verdict in ("revise", "block"):
        return "regen_master"
    return "confirm_master"


# ---------- 图装配 ----------

def build_graph(deps: Deps, checkpointer=None):
    g = StateGraph(GraphState)

    # 共创前置(ADR-0011;角色草案只暂存 state,确认总大纲时才落库)
    g.add_node("coauthor", _node(CoauthorNode(deps.llm), deps))
    g.add_node("init_characters", _node(InitCharactersNode(deps.llm), deps))
    g.add_node("persist_characters", lambda s: {"character_drafts": s.get("character_drafts", [])})
    g.add_node("gen_master_outline", _node(GenMasterOutline(deps.llm), deps))
    g.add_node("review_master_outline", _node(ReviewMasterOutline(deps.llm), deps))
    g.add_node("confirm_master_outline", functools.partial(confirm_master_outline, deps=deps))

    # 生产循环
    g.add_node("next_chapter", _node(next_chapter, deps))
    g.add_node("stage_outline", _node(StageOutlineNode(deps.llm), deps))
    g.add_node("review_stage_outline", _node(ReviewStageOutline(deps.llm), deps))
    g.add_node("confirm_stage_outline", functools.partial(confirm_stage_outline, deps=deps))
    g.add_node("chapter_slice", _node(ChapterSliceNode(deps.llm), deps))
    g.add_node("build_context", _node(build_context, deps))
    g.add_node("write_draft", _node(WriterNode(deps.llm), deps))
    g.add_node("review_draft_outline", _node(ReviewDraftOutline(deps.llm), deps))
    g.add_node("review_quality", _node(QualityReviewNode(deps.llm), deps))
    g.add_node("merge_reviews", _node(merge_reviews, deps))
    g.add_node("user_review_chapter", functools.partial(user_review_chapter, deps=deps))
    g.add_node("event_extract", _node(EventExtractNode(deps.llm), deps))
    g.add_node("update_characters", _node(UpdateCharactersNode(deps.llm), deps))
    g.add_node("summary", _node(SummaryNode(deps.llm), deps))
    g.add_node("finalize", _node(finalize, deps))

    # 共创边(入口路由:续写跳过共创,ADR-0011 只走一次)
    g.add_conditional_edges(
        START, route_entry,
        {"coauthor": "coauthor", "next_chapter": "next_chapter"},
    )
    g.add_edge("coauthor", "init_characters")
    g.add_edge("init_characters", "persist_characters")
    g.add_edge("persist_characters", "gen_master_outline")
    g.add_edge("gen_master_outline", "review_master_outline")
    g.add_conditional_edges(
        "review_master_outline", route_after_master_review,
        {"regen_master": "gen_master_outline", "confirm_master": "confirm_master_outline"},
    )
    g.add_conditional_edges(
        "confirm_master_outline",
        lambda s: "next_chapter" if s.get("outline_confirmed") else "gen_master_outline",
        {"next_chapter": "next_chapter", "gen_master_outline": "gen_master_outline"},
    )

    # 章节入口(阶段首章 / 切片)
    g.add_conditional_edges(
        "next_chapter", route_chapter_entry,
        {"stage_outline": "stage_outline", "chapter_slice": "chapter_slice"},
    )
    g.add_edge("stage_outline", "review_stage_outline")
    g.add_conditional_edges(
        "review_stage_outline", route_after_stage_agent_review,
        {"regen_stage": "stage_outline", "confirm_stage": "confirm_stage_outline"},
    )
    g.add_conditional_edges(
        "confirm_stage_outline", route_after_stage_review,
        {"regen_stage": "stage_outline", "build_context": "build_context"},
    )
    g.add_edge("chapter_slice", "build_context")
    g.add_edge("build_context", "write_draft")

    # 双评审 fan-out / fan-in(并行)
    g.add_edge("write_draft", "review_draft_outline")
    g.add_edge("write_draft", "review_quality")
    g.add_edge("review_draft_outline", "merge_reviews")
    g.add_edge("review_quality", "merge_reviews")
    g.add_conditional_edges(
        "merge_reviews", route_after_merge,
        {"rewrite": "write_draft", "user_review": "user_review_chapter"},
    )
    g.add_conditional_edges(
        "user_review_chapter", route_after_review,
        {"rewrite_with_feedback": "write_draft", "finalize": "event_extract"},
    )

    # 定稿管道
    g.add_edge("event_extract", "update_characters")
    g.add_edge("update_characters", "summary")
    g.add_edge("summary", "finalize")
    g.add_conditional_edges("finalize", route_next, {END: END, "next_chapter": "next_chapter"})

    return g.compile(checkpointer=checkpointer)
