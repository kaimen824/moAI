"""图状态(工作记忆,ADR-0003:草稿/反馈不落库,定稿沉淀才落库)。"""

from __future__ import annotations

from typing import Any, TypedDict


class GraphState(TypedDict, total=False):
    # ---- 会话上下文 ----
    story_id: str
    branch_id: str
    target_chapters: int          # 本次运行要产出的章数(端到端/测试用)
    chapters_done: int
    auto_mode: bool               # 自动模式(ADR-0016):细纲/章节两道闸评审绿则自动确认;
                                  # 总大纲确认永远人工(书之根基);轮次耗尽强制转人工

    # ---- 共创(ADR-0011)----
    initial_input: str            # 用户的世界观构想(共创起点)
    world_settings: str           # 访谈汇总产出
    character_drafts: list[dict]  # 角色管理产出的角色卡草案
    entity_drafts: dict           # 实体种子(ADR-0015:entities/character_aliases/links,确认总大纲时落库)
    master_outline: str           # 总大纲(含卷结构)
    outline_verdict: dict         # 大纲 Agent 对总大纲的裁决
    outline_confirmed: bool
    master_regen_count: int       # 总大纲重生成轮次(上限转人工,ADR-0016)

    # ---- 阶段与章节 ----
    chapter_no: int
    is_stage_first: bool
    stage_outline: str            # 阶段细纲(首章生成并确认)
    stage_start_chapter: int      # 当前细纲覆盖的起始章号
    stage_end_chapter: int        # 当前细纲覆盖的末章章号(阶段边界)
    stage_regen_count: int        # 当前阶段细纲的生成轮次(可观测+轮次上限判据)
    chapter_brief: str            # 本章要点(阶段细纲切片)
    present_characters: list[str] # 本章在场角色(character id)

    # ---- 生成与评审 ----
    context_bundle: dict          # 检索服务组装的上下文
    draft: str
    rewrite_count: int
    outline_review: dict          # 大纲 Agent 成稿裁决
    quality_review: dict          # 审校裁决(含伏笔变更建议)
    merged_verdict: str           # pass | revise | block | needs_user
    rewrite_exhausted: bool       # 达重写上限仍未通过 -> 交用户裁决

    # ---- 定稿(编排原子性:暂存变更集)----
    fact_changes: dict            # 事件管理抽取(facts/beliefs/visibility/entities/links)
    entity_changes: dict          # 实体消歧产物(ADR-0015:new_entities/aliases/links/proposals)
    character_changes: list[dict] # 角色卡更新
    thread_changes: list[dict]    # 伏笔变更(审校建议,人工已确认)
    chapter_summary: str
    stage_summary: str            # 阶段末章时的聚合摘要(layer='stage')
    entity_content_updates: list[dict]  # 阶段末实体条目滚动(ADR-0015 裁决②)
    chapter_id: str

    # ---- 中断恢复的用户输入 ----
    user_input: Any
    error: str
