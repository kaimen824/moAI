"""实体层服务(ADR-0015 激活:先查询再语义识别)。

三层漏斗消歧:
  ① 确定性层:精确名/别名/历史人工裁决命中 -> 直接合并(零成本零人工)
  ② 查询层:候选"名字:简介"embedding 与已有实体余弦,取 top-k(非阈值二分)
  ③ 语义识别层(节点内 LLM,批量):same/new/uncertain 结构化 verdict;
     仅 uncertain 进合并提案队列(复用 facts 抽检骨架)

先写后合并:uncertain 候选先作为独立实体落库,人工裁决后 execute_merge 归一
(合并可逆、漏检不可逆)。写路径经定稿单事务(commit_finalize)/API 裁决端点,
与 ADR-0013 编排层豁免同语义;读路径入口 fail-closed(同检索服务)。
"""

from __future__ import annotations

import uuid
from typing import Callable, Sequence

from app.memory.retrieval import cosine, decode_embedding
from app.memory.repository import AgentContext, Repository

EmbedFn = Callable[[Sequence[str]], list[list[float]]]

# 向量层下限:最高相似度低于此值 -> 视为全新,不送裁决
# (裁决的输入是 top-k 而非阈值带;该值只负责拦掉"明显无关"的候选)
LOW_SIMILARITY = 0.55


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class EntityService:
    def __init__(self, repo: Repository, embed_fn: EmbedFn | None = None):
        self._repo = repo
        self._embed_fn = embed_fn

    # ================= 读侧:漏斗 ①② =================

    def _load_active(self, story_id: str) -> tuple[list[dict], dict[str, dict]]:
        """载入本书全部 active 实体与 名字/别名 -> 实体 索引(单本量级,内存可载)。"""
        entities = [dict(r) for r in self._repo.conn.execute(
            "SELECT id, type, name, content, embedding, chapter_no FROM entities"
            " WHERE story_id=? AND status='active'",
            (story_id,)).fetchall()]
        by_key: dict[str, dict] = {}
        for e in entities:
            by_key.setdefault(e["name"], e)
        for r in self._repo.conn.execute(
                "SELECT a.alias, a.entity_id FROM entity_aliases a"
                " JOIN entities e ON e.id=a.entity_id AND e.status='active'"
                " WHERE a.story_id=?", (story_id,)).fetchall():
            ent = next((e for e in entities if e["id"] == r["entity_id"]), None)
            if ent is not None:
                by_key.setdefault(r["alias"], ent)   # 本名优先,别名兜底
        return entities, by_key

    def _decided_rules(self, story_id: str) -> tuple[dict[str, str], set[str]]:
        """历史人工裁决 -> 持久规则:merged 的候选名自动并向目标,new/ignored 自动新建。"""
        merge_rules: dict[str, str] = {}
        new_names: set[str] = set()
        for r in self._repo.conn.execute(
                "SELECT candidate_name, target_entity_id, status"
                " FROM entity_merge_proposals"
                " WHERE story_id=? AND status!='pending'", (story_id,)).fetchall():
            if r["status"] == "merged":
                merge_rules[r["candidate_name"]] = r["target_entity_id"]
            else:   # new | ignored:都是"不是同一个"
                new_names.add(r["candidate_name"])
        return merge_rules, new_names

    def known_entities(self, ctx: AgentContext, *, cap: int = 80) -> list[dict]:
        """本书 active 实体清单(抽取去重/裁决输入用;超量截断保 token)。"""
        self._repo.check_access(ctx, "entities", "read")
        return [dict(r) for r in self._repo.conn.execute(
            "SELECT id, type, name, content FROM entities"
            " WHERE story_id=? AND status='active' ORDER BY created_at LIMIT ?",
            (ctx.story_id, cap)).fetchall()]

    def resolve_candidates(
        self, ctx: AgentContext, candidates: Sequence[dict], *, top_k: int = 5,
    ) -> list[dict]:
        """漏斗①②:每个候选得到 merge / new / adjudicate(带 top-k 证据)之一。

        返回项:{"candidate": 原候选, "action": ..., "target_id"?, "reason"?,
                "topk": [{id,name,type,content,sim}, ...](仅 adjudicate)}
        """
        self._repo.check_access(ctx, "entities", "read")   # fail-closed 入口
        entities, by_key = self._load_active(ctx.story_id)
        merge_rules, new_names = self._decided_rules(ctx.story_id)

        out: list[dict] = []
        pending_vec: list[tuple[dict, str]] = []
        for c in candidates:
            name = (c.get("name") or "").strip()
            if not name:
                continue
            if name in merge_rules and merge_rules[name]:
                out.append({"candidate": c, "action": "merge",
                            "target_id": merge_rules[name],
                            "reason": "历史人工裁决:合并"})
                continue
            hit = by_key.get(name)
            if hit is not None:
                out.append({"candidate": c, "action": "merge",
                            "target_id": hit["id"],
                            "reason": "名称/别名精确命中"})
                continue
            if name in new_names:
                out.append({"candidate": c, "action": "new",
                            "reason": "历史人工裁决:独立实体"})
                continue
            pending_vec.append((c, name))

        # 向量层:无 embed_fn / 无已有实体 / embed 失败 -> 全部送裁决(兜底不丢)
        qvecs: list[list[float] | None] | None = None
        if pending_vec and entities and self._embed_fn is not None:
            try:
                raw = self._embed_fn([
                    f"{name}:{c.get('description', '')[:120]}"
                    for c, name in pending_vec])
                qvecs = [v if v else None for v in raw]
            except Exception:
                qvecs = None   # embedding 故障不阻塞:降级为纯语义裁决

        name_inventory = [e["name"] for e in entities]
        for i, (c, name) in enumerate(pending_vec):
            topk: list[dict] = []
            if qvecs is not None and qvecs[i] is not None:
                qv = qvecs[i]
                scored = []
                for e in entities:
                    vec = decode_embedding(e["embedding"])
                    if vec:
                        scored.append((cosine(qv, vec), e))
                scored.sort(key=lambda t: t[0], reverse=True)
                if scored and scored[0][0] >= LOW_SIMILARITY:
                    topk = [{"id": e["id"], "name": e["name"], "type": e["type"],
                             "content": (e.get("content") or "")[:160], "sim": round(s, 3)}
                            for s, e in scored[:top_k]]
            if not topk and not name_inventory:
                out.append({"candidate": c, "action": "new", "reason": "尚无任何实体"})
                continue
            out.append({"candidate": c, "action": "adjudicate", "topk": topk,
                        "inventory": name_inventory})
        return out

    # ================= 写侧:产出待落库行(state 增量,单事务消费)=================

    def apply_verdicts(
        self, ctx: AgentContext, resolutions: Sequence[dict],
        decisions: dict[str, dict], chapter_no: int,
    ) -> dict:
        """漏斗③结果 -> 待落库行集合(commit_finalize 单事务写入)。

        decisions: 候选名 -> {"decision": "same", "target_id"} | {"decision": "new"}
                   | {"decision": "uncertain", "target_id"(最相似者), "evidence"}
        返回 {"new_entities", "aliases", "links", "proposals"}(行数据,含预生成 id)。
        """
        entities, by_key = self._load_active(ctx.story_id)
        name_to_id = {e["name"]: e["id"] for e in entities}
        new_entities: list[dict] = []
        aliases: list[dict] = []
        proposals: list[dict] = []

        for r in resolutions:
            c = r["candidate"]
            name = (c.get("name") or "").strip()
            if not name:
                continue
            d = decisions.get(name, {})
            decision = d.get("decision")
            if r["action"] == "merge":
                target = r.get("target_id")
                if target and target in name_to_id.values():
                    # 确定性/历史规则命中:候选名吸收为目标别名(下次精确命中)
                    aliases.append({"alias": name, "entity_id": target})
                continue
            if decision == "same" and d.get("target_id"):
                aliases.append({"alias": name, "entity_id": d["target_id"]})
                continue
            # new / uncertain / 未裁决:先写后合并 -> 独立条目
            eid = uuid.uuid4().hex
            new_entities.append({
                "id": eid, "type": self._norm_type(c.get("type")),
                "name": name, "content": (c.get("description") or "")[:500],
                "chapter_no": chapter_no,
            })
            name_to_id[name] = eid
            for a in c.get("aliases", []) or []:
                a = (a or "").strip()
                if a and a != name:
                    aliases.append({"alias": a, "entity_id": eid})
            if decision == "uncertain" and d.get("target_id"):
                proposals.append({
                    "candidate_name": name, "candidate_entity_id": eid,
                    "target_entity_id": d["target_id"],
                    "similarity": d.get("similarity"),
                    "evidence": (d.get("evidence") or "")[:500],
                    "chapter_no": chapter_no,
                })
        return {"new_entities": new_entities, "aliases": aliases,
                "links": [], "proposals": proposals}

    def resolve_links(
        self, ctx: AgentContext, links: Sequence[dict],
        name_to_id_extra: dict[str, str], chapter_no: int,
    ) -> list[dict]:
        """抽取产出的 links(名字对) -> 行数据;端点无法解析的链接丢弃。"""
        entities, _ = self._load_active(ctx.story_id)
        name_to_id = {e["name"]: e["id"] for e in entities}
        name_to_id.update(name_to_id_extra)   # 本章新建实体优先(新 id 覆盖旧名)
        rows = []
        for l in links:
            src = name_to_id.get((l.get("from") or "").strip())
            dst = name_to_id.get((l.get("to") or "").strip())
            rel = (l.get("relation") or "").strip()
            if src and dst and src != dst and rel:
                rows.append({"id": uuid.uuid4().hex, "from_entity": src,
                             "to_entity": dst, "relation": rel[:120],
                             "chapter_no": chapter_no})
        return rows

    @staticmethod
    def _norm_type(t: str | None) -> str:
        t = (t or "concept").strip().lower()
        return t if t in {"character", "faction", "location", "item",
                          "technique", "concept"} else "concept"

    # ================= 阶段滚动:本阶段被触达的实体 =================

    def stage_touched(self, ctx: AgentContext, start: int, end: int, *,
                      cap: int = 12) -> list[dict]:
        """阶段内新建或新增链接的实体(阶段末滚动摘要的对象)。"""
        rows = self._repo.conn.execute(
            "SELECT DISTINCT e.id, e.name, e.type, e.content FROM entities e"
            " WHERE e.story_id=? AND e.status='active'"
            "   AND (COALESCE(e.chapter_no, 0) BETWEEN ? AND ?"
            "        OR e.id IN (SELECT from_entity FROM entity_links"
            "                    WHERE story_id=? AND COALESCE(chapter_no,0) BETWEEN ? AND ?)"
            "        OR e.id IN (SELECT to_entity FROM entity_links"
            "                     WHERE story_id=? AND COALESCE(chapter_no,0) BETWEEN ? AND ?))"
            " ORDER BY e.created_at LIMIT ?",
            (ctx.story_id, start, end, ctx.story_id, start, end,
             ctx.story_id, start, end, cap)).fetchall()
        return [dict(r) for r in rows]

    # ================= 人工裁决执行(API 层调用,自带锁与事务)=================

    def execute_merge(self, conn, proposal: dict) -> None:
        """执行合并:候选实体链接重定向 -> 名字吸收为目标别名 -> 候选置 merged。

        调用方负责事务与 run_lock(与编排层单事务同语义)。
        """
        story_id = proposal["story_id"]
        cand, target = proposal["candidate_entity_id"], proposal["target_entity_id"]
        if cand and cand != target:
            conn.execute(
                "UPDATE entity_links SET from_entity=? WHERE story_id=? AND from_entity=?",
                (target, story_id, cand))
            conn.execute(
                "UPDATE entity_links SET to_entity=? WHERE story_id=? AND to_entity=?",
                (target, story_id, cand))
            conn.execute(
                "INSERT OR REPLACE INTO entity_aliases (alias, story_id, entity_id, created_at)"
                " VALUES (?,?,?,?)",
                (proposal["candidate_name"], story_id, target, _now()))
            conn.execute(
                "UPDATE entities SET status='merged', updated_at=? WHERE id=?",
                (_now(), cand))
        conn.execute(
            "UPDATE entity_merge_proposals SET status='merged', decided_at=? WHERE id=?",
            (_now(), proposal["id"]))
