"""实体管理 Agent(定稿管道,ADR-0015):漏斗③语义识别 + 变更集组装。

先查询(EntityService.resolve_candidates 已做确定性层+向量层)再语义识别:
LLM 批量裁决 same/new/uncertain(结构化 verdict),仅 uncertain 进人工提案队列。
产出暂存变更集(entity_changes,存图状态,定稿单事务落库——与 fact_changes 同链路)。
"""

from __future__ import annotations

from app.core.config import AgentRole
from app.graph.agents.base import BaseAgent, NodeDeps, register_agent
from app.memory.repository import AgentContext

_ADJUDICATE_SYSTEM = (
    "你是实体消歧裁决官。对每个候选实体,判断它与候选相似列表中的哪个已有实体"
    "是同一个(道号/俗称/尊称/简称/别名都算同一个),严格按 JSON 输出:\n"
    '{"decisions":[{"name":"候选名","decision":"same|new|uncertain",'
    '"target":"同一实体的名字(仅 same 时填)","evidence":"一句话依据"}]}\n'
    "规则:\n"
    "1. 判'同一个'要有依据:相似的称呼习惯(如'剑尘真人'是'李剑尘'的道号)、"
    "身份/门派/描述高度重合;名字相似但身份描述冲突(如'林家三少'与'林家四少')"
    "必须判 new。\n"
    "2. 相似列表为空或都不像 -> new。证据不足以下结论 -> uncertain(交人工)。\n"
    "3. 不确定的宁选 uncertain,不要猜 same(错误合并会污染整张实体图)。"
)


@register_agent
class EntityResolveNode(BaseAgent):
    """定稿管道:实体候选消歧(entity_changes 暂存,commit_finalize 落库)。"""

    name = "entity_manager"
    role = AgentRole.ENTITY

    def __call__(self, state: dict, deps: NodeDeps) -> dict:
        story_id = state.get("story_id", "")
        chapter_no = state.get("chapter_no", 0)
        changes = state.get("fact_changes", {}) or {}
        candidates = changes.get("entities", []) or []
        ctx = AgentContext("entity_manager", story_id)

        if not candidates:
            return {"entity_changes": {"new_entities": [], "aliases": [],
                                        "links": [], "proposals": []}}

        resolutions = deps.entities.resolve_candidates(ctx, candidates)

        # 漏斗③:仅待裁决候选进 LLM(批量,一次调用)
        decisions: dict[str, dict] = {}
        to_judge = [r for r in resolutions if r["action"] == "adjudicate"]
        if to_judge:
            blocks = []
            for r in to_judge:
                c = r["candidate"]
                topk = r.get("topk") or []
                sim_lines = "\n".join(
                    f"  - {t['name']}({t['type']}) 相似度{t['sim']}: {t['content']}"
                    for t in topk) or "  (无向量相似命中)"
                blocks.append(
                    f"候选:{c.get('name')}({c.get('type', '未知')})\n"
                    f"  描述:{c.get('description', '')}\n"
                    f"  相似列表:\n{sim_lines}")
            inventory = "、".join(to_judge[0].get("inventory", [])[:80]) or "(无)"
            verdict = self.ask_json(
                _ADJUDICATE_SYSTEM,
                f"[已有实体全名单(辅助判断,可能不在相似列表中)]\n{inventory}\n\n"
                f"[候选列表]\n" + "\n\n".join(blocks),
                stage="entity_resolve",
                story_id=story_id,
            )
            # 目标 id 解析:全量清单 ∪ 向量 top-k(top-k 同源自 active 实体)
            id_by_name = {e["name"]: e["id"]
                          for e in deps.entities.known_entities(ctx, cap=500)}
            for r in to_judge:
                for t in (r.get("topk") or []):
                    id_by_name[t["name"]] = t["id"]
            for d in verdict.get("decisions", []):
                name = (d.get("name") or "").strip()
                if not name or name not in {r["candidate"]["name"] for r in to_judge}:
                    continue   # 模型幻觉名:丢弃
                decision = d.get("decision")
                if decision == "same":
                    tid = id_by_name.get((d.get("target") or "").strip())
                    decisions[name] = ({"decision": "same", "target_id": tid}
                                       if tid else {"decision": "new"})
                else:
                    decisions[name] = {"decision": decision or "new",
                                       "evidence": d.get("evidence", "")}
            # uncertain 补充证据与目标(提案队列展示用)
            for r in to_judge:
                d = decisions.get(r["candidate"]["name"])
                if d and d.get("decision") == "uncertain":
                    topk = r.get("topk") or []
                    if topk:
                        d["target_id"] = topk[0]["id"]
                        d["similarity"] = topk[0]["sim"]
                        d.setdefault("evidence", "")

        entity_changes = deps.entities.apply_verdicts(ctx, resolutions, decisions, chapter_no)
        # 抽取产出的 links:名字对 -> 行(端点含本章新建实体)
        new_id_map = {e["name"]: e["id"] for e in entity_changes["new_entities"]}
        alias_map = {a["alias"]: a["entity_id"] for a in entity_changes["aliases"]}
        entity_changes["links"] = deps.entities.resolve_links(
            ctx, changes.get("links", []) or [], {**alias_map, **new_id_map}, chapter_no)
        return {"entity_changes": entity_changes}
