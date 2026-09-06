"""主控 supervisor 的 LLM 节点(ADR-0005/0011):共创汇总、总大纲、阶段细纲、切片、摘要。"""

from __future__ import annotations

from app.core.config import AgentRole
from app.graph.agents.base import BaseAgent, NodeDeps, register_agent


@register_agent
class CoauthorNode(BaseAgent):
    """共创访谈汇总:用户构想 -> 世界观设定(多轮对话由 API 层聚合,节点做收敛)。"""

    name = "supervisor"
    role = AgentRole.SUPERVISOR

    def __call__(self, state: dict, deps: NodeDeps) -> dict:
        settings = self.ask_text(
            system=(
                "你是小说世界观共创策划。把用户的构想收敛为结构化世界观设定,"
                "包含:基调、核心冲突、力量/社会体系、3-6 个主要角色构想(名字+一句话)。"
                "直接输出设定文本,不要客套。"
            ),
            user=state.get("initial_input", "用户未提供,请生成一个东方奇幻世界观"),
            stage="coauthor",
            story_id=state.get("story_id", ""),
        )
        return {"world_settings": settings}


@register_agent
class GenMasterOutline(BaseAgent):
    """总大纲(含卷/阶段结构——阶段边界的规划源,ADR-0007 裁决 2)。"""

    name = "supervisor"
    role = AgentRole.SUPERVISOR

    def __call__(self, state: dict, deps: NodeDeps) -> dict:
        n = state.get("target_chapters", 6)
        outline = self.ask_text(
            system=(
                f"你是小说总大纲架构师。基于世界观设定产出总大纲(markdown):"
                f"全书主线、分卷结构(每卷 3-5 章,共规划约 {n} 章)、"
                f"每卷一行卷名+核心事件+阶段目标。简洁,总长不超过 600 字。"
            ),
            user=(state.get("world_settings", "")
                  + ("\n\n[用户对上一版大纲的修改意见] " + state["user_input"]["feedback"]
                     if isinstance(state.get("user_input"), dict)
                     and state["user_input"].get("action") == "revise" else "")),
            stage="master_outline",
            story_id=state.get("story_id", ""),
        )
        return {"master_outline": outline, "outline_confirmed": False}


@register_agent
class StageOutlineNode(BaseAgent):
    """阶段细纲(阶段首章):总大纲的当前卷展开为各章要点。"""

    name = "supervisor"
    role = AgentRole.SUPERVISOR

    def __call__(self, state: dict, deps: NodeDeps) -> dict:
        done = state.get("chapters_done", 0)
        brief = deps.recent_carryover(state)   # 上期衔接状态(短期记忆)
        outline = self.ask_text(
            system=(
                "你是剧情策划。基于总大纲中下一卷的内容,产出该阶段(卷)的分章细纲:"
                "每章一行:章号|主要事件|在场角色|本章要点。阶段内 3-5 章。"
                "必须与已完成章节衔接。总长不超过 400 字。"
            ),
            user=f"[总大纲]\n{state.get('master_outline','')}\n\n"
                 f"[已完成章数]{done}\n[上期衔接]{brief}",
            stage="stage_outline",
            story_id=state.get("story_id", ""),
        )
        return {"stage_outline": outline, "is_stage_first": True}


@register_agent
class ChapterSliceNode(BaseAgent):
    """非首章:从已确认阶段细纲切片派生本章要点(R1 裁决 a:轻量,无评审)。"""

    name = "supervisor"
    role = AgentRole.SUPERVISOR

    def __call__(self, state: dict, deps: NodeDeps) -> dict:
        no = state["chapter_no"]
        brief = deps.recent_carryover(state)
        # 确定性优先:从阶段细纲提取本章行;LLM 仅做衔接补全
        slice_line = deps.extract_stage_line(state.get("stage_outline", ""), no)
        chapter_brief = self.ask_text(
            system="把给定章节细纲行扩展为 3-5 句本章写作要点,包含开场衔接提示。直接输出要点。",
            user=f"[本章细纲行]{slice_line}\n[上期衔接]{brief}",
            stage="chapter_slice",
            story_id=state.get("story_id", ""),
        )
        return {"chapter_brief": chapter_brief}


@register_agent
class SummaryNode(BaseAgent):
    """章摘要(便宜模型,定稿管道步骤)。"""

    name = "supervisor"
    role = AgentRole.SUMMARY

    def __call__(self, state: dict, deps: NodeDeps) -> dict:
        summary = self.ask_text(
            system="用 100-150 字概括本章:主要事件、角色状态变化、留下的悬念。直接输出摘要。",
            user=state.get("draft", "")[:6000],
            stage="chapter_summary",
            story_id=state.get("story_id", ""),
        )
        return {"chapter_summary": summary}
