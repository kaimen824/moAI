"""检索服务(ADR-0002/0003/0006):确定性代码,fail-closed。

管线:agent_acl 查域 → 强制 story_id 过滤 → POV 过滤 →
  ① 结构化查表(主路:在场角色 POV 记忆 + 角色卡 + 活跃伏笔)
  ② 实体链接扩展一跳
  ③ 向量兜底(embed_fn 可为 None = 桩,跳过;内存余弦)
每次检索落 retrieval_audit。
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Sequence

from app.memory.repository import AgentContext, Repository
from app.memory.world import get_pov_memory

# embed_fn: texts -> vectors(由 core/llm 注入;None = 向量兜底降级)
EmbedFn = Callable[[Sequence[str]], list[list[float]]]


# ---------- embedding 序列化(存 SQLite blob)----------

def encode_embedding(vec: Sequence[float]) -> bytes:
    return json.dumps(list(vec)).encode("utf-8")


def decode_embedding(blob: bytes | None) -> list[float] | None:
    if blob is None:
        return None
    return json.loads(blob)


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1e-12
    nb = math.sqrt(sum(y * y for y in b)) or 1e-12
    return dot / (na * nb)


# ---------- 检索结果 ----------

@dataclass
class RetrievedContext:
    pov_facts: list[dict] = field(default_factory=list)        # 含 knowledge_level/detail
    beliefs: list[dict] = field(default_factory=list)
    characters: list[dict] = field(default_factory=list)       # 在场角色卡
    active_threads: list[dict] = field(default_factory=list)   # 活跃伏笔
    expanded_entities: list[dict] = field(default_factory=list)  # 链接扩展一跳
    vector_hits: list[dict] = field(default_factory=list)      # 向量兜底命中(长尾)
    degraded_vector: bool = False                              # True=embed 桩,跳过了兜底


class RetrievalService:
    def __init__(self, repo: Repository, embed_fn: EmbedFn | None = None):
        self._repo = repo
        self._embed_fn = embed_fn

    # ---------- 主入口 ----------
    def retrieve_for_chapter(
        self,
        ctx: AgentContext,
        chapter_no: int,
        present_character_ids: Sequence[str],
        *,
        query_text: str | None = None,
        vector_top_k: int = 5,
    ) -> RetrievedContext:
        started = time.perf_counter()
        self._repo.check_access(ctx, "facts", "read")   # fail-closed 入口
        result = RetrievedContext()

        branch = self._repo.main_branch(ctx)
        characters = {c["id"]: c for c in map(vars, self._repo.get_characters(ctx))}

        # ① 主路:在场角色 POV + 角色卡 + 活跃伏笔
        seen_fact_ids: set[str] = set()
        for char_id in present_character_ids:
            pov = get_pov_memory(self._repo.conn, ctx.story_id, branch, char_id, chapter_no)
            for f in pov["facts"]:
                if f["id"] not in seen_fact_ids:
                    seen_fact_ids.add(f["id"])
                    result.pov_facts.append(f)
            for b in pov["beliefs"]:
                result.beliefs.append(b)
            if char_id in characters:
                result.characters.append(characters[char_id])
        result.active_threads = [vars(t) for t in self._repo.get_plot_threads(ctx, status="open")]

        # ② 链接扩展一跳(在场角色的实体邻居)
        entity_ids = [c["entity_id"] for c in result.characters if c.get("entity_id")]
        if entity_ids:
            result.expanded_entities = [vars(e) for e in self._repo.get_linked_entities(ctx, entity_ids)]

        # ③ 向量兜底:query embedding 对全库 facts 余弦 top-k(排除已含)
        if query_text:
            if self._embed_fn is None:
                result.degraded_vector = True
            else:
                result.vector_hits = self._vector_fallback(
                    ctx, branch, query_text, exclude=seen_fact_ids, top_k=vector_top_k
                )

        self._audit(ctx, query_text, len(result.pov_facts) + len(result.vector_hits),
                    int((time.perf_counter() - started) * 1000))
        return result

    # ---------- 向量兜底 ----------
    def _vector_fallback(
        self, ctx: AgentContext, branch: str, query_text: str,
        *, exclude: set[str], top_k: int,
    ) -> list[dict]:
        try:
            qvec = self._embed_fn([query_text])[0]
        except Exception:
            return []
        rows = self._repo.conn.execute(
            "SELECT id, type, content, chapter_established, embedding FROM facts"
            " WHERE story_id=? AND branch_id=? AND embedding IS NOT NULL",
            (ctx.story_id, branch),
        ).fetchall()
        scored: list[tuple[float, dict]] = []
        for r in rows:
            if r["id"] in exclude:
                continue
            vec = decode_embedding(r["embedding"])
            if vec:
                scored.append((cosine(qvec, vec), dict(r)))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [item for _, item in scored[:top_k]]

    # ---------- 审计(系统级写入,非 Agent 权限域)----------
    def _audit(self, ctx: AgentContext, query: str | None, count: int, latency_ms: int) -> None:
        self._repo.conn.execute(
            "INSERT INTO retrieval_audit (id, story_id, caller, query, returned_count, latency_ms, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (uuid.uuid4().hex, ctx.story_id, ctx.agent_name, query, count, latency_ms,
             datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
        self._repo.conn.commit()
