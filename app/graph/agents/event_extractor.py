"""事件管理 Agent(定稿管道步骤 1):从正文抽取事实/认知/可见性变更集。

输出为暂存变更集(存图状态,定稿单事务落库,ADR-0003 写链路);
含置信度分层(E3)与冲突检测标记(R5)。
"""

from __future__ import annotations

from app.core.config import AgentRole
from app.graph.agents.base import BaseAgent, NodeDeps, register_agent

_SYSTEM = (
    "你是叙事事实抽取器。从章节正文中抽取客观世界事实与角色认知,严格按 JSON 输出:\n"
    '{"facts":[{"content":"事实陈述","type":"event|state|setting|relation",'
    '"confidence":"high|low","visible_to":["角色名",...]}],\n'
    '"beliefs":[{"character":"角色名","content":"该角色以为...(可为误信)"}],\n'
    '"conflicts":[{"description":"与已知事实的矛盾"}]}\n'
    "规则:只抽正文明确陈述的(显式 -> confidence=high);需要推断的隐含信息 -> low;"
    "visible_to 为正文中知晓该事实的角色名;与[已知事实]矛盾的抽到 conflicts。"
)


@register_agent
class EventExtractNode(BaseAgent):
    name = "event_manager"
    role = AgentRole.EVENT

    def __call__(self, state: dict, deps: NodeDeps) -> dict:
        bundle = state.get("context_bundle", {})
        known = "\n".join(f"- {f['content']}" for f in bundle.get("pov_facts", [])[:30])
        result = self.ask_json(
            _SYSTEM,
            f"[第{state.get('chapter_no', 0)}章正文]\n{state.get('draft','')[:6000]}\n\n"
            f"[已知事实(用于冲突检测)]\n{known or '(本章之前无)'}",
            stage="extract_facts",
            story_id=state.get("story_id", ""),
        )
        # 规范化 + 角色名 -> id 映射(暂存变更集,落库时用)
        name_to_id = deps.character_name_map(state)
        for f in result.get("facts", []):
            f["visible_ids"] = [name_to_id[n] for n in f.get("visible_to", []) if n in name_to_id]
        for b in result.get("beliefs", []):
            b["character_id"] = name_to_id.get(b.get("character"), "")
        result["chapter_no"] = state.get("chapter_no", 0)
        return {"fact_changes": result}
