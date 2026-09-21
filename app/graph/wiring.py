"""图装配(ADR-0007 生产循环 + ADR-0011 共创前置;ADR-0030 阶段4 自 build.py 分出)。

三类 human-in-the-loop 中断(interrupt + Command(resume=...) 恢复):
  中断点 0:总大纲确认   resume {"action": "confirm|revise", "feedback": str}
  中断点 A:阶段细纲确认 resume 同上
  中断点 B:章节审阅     resume {"action": "confirm|revise",
                                "feedback": str,
                                "threads": [被人工确认的伏笔变更]}
串行双闸(ADR-0036):结构闸(大纲一致性+字数)先行,revise 全文重写
(REWRITE_LIMIT=3);过了才进风格闸(文风/复读),revise 走精校局部修
(STYLE_POLISH_LIMIT=2 轮);任一闸耗尽转人工,不自动降级。伏笔评审与
结构评审并行 fan-out,不参与闸门表决。

本模块只做纯结构接线:节点实现在 agents/ 与 nodes.py,路由判定在
routes.py;这里看不到业务规则。
"""

from __future__ import annotations

import functools

from langgraph.graph import END, START, StateGraph

from app.graph.agents.character_manager import InitCharactersNode, UpdateCharactersNode
from app.graph.agents.entity_resolver import EntityResolveNode
from app.graph.agents.event_extractor import EventExtractNode
from app.graph.agents.polisher import PolishDraftNode
from app.graph.agents.outline_reviewer import (
    ReviewDraftOutline,
    ReviewMasterOutline,
    ReviewStageOutline,
)
from app.graph.agents.quality_reviewer import QualityReviewNode
from app.graph.agents.thread_reviewer import ThreadReviewNode
from app.graph.agents.supervisor import (
    ChapterSliceNode,
    CoauthorNode,
    GenMasterOutline,
    StageOutlineNode,
    SummaryNode,
)
from app.graph.agents.writer import WriterNode
from app.graph.nodes import (
    _node,
    build_context,
    confirm_master_outline,
    confirm_stage_outline,
    finalize,
    next_chapter,
    struct_merge,
    style_merge,
    user_review_chapter,
)
from app.graph.routes import (
    route_after_master_review,
    route_after_review,
    route_after_stage_agent_review,
    route_after_stage_review,
    route_after_struct_review,
    route_after_style_review,
    route_chapter_entry,
    route_entry,
    route_next,
)
from app.graph.runtime import Deps
from app.graph.state import GraphState


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
    # 文风返工通道(ADR-0018;ADR-0036 串行化后专属风格闸,精校不回结构)
    g.add_node("polish_draft", _node(PolishDraftNode(deps.llm), deps))
    g.add_node("review_draft_outline", _node(ReviewDraftOutline(deps.llm), deps))
    g.add_node("review_quality", _node(QualityReviewNode(deps.llm), deps))
    # 伏笔评审(ADR-0020 单独评审):与结构评审并列 fan-out,无 pass/revise
    # 裁决,不参与闸门表决——只产 thread_changes 与超龄复核
    g.add_node("review_threads", _node(ThreadReviewNode(deps.llm), deps))
    g.add_node("struct_merge", _node(struct_merge, deps))
    g.add_node("style_merge", _node(style_merge, deps))
    g.add_node("user_review_chapter", functools.partial(user_review_chapter, deps=deps))
    g.add_node("event_extract", _node(EventExtractNode(deps.llm), deps))
    g.add_node("update_characters", _node(UpdateCharactersNode(deps.llm), deps))
    # 实体消歧(ADR-0015):与角色更新/摘要并列——三者都只依赖抽取产物/草稿,
    # 同一 super-step 并行,fan-in 到定稿单事务(不给串行链加深度)
    g.add_node("entity_resolve", _node(EntityResolveNode(deps.llm), deps))
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
        {"regen_stage": "stage_outline", "chapter_slice": "chapter_slice"},
    )
    g.add_edge("chapter_slice", "build_context")
    g.add_edge("build_context", "write_draft")

    # 串行双闸(ADR-0036):结构闸先行(大纲一致性+字数,revise 全文重写),
    # 过了才进风格闸(文风/复读,revise 精校局部修)——拆掉合取闸门,
    # 每轮反馈单维,治"修结构顾不上风格"的打地鼠死锁;伏笔评审与结构
    # 并行 fan-out(不表决),风格闸只跑 review_quality
    g.add_edge("write_draft", "review_draft_outline")
    g.add_edge("write_draft", "review_threads")
    g.add_edge("review_draft_outline", "struct_merge")
    g.add_edge("review_threads", "struct_merge")
    g.add_conditional_edges(
        "struct_merge", route_after_struct_review,
        {"rewrite": "write_draft", "style_review": "review_quality",
         "user_review": "user_review_chapter"},
    )
    g.add_edge("review_quality", "style_merge")
    g.add_conditional_edges(
        "style_merge", route_after_style_review,
        {"polish": "polish_draft", "user_review": "user_review_chapter"},
    )
    g.add_edge("polish_draft", "review_quality")
    g.add_conditional_edges(
        "user_review_chapter", route_after_review,
        {"rewrite_with_feedback": "write_draft",
         "polish_with_feedback": "polish_draft",   # 结构已过的打回:风格节点续修(ADR-0036)
         "finalize": "event_extract"},
    )

    # 定稿管道:抽取 -> [角色更新 ∥ 实体消歧 ∥ 摘要] -> 单事务定稿(fan-out/fan-in)
    g.add_edge("event_extract", "update_characters")
    g.add_edge("event_extract", "entity_resolve")
    g.add_edge("event_extract", "summary")
    g.add_edge("update_characters", "finalize")
    g.add_edge("entity_resolve", "finalize")
    g.add_edge("summary", "finalize")
    g.add_conditional_edges("finalize", route_next, {END: END, "next_chapter": "next_chapter"})

    return g.compile(checkpointer=checkpointer)
