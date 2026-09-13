"""记忆系统行模型(与 db/ddl.py 表一一对应)。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Story:
    id: str
    title: str
    premise: str = ""
    status: str = "draft"
    main_branch_id: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass
class Fact:
    id: str
    story_id: str
    type: str                    # event|state|setting|relation
    content: str
    chapter_established: int
    branch_id: str
    prev_version_id: str | None = None
    confidence: str = "high"     # high|low
    status: str = "confirmed"    # confirmed|pending_review
    embedding: bytes | None = None
    created_at: str = ""


@dataclass
class Belief:
    id: str
    story_id: str
    character_id: str
    content: str
    established_chapter: int
    branch_id: str
    source_fact_id: str | None = None
    status: str = "believed"     # believed|dispelled
    dispelled_chapter: int | None = None
    prev_version_id: str | None = None
    embedding: bytes | None = None
    created_at: str = ""


@dataclass
class VisibilityEntry:
    fact_id: str
    character_id: str
    knowledge_level: str         # known_full|known_partial(R3:缺省即未知,无 unknown 行)
    detail: str | None = None    # R4:partial 时"知晓的部分"
    learned_chapter: int = 0
    branch_id: str = ""


@dataclass
class CharacterRow:
    id: str
    story_id: str
    name: str
    profile: str = ""
    entity_id: str | None = None
    created_at: str = ""
    updated_at: str = ""


@dataclass
class ChapterRow:
    id: str
    story_id: str
    chapter_no: int
    branch_id: str
    version_no: int = 1
    prev_version_id: str | None = None
    title: str = ""
    content: str = ""
    status: str = "draft"        # draft|active|stale|archived
    created_at: str = ""
    updated_at: str = ""


@dataclass
class PlotThread:
    id: str
    story_id: str
    description: str
    branch_id: str
    planted_chapter: int | None = None
    resolved_chapter: int | None = None
    status: str = "open"         # open|resolved|dropped
    tier: str | None = None      # short|long(ADR-0020;NULL 按 short 计账龄)
    basis: str | None = None     # plant 依据/长线绑定(审计用)
    escalated_chapter: int | None = None   # short->long 升格章(仅一次)
    created_at: str = ""
    updated_at: str = ""


@dataclass
class EntityRow:
    id: str
    story_id: str
    type: str                    # character|faction|location|item|technique|concept
    name: str
    content: str = ""            # wiki 条目正文(阶段末滚动摘要维护)
    embedding: bytes | None = None
    chapter_no: int | None = None   # 首次出现章(None=共创种子)
    status: str = "active"       # active|merged(ADR-0015:被合并保留审计)
    created_at: str = ""
    updated_at: str = ""


@dataclass
class EntityLink:
    id: str
    story_id: str
    from_entity: str
    to_entity: str
    relation: str = ""           # 自由文本(ADR-0015 裁决④)
    chapter_no: int | None = None


@dataclass
class POVMemory:
    """某角色视角的长期记忆(检索服务/测试的返回结构)。"""

    visible_facts: list[Fact] = field(default_factory=list)
    partial_details: dict[str, str] = field(default_factory=dict)  # fact_id -> detail
    beliefs: list[Belief] = field(default_factory=list)


@dataclass
class WorldSnapshot:
    """某 (chapter, branch) 时点的世界状态(回放产物,审校校验基准)。"""

    facts: list[Fact] = field(default_factory=list)
    beliefs: list[Belief] = field(default_factory=list)
    plot_threads: list[PlotThread] = field(default_factory=list)
    characters: list[CharacterRow] = field(default_factory=list)
