"""基线策略:naive 最近 N 章全文上下文(常见做法,对照组)。"""

from __future__ import annotations

from evals.synth import SynthWorld


def naive_context(world: SynthWorld, chapter_no: int, window: int = 3) -> dict:
    """返回最近 window 章的"全文"(所有事实文本,无角色区分、无可见性)。"""
    texts: list[str] = []
    fact_ids: list[str] = []
    for ch in world.chapters:
        if chapter_no - window <= ch.chapter_no < chapter_no:
            for fid in ch.new_fact_ids:
                texts.append(world.facts[fid])
                fact_ids.append(fid)
    return {"texts": texts, "fact_ids": set(fact_ids), "characters": set()}
