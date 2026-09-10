"""审校 Agent:质量评审(一致性/伏笔/文风)+ 伏笔变更建议(ADR-0006:写者,人工复核后生效)。"""

from __future__ import annotations

from app.core.config import AgentRole
from app.graph.agents.base import BaseAgent, NodeDeps, register_agent

_SYSTEM = (
    "你是小说质量审校员。严格按 JSON 输出:"
    '{"verdict":"pass|revise|block","scores":{"consistency":0-10,"foreshadow":0-10,"style":0-10},'
    '"feedback":"具体修改意见",'
    '"thread_changes":[{"description":"伏笔描述","action":"plant|advance|resolve|drop"}]}。'
    "thread_changes 列出本章正文中 埋设(plant)/推进(advance)/回收(resolve)/放弃(drop) 的伏笔;"
    "无伏笔变更时给空数组。任一维度低于 7 分给 revise。\n"
    "反AI检查(命中任一即 style 记 ≤5 并 revise,feedback 逐条指出):\n"
    "- 能力越权:关键结论由能力/系统直接给出,人物没有推理过程;\n"
    "- 信息倾泻:连续三行以上的设定罗列/数值播报/面板堆砌;\n"
    "- 主角全知:无信息来源却正确的推断;\n"
    "- 无代价胜利:目标达成且无损失、无遗留问题;\n"
    "- 复读表达:与近章高度重复的句式或口头禅(参见[禁用表达]清单,若提供)。\n"
    "feedback 的'亮点'至多一条,不得是'延续风格'式加码夸奖。"
)


@register_agent
class QualityReviewNode(BaseAgent):
    name = "reviewer"
    role = AgentRole.REVIEWER

    def __call__(self, state: dict, deps: NodeDeps) -> dict:
        bundle = state.get("context_bundle", {})
        world_lines = "\n".join(
            f"- [ch{f.get('chapter_established', '?')}] {f['content']}"
            for f in bundle.get("pov_facts", [])
        )
        threads = "\n".join(f"- {t['description']}({t['status']})"
                            for t in bundle.get("active_threads", []))
        ban = "\n".join(f"- {p}" for p in bundle.get("style_ban", []))
        review = self.ask_json(
            _SYSTEM,
            f"[本章草稿]\n{state.get('draft','')}\n\n"
            f"[上期衔接(草稿若重演其中已发生事件,一致性记低分)]\n"
            f"{bundle.get('carryover', '')}\n\n"
            f"[世界已知事实(校验基准,标注章号)]\n{world_lines}\n\n[现有活跃伏笔]\n{threads}"
            + (f"\n\n[禁用表达(近章高频复现,本章出现即 style 记低分)]\n{ban}" if ban else ""),
            stage="review_quality",
            story_id=state.get("story_id", ""),
        )
        deps.log_review(state, reviewer="reviewer", verdict=review,
                        round_no=state.get("rewrite_count", 0) + 1)
        return {"quality_review": review}
