"""大纲 Agent(ADR-0005):独立 critic,只读,结构化裁决。

三种评审模式共用裁决契约:
  {"verdict": "pass|revise|block", "scores": {"consistency": 0-10, "structure": 0-10},
   "feedback": "..."}
"""

from __future__ import annotations

from app.core.config import AgentRole
from app.graph.agents.base import BaseAgent, NodeDeps, register_agent

_SYSTEM = (
    "你是大纲一致性评审员(独立评审,不参与创作)。严格按 JSON 输出:"
    '{"verdict":"pass|revise|block","scores":{"consistency":0-10,"structure":0-10},'
    '"fix_scope":"style|local|content","feedback":"评审意见"}。\n'
    "consistency 评与基准的吻合度,structure 评结构完备度。低于 7 分给 revise,严重矛盾给 block。\n"
    "fix_scope(成稿评审必填;大纲类评审填 content):revise 时标注问题性质——\n"
    "- style:纯文风问题(措辞/节奏/冗余/复读表达/措辞层面的倾泻);\n"
    "- local:局部事实修正,且必须在'必须修改'里给出精确处方(将X改为Y:"
    "数值衔接、称谓统一、单句事实更正等),不含任何结构调整;\n"
    "- content:大范围结构性偏离(增删场景/改因果/改人物行动逻辑/能力越权/"
    "信息边界——未必是偏离大纲)。\n"
    "反AI检查(命中任一,consistency 至少扣 2 分并在 feedback 逐条指出):\n"
    "- 能力越权:关键结论(谁做的/为什么/怎么办)由能力、系统或直觉直接给出,"
    "而非人物观察推理得出;\n"
    "- 信息倾泻:连续超过三行的设定罗列、数值播报或面板堆砌;\n"
    "- 主角全知:无信息来源却正确的推断;\n"
    "- 无代价胜利:本章目标达成且没有付出、损失或遗留问题;\n"
    "- 复读表达:与近章高度重复的句式或口头禅(参见[禁用表达]清单,若提供)。\n"
    "feedback 必须具体可执行,无论结论如何都要给:① 至多一条亮点,且不得是"
    "'延续既有风格'式的加码夸奖(如'更多数据流描写很精彩'——这正是要抑制的);"
    "② 风险或待改点(pass 也要列);"
    "③ revise/block 时逐条列出必须修改的内容与建议改法。禁止只写'结构完整''整体良好'这类空话。"
)


@register_agent
class ReviewMasterOutline(BaseAgent):
    name = "outline_agent"
    role = AgentRole.OUTLINE

    def __call__(self, state: dict, deps: NodeDeps) -> dict:
        verdict = self.ask_json(
            _SYSTEM,
            f"[评审对象] 总大纲\n{state.get('master_outline','')}\n\n"
            f"[基准] 世界观设定\n{state.get('world_settings','')}",
            stage="review_master_outline",
            story_id=state.get("story_id", ""),
        )
        deps.log_review(state, reviewer="outline", verdict=verdict, round_no=0)
        return {"outline_verdict": verdict}


@register_agent
class ReviewStageOutline(BaseAgent):
    name = "outline_agent"
    role = AgentRole.OUTLINE

    def __call__(self, state: dict, deps: NodeDeps) -> dict:
        verdict = self.ask_json(
            _SYSTEM,
            f"[评审对象] 阶段细纲\n{state.get('stage_outline','')}\n\n"
            f"[基准] 总大纲\n{state.get('master_outline','')}\n\n"
            f"[已完成剧情回顾(细纲不得与其中事件重复,重复即 revise)]\n"
            f"{deps.story_recap(state)}\n\n"
            f"[已完成]{state.get('chapters_done',0)} 章",
            stage="review_stage_outline",
            story_id=state.get("story_id", ""),
        )
        deps.log_review(state, reviewer="outline", verdict=verdict, round_no=0)
        return {"outline_verdict": verdict}


@register_agent
class ReviewDraftOutline(BaseAgent):
    """成稿一致性评审(fan-out 之一,ADR-0007 裁决 3)。"""

    name = "outline_agent"
    role = AgentRole.OUTLINE

    def __call__(self, state: dict, deps: NodeDeps) -> dict:
        ban = "\n".join(f"- {p}" for p in
                        state.get("context_bundle", {}).get("style_ban", []))
        verdict = self.ask_json(
            _SYSTEM.replace('"structure":0-10', '"fidelity":0-10'),
            f"[评审对象] 本章正文草稿\n{state.get('draft','')}\n\n"
            f"[基准] 本章要点\n{state.get('chapter_brief','')}\n\n"
            f"[基准] 总大纲(当前卷)\n{state.get('master_outline','')[:1500]}\n\n"
            f"[上期衔接(若草稿重演/复述其中已发生事件,判 revise)]\n"
            f"{deps.recent_carryover(state)}"
            + (f"\n\n[禁用表达(近章高频复现,本章出现即扣分)]\n{ban}" if ban else ""),
            stage="review_draft_outline",
            story_id=state.get("story_id", ""),
        )
        deps.log_review(state, reviewer="outline", verdict=verdict,
                        round_no=state.get("rewrite_count", 0) + 1)
        return {"outline_review": verdict}
