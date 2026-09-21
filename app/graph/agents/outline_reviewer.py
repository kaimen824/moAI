"""大纲 Agent(ADR-0005):独立 critic,只读,结构化裁决。

三种评审模式共用裁决契约:
  {"verdict": "pass|revise|block", "scores": {"consistency": 0-10, "structure": 0-10},
   "feedback": "..."}
评审输出无效(重试后仍不过 schema)时安全默认 revise(ADR-0026)——
评审链路宁可误返工,绝不静默 pass。
"""

from __future__ import annotations

from app.core.config import AgentRole
from app.graph.agents.base import BaseAgent, LLMFormatError, NodeDeps, register_agent
from app.graph.agents.schemas import ReviewVerdict

_SAFE_REVISE = {
    "verdict": "revise", "scores": {}, "fix_scope": "content",
    "feedback": "评审输出无效(未通过 schema 校验),已安全降级为返工。",
}


def _judge(self: BaseAgent, deps: NodeDeps, *, system: str, user: str,
           stage: str, state: dict, round_no: int, reviewer: str = "outline") -> dict:
    """评审裁决公共路径:schema 校验 + 失败台账 + 安全默认 revise。"""
    try:
        verdict = self.ask_json(system, user, stage=stage,
                                story_id=state.get("story_id", ""), schema=ReviewVerdict)
    except LLMFormatError as exc:
        deps.log_llm_failure(story_id=state.get("story_id", ""),
                             stage=stage, node=self.name, exc=exc)
        verdict = {**_SAFE_REVISE, "feedback": _SAFE_REVISE["feedback"] +
                   f"(trace={exc.trace_id})"}
    deps.log_review(state, reviewer=reviewer, verdict=verdict, round_no=round_no)
    return verdict

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
    "- 主角全知:无信息来源却正确的推断;\n"
    "- 无代价胜利:本章目标达成且没有付出、损失或遗留问题;\n"
    "- 结构性信息过载:单场景塞入超出剧情需要的设定/数值/面板数量"
    "(场景信息负载问题;措辞层面的罗列写法由质量审校专责,此处不评)。\n"
    "文风问题(措辞/节奏/冗余/复读表达)不在你的职责内——你只评一致性、结构"
    "与信息边界,发现文风问题也不用提(ADR-0019 职责切分)。\n"
    "判定纪律(ADR-0037,防贴线死锁):\n"
    "- 按逻辑不按字面:时间线/数值以是否自洽为准——草稿推进与基准一致地递变"
    "(如过了一夜倒计时减一并有过渡交代)即通过;仅当自相矛盾或无依据跳变"
    "才算硬伤。基准第一行[时间线]是双方共同基准。\n"
    "- local 不单独致 revise:全部问题均为 local(单句可改)时 verdict 给 pass,"
    "处方写进 feedback 供后续参考;local 与 content 并存时才 revise,"
    "fix_scope 标 content。\n"
    "- 基准标注'深化(可选)'的条目未落实不扣分不致 revise,只在风险区提示。\n"
    "- 主条件(基准未标可选的条目)实质达成即算落实——写法与基准措辞不同、"
    "顺序调整不扣分;抓住'实质未达成'而非'与基准字面不一致'。\n"
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
        verdict = _judge(
            self, deps, system=_SYSTEM,
            user=f"[评审对象] 总大纲\n{state.get('master_outline','')}\n\n"
                 f"[基准] 世界观设定\n{state.get('world_settings','')}",
            stage="review_master_outline", state=state, round_no=0)
        return {"outline_verdict": verdict}


@register_agent
class ReviewStageOutline(BaseAgent):
    name = "outline_agent"
    role = AgentRole.OUTLINE

    def __call__(self, state: dict, deps: NodeDeps) -> dict:
        verdict = _judge(
            self, deps, system=_SYSTEM,
            user=f"[评审对象] 阶段细纲\n{state.get('stage_outline','')}\n\n"
                 f"[基准] 总大纲\n{state.get('master_outline','')}\n\n"
                 f"[已完成剧情回顾(细纲不得与其中事件重复,重复即 revise)]\n"
                 f"{deps.story_recap(state)}\n\n"
                 f"[已完成]{state.get('chapters_done',0)} 章",
            stage="review_stage_outline", state=state, round_no=0)
        return {"outline_verdict": verdict}


@register_agent
class ReviewDraftOutline(BaseAgent):
    """成稿一致性评审(fan-out 之一,ADR-0007 裁决 3)。"""

    name = "outline_agent"
    role = AgentRole.OUTLINE

    def __call__(self, state: dict, deps: NodeDeps) -> dict:
        verdict = _judge(
            self, deps, system=_SYSTEM.replace('"structure":0-10', '"fidelity":0-10'),
            user=f"[评审对象] 本章正文草稿\n{state.get('draft','')}\n\n"
                 f"[基准] 本章要点\n{state.get('chapter_brief','')}\n\n"
                 f"[基准] 总大纲(当前卷)\n{state.get('master_outline','')[:1500]}\n\n"
                 f"[上期衔接(若草稿重演/复述其中已发生事件,判 revise)]\n"
                 f"{deps.recent_carryover(state)}",
            stage="review_draft_outline", state=state,
            round_no=state.get("rewrite_count", 0) + 1)
        return {"outline_review": verdict}
