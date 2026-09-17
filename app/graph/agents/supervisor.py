"""主控 supervisor 的 LLM 节点(ADR-0005/0011):共创汇总、总大纲、阶段细纲、切片、摘要。"""

from __future__ import annotations

from app.core.config import AgentRole
from app.graph.agents.base import BaseAgent, NodeDeps, register_agent
from app.graph.agents.schemas import CapabilityContract, EntityStageUpdate


@register_agent
class CoauthorNode(BaseAgent):
    """共创访谈汇总:用户构想 -> 世界观设定 + 核心能力契约(ADR-0017)。"""

    name = "supervisor"
    role = AgentRole.SUPERVISOR

    def __call__(self, state: dict, deps: NodeDeps) -> dict:
        settings = self.ask_text(
            system=(
                "你是小说世界观共创策划。若输入只是题材标签(如'龙傲天''无限流'),"
                "按该题材的经典范式自行展开设定;若输入较完整则收敛。产出结构化世界观设定,"
                "包含:基调、核心冲突、力量/社会体系、3-6 个主要角色构想(名字+一句话)。"
                "若设定包含主角的核心能力(金手指),只描述其现象与来源,不要在此展开其全能用法。"
                "直接输出设定文本,不要客套。"
            ),
            user=state.get("initial_input", "用户未提供,请生成一个东方奇幻世界观"),
            stage="coauthor",
            story_id=state.get("story_id", ""),
        )
        contract = self.ask_json(
            "你是能力系统设计师。为主角的核心能力(金手指;若世界观没有明确能力,"
            "则以主角最突出的专长为对象)制定硬边界契约,严格按 JSON 输出:\n"
            '{"capability_contract":"能做什么:...\\n'
            "不能做什么:必须列出至少两类无法直接获得的信息(如:他人真实动机/"
            "幕后主使身份/现成解决方案/未来走向),能力只能给出线索与现象\\n"
            "使用成本:每次使用付出的代价或风险\\n"
            '失效条件:在什么情况下完全不可用"}\n'
            "契约目标:把'观察和收集线索'交给能力,把'得出结论、解决问题'留给人物的"
            "推理、试错与协作。契约是该书的硬约束,后续所有章节遵守。",
            settings,
            stage="capability_contract",
            story_id=state.get("story_id", ""),
            schema=CapabilityContract,
        )
        return {"world_settings": settings,
                "capability_contract": contract.get("capability_contract", "")}


@register_agent
class GenMasterOutline(BaseAgent):
    """总大纲(含卷/阶段结构——阶段边界的规划源,ADR-0007 裁决 2)。"""

    name = "supervisor"
    role = AgentRole.SUPERVISOR

    def __call__(self, state: dict, deps: NodeDeps) -> dict:
        n = max(state.get("target_chapters", 6), 6)
        outline = self.ask_text(
            system=(
                "你是长篇连载网文的总大纲架构师。按网文体量规划——全书数百章、"
                "数百万字,分卷推进,每卷 30-100+ 章。基于世界观设定产出总大纲"
                "(markdown),总长 2500-4000 字,必须包含:\n"
                "1. 全书主线、核心卖点/金手指、力量与升级体系、结局走向;\n"
                "2. 分卷结构(至少规划 4 卷直到结局):卷标题行必须标注章节范围,"
                "如'**第一卷 山村异变(第1-80章)**';\n"
                "3. 分层展开:前两卷(近期就要写的)每卷列 8-12 个按顺序的阶段性"
                "事件(具体到'谁做了什么、导致什么',写明因果衔接)与卷末钩子;"
                "之后的卷各给 2-4 句卷级走向;\n"
                "4. 主要角色的成长弧线各一行。\n"
                "近期卷的阶段性事件是后续细纲切片的直接依据,宁可具体勿空泛;"
                "远期卷只需锁住大方向,留给后续展开。"
            ),
            user=(state.get("world_settings", "")
                  + f"\n\n[近期写作目标]本次先写约 {n} 章,大纲前两卷需覆盖到该进度之后。"
                  + (f"\n\n[核心能力契约(规划不得让能力越权解题)]\n{state['capability_contract']}"
                     if state.get("capability_contract") else "")
                  + ("\n\n[用户对上一版大纲的修改意见] " + state["user_input"]["feedback"]
                     if isinstance(state.get("user_input"), dict)
                     and state["user_input"].get("action") == "revise" else "")),
            stage="master_outline",
            story_id=state.get("story_id", ""),
        )
        return {"master_outline": outline,
                "master_regen_count": state.get("master_regen_count", 0) + 1}


@register_agent
class StageOutlineNode(BaseAgent):
    """阶段细纲(阶段首章):总大纲的当前卷展开为各章要点。

    阶段范围由本节点产出并被代码解析(stage_end_chapter)——阶段边界
    以细纲实际覆盖的章号为准,不再按固定章数硬切。
    """

    name = "supervisor"
    role = AgentRole.SUPERVISOR

    def __call__(self, state: dict, deps: NodeDeps) -> dict:
        done = state.get("chapters_done", 0)
        recap = deps.story_recap(state)     # 已完成剧情回顾(防重排)
        brief = deps.recent_carryover(state)   # 上期衔接状态(短期记忆)
        outline = self.ask_text(
            system=(
                "你是剧情策划。基于总大纲中尚未完成的剧情,产出下一阶段的张力计划"
                "(不是任务清单——章节推进张力,不要求每章解决一个问题):\n"
                "[阶段目标]一句话:本阶段结束时的局面变化。\n"
                "[各方意图]\n- 主角方:想要什么、可用手段。\n"
                "- 对手方:独立目标与手段(对手为自己行动,不是为主角服务);"
                "对手不为主角所知的底牌只写在:[各方意图]里,严禁进入分章要点。\n"
                "[强制受挫]第N章:主角的一次误判或失败,及其会延续到后续章节的后果"
                "(N 必须在本阶段章号范围内;不得设计当场翻盘)。\n"
                "[悬念锁]本阶段禁止解释的谜团(只可加深,不可回收)。\n"
                "[分章计划]每章一行:章号|本章张力推进(信息不完整、允许小挫败)"
                "|在场角色。阶段覆盖 3-6 章,"
                f"章号从第 {done + 1} 章起连续编号。\n"
                "铁律:[已完成剧情回顾]中的事件已经写过——计划必须从回顾末尾的"
                "剧情状态继续向前推进,严禁重排、复写或换措辞重演已完成事件;"
                "若总大纲的某卷事件已部分完成,只规划其未完成部分。\n"
                "每章要有新的剧情推进,但推进不等于解决。总长不超过 600 字。"
            ),
            user=(f"[总大纲]\n{state.get('master_outline','')}\n\n"
                  f"[已完成章数]{done}\n\n[已完成剧情回顾]\n{recap}\n\n"
                  f"[上期衔接]{brief}"
                  + ("\n\n[用户对上一版细纲的修改意见(优先落实)] "
                     + state["user_input"]["feedback"]
                     if isinstance(state.get("user_input"), dict)
                     and state["user_input"].get("action") == "revise" else "")),
            stage="stage_outline",
            story_id=state.get("story_id", ""),
        )
        return {"stage_outline": outline, "is_stage_first": True,
                "stage_start_chapter": done + 1,
                "stage_end_chapter": deps.parse_stage_range(outline, start=done + 1),
                "stage_regen_count": state.get("stage_regen_count", 0) + 1,
                # 新阶段开始即作废旧章要点(防御:残留的 chapter_brief 一旦
                # 进入写作/评审上下文会被当成"本章基准"——ch19 串章事故)
                "chapter_brief": ""}


@register_agent
class ChapterSliceNode(BaseAgent):
    """每章(含阶段首章):从已确认阶段细纲切片派生本章要点(R1 裁决 a:轻量,无评审)。

    首章也必经此节点:细纲确认后若直连写作,上一阶段末章的 chapter_brief
    会残留为 writer/评审的"本章基准"(ch19 串章事故)。
    """

    name = "supervisor"
    role = AgentRole.SUPERVISOR

    def __call__(self, state: dict, deps: NodeDeps) -> dict:
        no = state["chapter_no"]
        brief = deps.recent_carryover(state)
        # 确定性优先:从阶段细纲提取本章行;LLM 仅做衔接补全
        slice_line = deps.extract_stage_line(state.get("stage_outline", ""), no)
        chapter_brief = self.ask_text(
            system=(
                "把给定章节细纲行扩展为 3-5 句本章写作要点,包含开场衔接提示"
                "(以上一章结尾的状态、场景、时间为起点继续)。\n"
                "注意:细纲行之后已经写完的章节不得出现在本章要点里;"
                "本章要点必须是尚未发生的新剧情。\n"
                "要点必须信息不完整:主角在本章结束时仍缺少至少一项关键信息,"
                "不得把本章涉及的谜团全部解释完;若细纲或阶段计划标记了本章受挫,"
                "要点必须落实失败/误判及其延续后果,不得设计当场翻盘。直接输出要点。"
            ),
            user=f"[本章细纲行]{slice_line}\n[章号]第{no}章\n[上期衔接]{brief}",
            stage="chapter_slice",
            story_id=state.get("story_id", ""),
        )
        return {"chapter_brief": chapter_brief}


@register_agent
class SummaryNode(BaseAgent):
    """章摘要(便宜模型,定稿管道步骤)。

    分层记忆(ADR-0003 长篇扩展):定稿章为阶段末章时,追加生成该阶段的
    聚合摘要(layer='stage')——供 story_recap 以"早期聚合+近期全量"
    拼装,防止长篇上下文膨胀。
    """

    name = "supervisor"
    role = AgentRole.SUMMARY

    def __call__(self, state: dict, deps: NodeDeps) -> dict:
        story_id = state.get("story_id", "")
        chapter_no = state.get("chapter_no", 0)
        summary = self.ask_text(
            system="用 100-150 字概括本章:主要事件、角色状态变化、留下的悬念。直接输出摘要。",
            user=state.get("draft", "")[:6000],
            stage="chapter_summary",
            story_id=story_id,
        )
        update: dict = {"chapter_summary": summary}
        stage_end = state.get("stage_end_chapter", 0)
        stage_start = state.get("stage_start_chapter", 0)
        if stage_end and chapter_no >= stage_end:
            chapter_summaries = deps.stage_chapter_summaries(
                story_id, stage_start, upto=chapter_no)
            # 阶段末章自身的摘要尚未落库(SummaryNode 先于 finalize 执行,评审 6.11):
            # 把内存中本章摘要显式并入聚合输入,防止阶段摘要漏掉末章
            chapter_summaries = [
                (no, text) for no, text in chapter_summaries if no != chapter_no
            ] + [(chapter_no, summary)]
            chapter_summaries.sort(key=lambda t: t[0])
            if chapter_summaries:
                merged = self.ask_text(
                    system=(
                        "你是连载小说的阶段记忆压缩器。把给定的一系列章节摘要聚合为"
                        "一份阶段摘要(300-500 字):主线推进、关键转折、各角色阶段性"
                        "状态、阶段结束时仍未回收的伏笔。只写已发生的事实,"
                        "不得虚构后续剧情。直接输出摘要。"
                    ),
                    user="\n".join(f"第{no}章:{text}" for no, text in chapter_summaries),
                    stage="stage_summary",
                    story_id=story_id,
                )
                update["stage_summary"] = merged
                # 阶段末实体条目滚动(ADR-0015 裁决②):本阶段被触达的实体,
                # 条目内容并入新剧情——防百章后条目失真
                from app.memory.repository import AgentContext
                touched = deps.entities.stage_touched(
                    AgentContext("entity_manager", story_id), stage_start, chapter_no)
                if touched:
                    upd = self.ask_json(
                        "你是设定条目维护器。把本阶段剧情并入既有实体条目,"
                        "严格按 JSON 输出:"
                        '{"updates":[{"name":"实体名","content":"并入新剧情后的完整条目(2-4 句,客观陈述)"}]}。'
                        "只列本阶段有新剧情的实体;没有新剧情的保持原样不输出。",
                        f"[本阶段剧情摘要]\n{merged}\n\n[待更新条目]\n"
                        + "\n".join(f"- {t['name']}({t['type']}): {t['content'] or '(空)'}"
                                    for t in touched),
                        stage="entity_stage_update",
                        story_id=story_id,
                        schema=EntityStageUpdate,
                    )
                    by_name = {t["name"]: t["id"] for t in touched}
                    update["entity_content_updates"] = [
                        {"entity_id": by_name[u["name"]], "content": u["content"]}
                        for u in upd.get("updates", [])
                        if u.get("name") in by_name and u.get("content")]
        return update
