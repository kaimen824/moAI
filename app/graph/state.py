"""图状态(工作记忆,ADR-0003:草稿/反馈不落库,定稿沉淀才落库)。"""

from __future__ import annotations

from typing import Any, TypedDict


class GraphState(TypedDict, total=False):
    # ---- 会话上下文 ----
    story_id: str
    branch_id: str
    target_chapters: int          # 本次运行要产出的章数(端到端/测试用)
    chapters_done: int

    # ---- 共创(ADR-0011)----
    initial_input: str            # 用户的世界观构想(共创起点)
    world_settings: str           # 访谈汇总产出
    character_drafts: list[dict]  # 角色管理产出的角色卡草案
    master_outline: str           # 总大纲(含卷结构)
    outline_verdict: dict         # 大纲 Agent 对总大纲的裁决
    outline_confirmed: bool

    # ---- 阶段与章节 ----
    chapter_no: int
    is_stage_first: bool
    stage_outline: str            # 阶段细纲(首章生成并确认)
    chapter_brief: str            # 本章要点(阶段细纲切片)
    present_characters: list[str] # 本章在场角色(character id)

    # ---- 生成与评审 ----
    context_bundle: dict          # 检索服务组装的上下文
    draft: str
    rewrite_count: int
    outline_review: dict          # 大纲 Agent 成稿裁决
    quality_review: dict          # 审校裁决(含伏笔变更建议)
    merged_verdict: str           # pass | revise | block | forced_pass
    forced_pass: bool

    # ---- 定稿(编排原子性:暂存变更集)----
    fact_changes: list[dict]      # 事件管理抽取(facts/beliefs/visibility)
    character_changes: list[dict] # 角色卡更新
    thread_changes: list[dict]    # 伏笔变更(审校建议,人工已确认)
    chapter_summary: str
    chapter_id: str

    # ---- 中断恢复的用户输入 ----
    user_input: Any
    error: str
