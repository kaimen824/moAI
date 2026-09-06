"""角色管理 Agent(共创初始化 + 定稿管道:消费事实变更更新角色卡)。"""

from __future__ import annotations

from app.core.config import AgentRole
from app.graph.agents.base import BaseAgent, NodeDeps, register_agent


@register_agent
class InitCharactersNode(BaseAgent):
    """共创流程 ②:从世界观设定初始化角色卡(ADR-0011)。"""

    name = "character_manager"
    role = AgentRole.CHARACTER

    def __call__(self, state: dict, deps: NodeDeps) -> dict:
        result = self.ask_json(
            "你是角色设计师。从世界观设定中提取 3-6 个主要角色,严格按 JSON 输出:"
            '{"characters":[{"name":"名字","profile":"身份/外貌/性格/目标,各一句话"}]}',
            state.get("world_settings", ""),
            stage="init_characters",
            story_id=state.get("story_id", ""),
        )
        return {"character_drafts": result.get("characters", [])}


@register_agent
class UpdateCharactersNode(BaseAgent):
    """定稿管道步骤 2:消费事实变更集,产出角色卡更新(暂存)。"""

    name = "character_manager"
    role = AgentRole.CHARACTER

    def __call__(self, state: dict, deps: NodeDeps) -> dict:
        changes = state.get("fact_changes", {})
        fact_lines = "\n".join(f"- {f['content']}" for f in changes.get("facts", []))
        belief_lines = "\n".join(f"- {b['content']}" for b in changes.get("beliefs", []))
        if not fact_lines and not belief_lines:
            return {"character_changes": []}
        result = self.ask_json(
            "你是角色状态管理员。基于本章新事实更新受影响角色的状态/关系/目标,严格按 JSON 输出:"
            '{"updates":[{"name":"角色名","profile_append":"新增状态描述(将追加到角色卡)"}]}。'
            "只列有变化的角色。",
            f"[本章新事实]\n{fact_lines}\n\n[本章认知变化]\n{belief_lines}",
            stage="update_characters",
            story_id=state.get("story_id", ""),
        )
        name_to_id = deps.character_name_map(state)
        for u in result.get("updates", []):
            u["character_id"] = name_to_id.get(u.get("name"), "")
        return {"character_changes": result.get("updates", [])}
