"""角色管理 Agent(共创初始化 + 定稿管道:消费事实变更更新角色卡)。"""

from __future__ import annotations

from app.core.config import AgentRole
from app.graph.agents.base import BaseAgent, NodeDeps, register_agent


@register_agent
class InitCharactersNode(BaseAgent):
    """共创流程 ②:从世界观设定初始化角色卡(ADR-0011)+ 实体种子(ADR-0015)。

    角色实体由代码从角色卡确定性生成(名字对齐零风险);
    势力/地点/物品/概念实体与初始链接由 LLM 从世界观抽取,
    连同角色别称一起暂存 entity_drafts,确认总大纲时随角色卡落库。
    """

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
        characters = result.get("characters", [])
        char_names = "、".join(c.get("name", "") for c in characters) or "(无)"

        seeds = self.ask_json(
            "你是设定沉淀器。基于世界观设定与既有角色,沉淀实体知识库的种子,"
            "严格按 JSON 输出:\n"
            '{"entities":[{"name":"","type":"faction|location|item|technique|concept",'
            '"content":"条目正文,2-3 句客观描述","aliases":["俗称/别称/道号"]}],\n'
            '"character_aliases":[{"name":"角色名","aliases":["该角色的道号/俗称/尊称"]}],\n'
            '"links":[{"from":"实体名或角色名","to":"实体名或角色名","relation":"关系短语"}]}\n'
            "规则:实体收世界观中成型的势力/地点/重要物品/功法体系/核心概念(6-12 个);"
            "角色本身不要放进 entities(系统会自动建);links 收设定中明确的关系,"
            "from/to 必须是已给的名字。",
            f"{state.get('world_settings', '')}\n\n[既有角色(勿重复入 entities)]\n{char_names}",
            stage="init_entities",
            story_id=state.get("story_id", ""),
        )
        return {"character_drafts": characters,
                "entity_drafts": {"entities": seeds.get("entities", []),
                                   "character_aliases": seeds.get("character_aliases", []),
                                   "links": seeds.get("links", [])}}


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
