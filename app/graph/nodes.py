"""图内节点函数(ADR-0030 阶段4):中断点、上下文拼装、合并裁决、定稿推进。

节点薄壳原则:业务规则在 application/ports 协作者与 agents 类里,
本模块只做 LangGraph 适配(_node 闭包注入)+ 无 LLM 的编排步骤。
"""

from __future__ import annotations

import functools
import uuid
from datetime import datetime, timezone
from typing import Callable

from langgraph.types import interrupt

from app.core.config import THREAD_LONG_AGE, THREAD_SHORT_AGE, get_settings
from app.graph.agents.base import LLMFormatError
from app.graph.routes import (REWRITE_LIMIT, _master_escalation,
                              _stage_escalation)
from app.graph.runtime import Deps, StopRequested
from app.graph.state import GraphState
from app.memory.repository import AgentContext


def _node(fn: Callable, deps: Deps):
    """把 (state, deps) 节点适配为 langgraph 节点(闭包注入 deps)。

    入口统一做协作式停止检查:用户中断置位后,图在下一个节点边界
    抛 StopRequested 安全退出(checkpointer 保留断点,续跑从此恢复)。
    LLMFormatError(评审类节点自行捕获降级,不落到这里)统一落
    llm_failures 台账后原样上抛——SSE error 携带 trace_id 关联归因。
    ADR-0028:其余异常(provider 429/超时、DB 约束等)同样落台账留痕
    (provider_status_code 取异常自带属性),不因不是"坏 JSON"就失踪。
    """
    @functools.wraps(fn)
    def wrapped(state: GraphState) -> dict:
        story_id = state.get("story_id", "")
        deps.check_stop(story_id)
        try:
            return fn(state, deps)
        except LLMFormatError as exc:
            deps.log_llm_failure(story_id=story_id, stage=exc.stage,
                                 node=getattr(fn, "name", "") or getattr(fn, "__name__", ""),
                                 exc=exc)
            raise
        except StopRequested:
            raise
        except Exception as exc:  # noqa: BLE001 — 落痕后原样上抛
            deps.log_llm_failure(story_id=story_id, stage=getattr(exc, "stage", ""),
                                 node=getattr(fn, "name", "") or getattr(fn, "__name__", ""),
                                 exc=exc)
            raise
    return wrapped


# ---------- 中断点 ----------

def confirm_master_outline(state: GraphState, deps: Deps) -> dict:
    # 总大纲确认永不由自动模式代签(所有者裁决,ADR-0016):
    # 整本书的根基方向必须作者亲自拍板,与审校绿否无关
    review = state.get("outline_verdict", {})
    decision = interrupt({
        "type": "confirm_master_outline",
        "outline": state.get("master_outline", ""),
        "review": review,
        "escalation": _master_escalation(state),
    })
    if decision.get("action") == "revise":
        return {"user_input": decision, "outline_confirmed": False}
    # 共创产物落库(确认前零残留,ADR-0011):准备阶段(embedding 计算)在
    # 事务外,失败则根本不进事务;写入阶段与总大纲归档/落库并入单事务
    # (评审 6.8 / ADR-0024)——中途失败整体回滚,不再出现"大纲已确认但
    # 角色卡缺失"的半成品,重试即完整重放
    plan = deps.prepare_character_seeds(state)
    with deps.run_lock:
        deps.conn.execute("BEGIN")
        try:
            deps.conn.execute(
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
            deps.commit_character_seeds(plan, commit=False)
            deps.conn.commit()
        except Exception:
            deps.conn.rollback()
            raise
    return {"user_input": decision, "outline_confirmed": True,
            "chapter_no": 1, "chapters_done": 0}


def confirm_stage_outline(state: GraphState, deps: Deps) -> dict:
    escalation = _stage_escalation(state)
    if state.get("auto_mode") and not escalation:
        decision = {"action": "confirm"}
    else:
        decision = interrupt({
            "type": "confirm_stage_outline",
            "stage_outline": state.get("stage_outline", ""),
            "review": state.get("outline_verdict", {}),
            "regen_count": state.get("stage_regen_count", 1),
            "escalation": escalation,   # 自动中断原因(非空=循环转人工)
        })
    return {"user_input": decision,
            "chapter_no": (state.get("chapters_done", 0) + 1)}


def user_review_chapter(state: GraphState, deps: Deps) -> dict:
    """中断点 B:章节审阅 + 伏笔人工二次确认(ADR-0007)。

    自动模式(ADR-0016):双评审 pass 直通定稿,伏笔变更随评审建议自动生效
    (Codex 台账可事后查阅);rewrite_exhausted(重写 3 次仍未过)强制回人工。
    """
    exhausted = bool(state.get("rewrite_exhausted"))
    if state.get("auto_mode") and not exhausted:
        decision = {"action": "confirm",
                    "threads": state.get("thread_review", {}).get("thread_changes", [])}
    else:
        decision = interrupt({
            "type": "user_review_chapter",
            "chapter_no": state.get("chapter_no"),
            "draft": state.get("draft", ""),
            "outline_review": state.get("outline_review", {}),
            "quality_review": state.get("quality_review", {}),
            "thread_review": state.get("thread_review", {}),
            "thread_changes": state.get("thread_review", {}).get("thread_changes", []),
            "conflicts": state.get("fact_changes", {}).get("conflicts", []),
            "rewrite_exhausted": exhausted,
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
        # 长尾召回(ADR-0024 分级硬过滤:信息差事实在检索层滤除,背景事实保留)
        "vector_hits": result.vector_hits,
    }
    bundle["carryover"] = deps.recent_carryover(state)
    # ADR-0017 信息差与自适应痕迹治理
    chapter_no = state["chapter_no"]
    for t in result.active_threads:
        planted = t.get("planted_chapter") or 0
        t["_suspend"] = bool(planted and 0 < chapter_no - planted < 3)
        # ADR-0020 账龄分级:超 tier 账龄线的活跃伏笔标"应回收"
        # (升格过的按 long 线重算;NULL=存量未回填,按 short 保守催收)
        tier = t.get("tier") or "short"
        limit = THREAD_LONG_AGE if tier == "long" else THREAD_SHORT_AGE
        t["_overdue"] = bool(
            planted and chapter_no - planted > limit and not t.get("escalated_chapter"))
    # 剧情承载短语豁免(ADR-0035):要点/细纲/活跃伏笔/上期衔接触及的短语
    # 不进禁用清单——剧情死线跨章高频是叙事骨架,不是复读口头禅
    excl = [state.get("chapter_brief", ""), state.get("stage_outline", "")]
    excl += [t.get("description", "") for t in result.active_threads]
    excl.append(bundle.get("carryover") or "")
    bundle["style_ban"] = deps.recent_phrase_blacklist(
        state["story_id"], chapter_no,
        exclude_texts=[t for t in excl if t])
    # ADR-0019 规范名词典:叙述层统一用名(实体表 canonical + 别名)
    bundle["canonical_names"] = deps.canonical_entity_registry(state["story_id"])
    present = set(present_ids)
    intents = [i for i in state.get("character_intents", [])
               if i.get("character_id") in present]
    if intents:
        bundle["character_intents"] = intents
    # 用户指令通道:注入挂起指示(最高优先级);消费标记延迟到定稿事务(ADR-0024)
    directives = deps.peek_pending_directives(state["story_id"])
    if directives:
        bundle["user_directives"] = [d["content"] for d in directives]
        bundle["user_directive_ids"] = [d["id"] for d in directives]
    stats = {
        "present": len(present_ids),
        "pov_facts": len(result.pov_facts),
        "beliefs": len(result.beliefs),
        "threads": len(result.active_threads),
        "expanded_entities": len(result.expanded_entities),
        "user_directives": len(directives),
        "style_ban": len(bundle["style_ban"]),
        "canonical_names": len(bundle["canonical_names"]),
        "intents": len(intents),
    }
    return {"context_bundle": bundle, "present_characters": present_ids,
            "context_stats": stats}


def merge_reviews(state: GraphState, deps: Deps) -> dict:
    """fan-in:双 pass 才通过;达重写上限不再自动通过——转交用户裁决(needs_user)。

    字数下限兜底(ADR-0034):双 pass 但草稿低于下限 → 强制 revise(writer
    自查补写与评审契约都漏掉时的最后一道代码闸),复用 rewrite_count/
    REWRITE_LIMIT 轮次上限,不新增循环机制。
    """
    o = state.get("outline_review", {})
    q = state.get("quality_review", {})
    verdicts = [v.get("verdict", "revise") for v in (o, q)]
    floor = get_settings().chapter_min_chars
    too_short = floor > 0 and len(state.get("draft") or "") < floor
    if all(v == "pass" for v in verdicts) and not too_short:
        return {"merged_verdict": "pass", "rewrite_exhausted": False}
    if all(v == "pass" for v in verdicts) and too_short:
        deps.log_review(state, reviewer="merge", forced=True,
                        verdict={"verdict": "revise",
                                 "feedback": (f"字数不足:草稿 {len(state.get('draft') or '')} 字"
                                              f"低于下限 {floor} 字,补足情节后重写")},
                        round_no=state.get("rewrite_count", 0) + 1)
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


def finalize(state: GraphState, deps: Deps) -> dict:
    """定稿管道(编排原子性):抽取 -> 角色更新 -> 摘要 -> 单事务落库。

    revamp 轮(ReAct 重构历史章,ADR-0031 P1):覆盖旧章,chapters_done
    不增——章数没变,只是某章换了新版本(旧版归档可回溯)。
    """
    chapter_id = deps.commit_finalize(state)
    if state.get("revamp_pending"):
        return {"chapter_id": chapter_id, "revamp_done": True}
    done = state.get("chapters_done", 0) + 1
    return {"chapter_id": chapter_id, "chapters_done": done}


def next_chapter(state: GraphState, deps: Deps) -> dict:
    """进入下一章:阶段边界以细纲实际覆盖范围(stage_end_chapter)为准。

    旧版按 done % 3 硬切导致卷结构失真与剧情重排,改为:
    下一章超出当前阶段范围(或尚无细纲)才重新生成阶段细纲。
    """
    done = state.get("chapters_done", 0)
    stage_end = state.get("stage_end_chapter", 0)
    is_stage_first = (done + 1 > stage_end) or not state.get("stage_outline")
    reset = {"rewrite_count": 0, "rewrite_exhausted": False,
             "revamp_pending": False, "revamp_done": False}   # revamp 标记不跨章残留
    if is_stage_first:
        reset["stage_regen_count"] = 0          # 新阶段:细纲轮次重新计
    return {"chapter_no": done + 1, "is_stage_first": is_stage_first, **reset}
