"""agent_acl 种子数据(ADR-0006 权限矩阵)。

单写者不变式:facts/beliefs/temporal_relations -> 事件管理;plot_threads -> 伏笔评审
(ADR-0020 自审校移交);characters/entities/entity_links -> 角色管理;
大纲(outlines/stories)-> 主控。retrieval_service 为检索服务专用只读身份。
"""

# (agent_name, data_domain, can_read, can_write)
SEED_ACL: list[tuple[str, str, int, int]] = []

# 读写域
_WRITER_DOMAINS: dict[str, list[str]] = {
    "event_manager": ["facts", "beliefs", "fact_visibility", "temporal_relations"],
    "thread_reviewer": ["plot_threads"],
    "character_manager": ["characters"],
    "entity_manager": ["entities", "entity_links", "entity_aliases", "entity_merge_proposals"],
    "supervisor": [
        "stories", "outlines", "branches", "facts", "beliefs",
        "fact_visibility", "characters", "chapters", "paragraphs",
        "chapter_summaries", "plot_threads", "temporal_relations",
        "entities", "entity_links", "entity_aliases", "entity_merge_proposals",
        "review_results", "user_directives",
    ],
}

# 只读域(除自己可写域之外的常用读域)
_READER_DOMAINS: dict[str, list[str]] = {
    "outline_agent": ["outlines", "facts", "beliefs", "chapters", "chapter_summaries", "plot_threads"],
    "writer": ["outlines", "facts", "beliefs", "fact_visibility", "characters", "chapters",
               "chapter_summaries", "plot_threads", "entities"],
    "reviewer": ["outlines", "facts", "beliefs", "characters", "chapters",
                 "chapter_summaries", "entities", "plot_threads"],
    "event_manager": ["outlines", "chapters", "characters", "chapter_summaries", "entities"],
    "character_manager": ["outlines", "facts", "beliefs", "chapters", "chapter_summaries",
                          "entities", "entity_links"],
    "entity_manager": ["outlines", "characters", "facts", "chapter_summaries"],
    "retrieval_service": [
        "outlines", "facts", "beliefs", "fact_visibility", "characters",
        "chapters", "paragraphs", "chapter_summaries", "plot_threads",
        "temporal_relations", "entities", "entity_links",
    ],
}


def build_seed() -> list[tuple[str, str, int, int]]:
    rows: dict[tuple[str, str], tuple[str, str, int, int]] = {}

    for agent, domains in _READER_DOMAINS.items():
        for domain in domains:
            rows[(agent, domain)] = (agent, domain, 1, 0)

    for agent, domains in _WRITER_DOMAINS.items():
        for domain in domains:
            if (agent, domain) in rows:
                rows[(agent, domain)] = (agent, domain, 1, 1)
            else:
                rows[(agent, domain)] = (agent, domain, 1, 1)

    return list(rows.values())
