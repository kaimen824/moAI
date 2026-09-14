"""LLM JSON 输出强契约(评审 6.9 / ADR-0026):每个 JSON stage 一个 Pydantic 模型。

约定:
- 真枚举(verdict/action/tier/decision/confidence/type)用 Literal 硬约束——
  错枚举即校验失败,ask_json 把错误回喂模型自纠一次,仍失败抛 LLMFormatError;
- 自由文本字段给安全默认值(模型省略不致命的允许缺省);
- ask_json 校验后返回 model_dump() 的 dict——节点侧协议保持 dict 访问,
  12 处调用点不必改为对象访问(ADR-0026 实现期裁决)。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# ---- 共创(supervisor / character_manager)----

class CapabilityContract(BaseModel):
    capability_contract: str = ""


class CharacterDraft(BaseModel):
    name: str
    profile: str = ""


class InitCharacters(BaseModel):
    characters: list[CharacterDraft] = Field(default_factory=list)


class EntitySeed(BaseModel):
    name: str
    type: str = "concept"          # 展示用分类,代码侧 _TYPE_ZH 兜底,不作硬枚举
    content: str = ""
    aliases: list[str] = Field(default_factory=list)


class EntitySeeds(BaseModel):
    entities: list[EntitySeed] = Field(default_factory=list)
    character_aliases: list[dict] = Field(default_factory=list)
    links: list[dict] = Field(default_factory=list)


# ---- 评审(outline_reviewer / quality_reviewer)----

class ReviewVerdict(BaseModel):
    """大纲/章节评审共用裁决契约(ADR-0005):verdict 硬枚举,绝不允许静默 pass。"""
    verdict: Literal["pass", "revise", "block"]
    scores: dict[str, float] = Field(default_factory=dict)
    fix_scope: Literal["style", "local", "content"] = "content"
    feedback: str = ""


# ---- 伏笔评审(thread_reviewer,ADR-0020/0025)----

class ThreadChange(BaseModel):
    thread_id: str = ""            # ADR-0025:操作对象 id(plant 留空)
    description: str = ""
    action: Literal["plant", "advance", "resolve", "drop"] = "plant"
    tier: Literal["short", "long"] = "short"
    basis: str = ""


class ThreadReview(BaseModel):
    thread_id: str = ""
    description: str = ""
    verdict: Literal["collect", "escalate", "keep"] = "keep"
    reason: str = ""


class ThreadVerdict(BaseModel):
    thread_changes: list[ThreadChange] = Field(default_factory=list)
    reviews: list[ThreadReview] = Field(default_factory=list)


# ---- 事件抽取(event_extractor)----

class FactItem(BaseModel):
    content: str
    type: Literal["event", "state", "setting", "relation"] = "event"
    confidence: Literal["high", "low"] = "high"
    visible_to: list[str] = Field(default_factory=list)
    supersedes: str = ""


class BeliefItem(BaseModel):
    character: str = ""
    content: str = ""


class EntityMention(BaseModel):
    name: str
    type: str = "concept"
    description: str = ""


class LinkItem(BaseModel):
    from_name: str = Field(alias="from")
    to: str = ""
    relation: str = ""
    model_config = dict(populate_by_name=True)


class ConflictItem(BaseModel):
    description: str = ""


class FactChanges(BaseModel):
    facts: list[FactItem] = Field(default_factory=list)
    beliefs: list[BeliefItem] = Field(default_factory=list)
    entities: list[EntityMention] = Field(default_factory=list)
    links: list[LinkItem] = Field(default_factory=list)
    conflicts: list[ConflictItem] = Field(default_factory=list)


# ---- 角色状态与意图(update_characters)----

class CharacterUpdate(BaseModel):
    name: str = ""
    profile_append: str = ""


class CharacterIntent(BaseModel):
    name: str = ""
    goal: str = ""
    knows: str = ""
    doesnt_know: str = ""
    self_interest: str = ""


class CharacterChanges(BaseModel):
    updates: list[CharacterUpdate] = Field(default_factory=list)
    intents: list[CharacterIntent] = Field(default_factory=list)


# ---- 实体消歧(entity_resolver)/ 阶段末条目滚动 ----

class EntityDecision(BaseModel):
    name: str
    decision: Literal["same", "new", "uncertain"]
    target: str = ""
    evidence: str = ""


class EntityDecisions(BaseModel):
    decisions: list[EntityDecision] = Field(default_factory=list)


class EntityStageUpdate(BaseModel):
    updates: list[dict] = Field(default_factory=list)
