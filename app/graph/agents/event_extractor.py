"""事件管理 Agent(定稿管道步骤 1):从正文抽取事实/认知/可见性变更集。

输出为暂存变更集(存图状态,定稿单事务落库,ADR-0003 写链路);
含置信度分层(E3)与冲突检测标记(R5)。
"""

from __future__ import annotations

from app.core.config import AgentRole
from app.graph.agents.base import BaseAgent, NodeDeps, register_agent
from app.graph.agents.schemas import FactChanges
from app.memory.repository import AgentContext

_TYPE_ZH = {
    "character": "角色", "faction": "势力", "location": "地点",
    "item": "物品", "technique": "功法", "concept": "概念",
}

_SYSTEM = (
    "你是叙事事实抽取器。从章节正文中抽取客观世界事实与角色认知,严格按 JSON 输出:\n"
    '{"facts":[{"content":"事实陈述","type":"event|state|setting|relation",'
    '"confidence":"high|low","visible_to":["角色名",...],'
    '"supersedes":"被本章取代的已知事实内容(仅 state/setting 类,可空)"}],\n'
    '"beliefs":[{"character":"角色名","content":"该角色以为...(可为误信)"}],\n'
    '"entities":[{"name":"实体名","type":"character|faction|location|item|technique|concept",'
    '"description":"一句话说明该实体是什么"}],\n'
    '"links":[{"from":"实体名","to":"实体名","relation":"关系短语(自由文本,如:师徒/隶属/敌对/发现)"}],\n'
    '"conflicts":[{"description":"与已知事实的矛盾"}]}\n'
    "规则:\n"
    "1. 语义分层:event=一次性过往事件(发生过即不变);state=持续状态"
    "(可被后续章节的新状态取代,此时填 supersedes);setting=场景级环境"
    "(时间/天气/地点氛围,随场景切换失效,新场景的 setting 应 supersede 旧 setting);"
    "relation=人物/势力间关系。\n"
    "2. 与[已知事实]语义相同或高度相近的事实不要重复抽取;本章内容若只是"
    "重演已知事件,也不得再次入库。\n"
    "3. 只抽正文明确陈述的(显式 -> confidence=high);需要推断的隐含信息 -> low;"
    "visible_to 为正文中知晓该事实的角色名;与[已知事实]矛盾的抽到 conflicts。\n"
    "4. 主观/客观分层(ADR-0017):只有叙述者客观描写的既成事实才进 facts;"
    "角色的口头陈述、宣称、转述、听来的情报一律进 beliefs(character=陈述/接收者)"
    "——即便听起来可信,也可能是假话、误传或欺骗;真相由后续章节的 facts 检验。\n"
    "5. entities 抽本章出场的关键实体:角色/势力/地点/物品/功法/概念;"
    "[已知实体清单]中已收录的不要重复输出(除非本章建立了它的新链接);"
    "机构/组织/部门类势力与固定地点在正文被明确命名即抽取,勿因'日常场景'"
    "省略——它们是全书称谓统一的锚点(ADR-0019);"
    "links 只抽正文明确呈现的关系,from/to 必须是 entities 或清单中的名字。"
)


@register_agent
class EventExtractNode(BaseAgent):
    name = "event_manager"
    role = AgentRole.EVENT

    def __call__(self, state: dict, deps: NodeDeps) -> dict:
        bundle = state.get("context_bundle", {})
        known = "\n".join(
            f"- [ch{f.get('chapter_established', '?')}] {f['content']}"
            for f in bundle.get("pov_facts", [])[:30]
        )
        known_entities = "\n".join(
            f"- {e['name']}({_TYPE_ZH.get(e.get('type'), e.get('type'))})"
            + (f":{(e.get('content') or '')[:60]}" if e.get("content") else "")
            for e in deps.entities.known_entities(
                AgentContext("event_manager", state.get("story_id", "")))
        ) or "(暂无)"
        result = self.ask_json(
            _SYSTEM,
            f"[第{state.get('chapter_no', 0)}章正文]\n{state.get('draft','')[:6000]}\n\n"
            f"[已知事实(用于去重/取代/冲突检测)]\n{known or '(本章之前无)'}\n\n"
            f"[已知实体清单(去重用,勿重复输出)]\n{known_entities}",
            stage="extract_facts",
            story_id=state.get("story_id", ""),
            schema=FactChanges,
        )
        # 规范化 + 角色名 -> id 映射(暂存变更集,落库时用)
        name_to_id = deps.character_name_map(state)
        for f in result.get("facts", []):
            f["visible_ids"] = [name_to_id[n] for n in f.get("visible_to", []) if n in name_to_id]
        for b in result.get("beliefs", []):
            b["character_id"] = name_to_id.get(b.get("character"), "")
        result["chapter_no"] = state.get("chapter_no", 0)
        return {"fact_changes": result}
