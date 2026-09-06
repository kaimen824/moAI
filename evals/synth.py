"""合成小说世界生成器(确定性,固定种子)。

构造带 ground truth 的世界:
- 每章 1-3 条新事实(事件/状态),部分事实带"远距离依赖"标记(后续章节仍需要)
- 每章在场角色 1-2 名;每条事实的可见性只授予部分在场者(制造 POV 敏感信息)
- ground truth:每章的 needed_fact_ids(近期事实 + 远距离引用)+ 在场角色
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field


@dataclass
class SynthChapter:
    chapter_no: int
    present_characters: list[str]
    new_fact_ids: list[str] = field(default_factory=list)
    needed_fact_ids: list[str] = field(default_factory=list)   # ground truth:本章生成所需
    fact_texts: dict[str, str] = field(default_factory=dict)   # fact_id -> 文本(naive 用)


@dataclass
class SynthWorld:
    characters: list[str]
    facts: dict[str, str] = field(default_factory=dict)            # id -> content
    fact_chapter: dict[str, int] = field(default_factory=dict)     # id -> established
    visibility: dict[str, set[str]] = field(default_factory=dict)  # fact_id -> 可见角色
    chapters: list[SynthChapter] = field(default_factory=list)
    remote_fact_ids: set[str] = field(default_factory=set)         # 被远距离引用的事实


def generate_world(n_chapters: int = 60, n_characters: int = 5, seed: int = 42) -> SynthWorld:
    rng = random.Random(seed)
    world = SynthWorld(characters=[f"角色{c}" for c in range(n_characters)])

    fact_counter = 0
    for ch in range(1, n_chapters + 1):
        present = rng.sample(world.characters, k=rng.choice([1, 2]))
        chapter = SynthChapter(chapter_no=ch, present_characters=present)

        n_new = rng.choice([1, 2, 3])
        for _ in range(n_new):
            fact_counter += 1
            fid = f"f{fact_counter}"
            is_remote = rng.random() < 0.35   # 35% 的事实将被远距离引用
            world.facts[fid] = f"第{ch}章确立的事实{fact_counter}"
            world.fact_chapter[fid] = ch
            # 可见性:随机授予部分在场者(至少 1 人,制造 POV 敏感)
            world.visibility[fid] = set(rng.sample(present, k=rng.randint(1, len(present))))
            chapter.new_fact_ids.append(fid)
            chapter.fact_texts[fid] = world.facts[fid]
            if is_remote:
                world.remote_fact_ids.add(fid)

        # ground truth:本章需要 近 2 章事实 + 1 条远距离事实(若有库存)。
        # 可见性感知:仅计"至少一名当前在场角色可见"的事实——POV 策略不应
        # 因为正确地隐藏了不可见信息而被记为漏召回。
        present_set = set(present)

        def visible_to_present(fid: str) -> bool:
            return bool(world.visibility[fid] & present_set)

        needed: list[str] = []
        for prev in world.chapters[-2:]:
            needed.extend(f for f in prev.new_fact_ids if visible_to_present(f))
        remote_pool = [
            f for f, c in world.fact_chapter.items()
            if f in world.remote_fact_ids and c < ch - 4 and visible_to_present(f)
        ]
        if remote_pool:
            needed.append(rng.choice(remote_pool))
        chapter.needed_fact_ids = needed
        world.chapters.append(chapter)

    return world
