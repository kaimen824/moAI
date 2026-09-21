"""路由谓词(ADR-0030 阶段4):条件边的纯函数判定,只读 state。

纯函数(无 deps、无副作用),可独立单测;wiring 将其映射到条件边。
"""

from __future__ import annotations

from langgraph.graph import END

from app.graph.state import GraphState

REWRITE_LIMIT = 3   # ADR-0007 裁决 4(可配置)——结构闸(大纲一致性)重写上限
STYLE_POLISH_LIMIT = 2      # 风格闸精校上限(ADR-0036 串行修复,独立计数)
STAGE_REGEN_LIMIT = 3        # 细纲重生成上限:超过自动转用户(行业惯例:escalate to human)
MASTER_REGEN_LIMIT = 3       # 总大纲重生成上限(ADR-0016:与细纲/重写同口径)


def route_after_struct_review(state: GraphState) -> str:
    """结构闸(ADR-0036):大纲一致性评审先行——revise 带结构反馈全文重写,
    过了才进风格闸;耗尽转人工。字数下限兜底也在此(内容问题归结构)。"""
    v = state.get("struct_verdict", "revise")
    if v == "pass":
        return "style_review"
    if v == "needs_user":
        return "user_review"
    return "rewrite"


def route_after_style_review(state: GraphState) -> str:
    """风格闸(ADR-0036):结构过了才跑——revise 走精校(局部修,不重掷
    全文骰子),过了交人审;精校上限独立计数,耗尽转人工。"""
    v = state.get("merged_verdict", "revise")
    if v == "pass":
        return "user_review"
    if v == "needs_user":
        return "user_review"
    return "polish"


def route_after_review(state: GraphState) -> str:
    """人审打回分阶段(ADR-0036):结构闸已过(struct_verdict=pass)的打回
    从风格节点续修——精校带用户意见,不全文重掷、不重过结构闸;
    结构闸本身未过/耗尽的打回仍走全文重写。"""
    decision = state.get("user_input") or {}
    if decision.get("action") == "revise":
        if state.get("struct_verdict") == "pass":
            return "polish_with_feedback"
        return "rewrite_with_feedback"
    return "finalize"


def route_next(state: GraphState) -> str:
    # ReAct 重构历史章(ADR-0031 P1):定稿即终点——revamp 只覆盖一章,
    # 不进入下一章生产循环;标记由 next_chapter 兜底清理
    if state.get("revamp_pending") or state.get("revamp_done"):
        return END
    if state.get("chapters_done", 0) >= state.get("target_chapters", 1):
        return END
    return "next_chapter"


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
    """细纲确认后必经 chapter_slice:阶段首章若直连写作,state 里残留的
    上一阶段末章 chapter_brief 会被 writer/一致性评审当成"本章基准"
    (ch19 串章事故:评审拿 ch18 要点逐条审 ch19 草稿,三轮乒乓转人工)。"""
    decision = state.get("user_input") or {}
    if decision.get("action") == "revise":
        return "regen_stage"
    return "chapter_slice"


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
    """总大纲评审通过才进入用户确认;不通过回主控重生成——
    但 3 轮上限(ADR-0016)后不再回炉,带着评审意见转用户裁决。"""
    if _master_escalation(state):
        return "confirm_master"
    verdict = state.get("outline_verdict", {}).get("verdict", "revise")
    if verdict in ("revise", "block"):
        return "regen_master"
    return "confirm_master"


# ---------- 升格判定(路由谓词的共享辅助,同样纯读 state)----------

def _master_escalation(state: GraphState) -> str | None:
    """总大纲循环自动中断原因(None=无需中断)。

    历史上总大纲回炉无上限(靠 recursion_limit 兜底);ADR-0016 起与
    细纲/重写同口径:3 轮未过评审即转用户,两种模式一致。
    """
    verdict = state.get("outline_verdict", {}).get("verdict", "revise")
    if verdict not in ("revise", "block"):
        return None
    count = state.get("master_regen_count", 1)
    if count >= MASTER_REGEN_LIMIT:
        return f"总大纲已重生成 {count} 轮仍未通过评审,自动转交你裁决"
    return None


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
