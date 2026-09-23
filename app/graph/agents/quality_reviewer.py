"""审校 Agent:质量评审(一致性/伏笔处理/文风)。

伏笔账本变更(thread_changes)已剥离至伏笔评审(ADR-0020 单独评审);
此处 foreshadow 维度只评"本章对既有伏笔的处理是否得当",不产账本变更。
评审输出无效(重试后仍不过 schema)时安全默认 revise(ADR-0026)。
"""

from __future__ import annotations

from app.core.config import AgentRole, get_settings
from app.graph.agents.base import BaseAgent, LLMFormatError, NodeDeps, register_agent
from app.graph.agents.outline_reviewer import _SAFE_REVISE
from app.graph.agents.schemas import ReviewVerdict

_SYSTEM = (
    "你是小说质量审校员——串行双闸的风格闸评审(ADR-0036):你评审的草稿"
    "已通过结构闸(大纲一致性/结构/信息边界/能力越权/主角全知均已把关),"
    "你只评文风与表达层。严格按 JSON 输出:"
    '{"verdict":"pass|revise|block","scores":{"consistency":0-10,"foreshadow":0-10,"style":0-10},'
    '"fix_scope":"style|local",'
    '"feedback":"具体修改意见"}。\n'
    "判定纪律(ADR-0040,带意见通过——评审是编辑视角不是零瑕疵验收):\n"
    "- consistency/foreshadow 低于 7 → revise(客观缺陷);\n"
    "- **style 单维不致 revise**:长文必然存在可改进的表达层细节,发现即"
    "失败会让精校循环永无止境。style<5(整段级复读/表达层严重失控)才"
    "revise;style≥5 时即使有可改进处也给 pass,**处方照常写入 feedback**"
    "(意见随中断卡供作者参考)。\n"
    "- 复检纪律:输入若附[上轮已套用编辑],其中已落实的修改不得再提——"
    "复审只看两点:已修处是否真正解决(未解决且严重才重提,注明'上轮"
    "处方未达预期'),以及是否有**新发现**;不得换个说法复述旧意见。\n"
    "fix_scope 只有两档——你的全部发现必须可由精校(表达层局部修订)落实,"
    "超出的归结构闸,不归你:\n"
    "- style:纯文风问题(措辞/节奏/冗余/复读表达/比喻句式堆叠);\n"
    "- local:单句级自洽修正,且必须在'必须修改'里给出精确处方(将X改为Y:"
    "数值对账、称谓统一、单句事实更正等),不含任何结构调整。\n"
    "职责边界:结构性问题(增删场景/改因果/改人物行动逻辑/能力越权/信息"
    "边界/结构性信息过载)是结构闸的职责,你不得以此评分或作为 verdict"
    "依据——结构闸已对同一稿把关,你复检结构只会以另一套尺度翻已过的结论,"
    "且精校修不了结构。确需提醒时在 feedback 末尾以【结构风险】一句话标注,"
    "不扣分不改 verdict。\n"
    "consistency 维度只评单句级自洽:同章内数值/称谓/时间线彼此对得上"
    "(local 可修);与基准的结构吻合度由结构闸专责,不在你的评分范围。\n"
    "表达层反AI检查(命中即 style 记 ≤5 并 revise,feedback 逐条指出):\n"
    "- 信息倾泻:连续三行以上的设定罗列/数值播报/面板堆砌(措辞呈现层);\n"
    "- 复读表达:与近章高度重复的句式或口头禅(参见[禁用表达]清单,若提供)。\n"
    "复读豁免(ADR-0035):剧情承载词(伏笔名/期限约定/专有设定,如活跃伏笔"
    "与本章要点中的短语)的必要指称不算复读,也不追究其同义变体——复读仅限"
    "修饰性口头禅与句式;禁用清单本身已剔除剧情词,清单外莫再扩大化。\n"
    "篇幅检查(ADR-0034):正文低于[字数下限](若标注)必须 revise,"
    "feedback 注明实际字数与下限,fix_scope 标 local。\n"
    "feedback 的'亮点'至多一条,不得是'延续风格'式加码夸奖。"
)


@register_agent
class QualityReviewNode(BaseAgent):
    name = "reviewer"
    role = AgentRole.REVIEWER

    def __call__(self, state: dict, deps: NodeDeps) -> dict:
        bundle = state.get("context_bundle", {})
        # 最小注入(ADR-0039,所有者提出):风格闸不做跨章事实校验(结构闸
        # 职责),世界事实全量账本不进输入——只带上期衔接(称谓/时间线衔接
        # 与重演判定)与伏笔评审结论(结构闸并行产出,比全量台账小一个量级
        # 且只含本章相关变更)。
        ban = "\n".join(f"- {p}" for p in bundle.get("style_ban", []))
        tr = state.get("thread_review") or {}
        tr_lines = "\n".join(
            f"- [{c.get('action', '?')}] {c.get('description', '')}"
            for c in tr.get("thread_changes", []))
        edits = state.get("last_polish_edits") or []
        edit_lines = "\n".join(
            f"- {e.get('find', '')[:40]!r} → {e.get('replace', '')[:40]!r}"
            for e in edits)
        floor = get_settings().chapter_min_chars
        length_note = (f"\n\n[字数下限:{floor} 字;本章草稿实际 {len(state.get('draft') or '')} 字,"
                       "低于下限必须 revise]" if floor > 0 else "")
        user = (f"[本章草稿]\n{state.get('draft','')}\n\n"
                f"[上期衔接(称谓/时间线衔接基准;草稿重演其中已发生事件记低分)]\n"
                f"{bundle.get('carryover', '')}"
                + (f"\n\n[本章伏笔变更(伏笔评审已判定,供 foreshadow 维度参照)]\n{tr_lines}"
                   if tr_lines else "")
                + (f"\n\n[上轮已套用编辑(已落实,不得重提;复审只看是否真解决与新发现)]\n{edit_lines}"
                   if edit_lines else "")
                + (f"\n\n[禁用表达(近章高频复现,本章出现即 style 记低分)]\n{ban}" if ban else "")
                + length_note)
        try:
            review = self.ask_json(_SYSTEM, user, stage="review_quality",
                                   story_id=state.get("story_id", ""),
                                   schema=ReviewVerdict)
        except LLMFormatError as exc:
            deps.log_llm_failure(story_id=state.get("story_id", ""),
                                 stage="review_quality", node=self.name, exc=exc)
            # 降级兜底同样守风格闸职责边界:content 不在选项内(ADR-0036)
            review = {**_SAFE_REVISE, "fix_scope": "style",
                      "feedback": f"{_SAFE_REVISE['feedback']}(trace={exc.trace_id})"}
        deps.log_review(state, reviewer="reviewer", verdict=review,
                        round_no=state.get("rewrite_count", 0) + 1)
        return {"quality_review": review}
