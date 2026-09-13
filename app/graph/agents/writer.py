"""写作 Agent:上下文拼装 + 正文生成。"""

from __future__ import annotations

import re

from app.core.config import AgentRole
from app.core.llm.base import ChatMessage
from app.graph.agents.base import BaseAgent, NodeDeps, register_agent

_TYPE_ZH = {
    "character": "角色", "faction": "势力", "location": "地点",
    "item": "物品", "technique": "功法", "concept": "概念",
}


def strip_markdown_title(text: str) -> str:
    """剥离开头残留的 markdown 标题行(模型偶发无视'不要标题'指令,ADR-0017)。"""
    lines = text.lstrip().splitlines()
    if lines and re.match(r"^#{1,6}\s*\S", lines[0]):
        lines = lines[1:]
    return "\n".join(lines).strip()


def render_context(state: dict, *, max_facts: int = 40) -> str:
    """把检索服务组装的 context_bundle 渲染为 prompt 片段(POV 已在检索层过滤)。

    pov_facts 按新近度排序截断(长篇防爆上下文):最近章的事实全保真,
    更早的按章号倒序取前 max_facts 条,并注明有省略。
    """
    bundle = state.get("context_bundle", {})
    parts: list[str] = []
    if bundle.get("user_directives"):
        lines = "\n".join(f"- {d}" for d in bundle["user_directives"])
        parts.append(f"[用户指示(最高优先级,必须遵从)]\n{lines}")
    if state.get("capability_contract"):
        parts.append(f"[核心能力契约(主角能力的硬边界,不得越权)]\n{state['capability_contract']}")
    if bundle.get("character_intents"):
        ints = "\n".join(
            f"- {i['name']}|目标:{i.get('goal', '')}|确知:{i.get('knows', '')}"
            f"|不知:{i.get('doesnt_know', '')}|将为自己做:{i.get('self_interest', '')}"
            for i in bundle["character_intents"][:8]
        )
        parts.append(
            "[在场角色意图(每个角色按自身利益行动;'不知'中的信息该角色言行不得引用)]\n" + ints)
    if state.get("master_outline"):
        parts.append(f"[全书大纲]\n{state['master_outline']}")
    if state.get("chapter_brief"):
        parts.append(f"[本章要点]\n{state['chapter_brief']}")
    if bundle.get("characters"):
        chars = "\n".join(f"- {c['name']}: {c.get('profile','')[:200]}" for c in bundle["characters"])
        parts.append(f"[在场角色卡]\n{chars}")
    if bundle.get("pov_facts"):
        facts_sorted = sorted(
            bundle["pov_facts"],
            key=lambda f: f.get("chapter_established") or 0, reverse=True,
        )
        shown, hidden = facts_sorted[:max_facts], facts_sorted[max_facts:]
        lines = [f"- [ch{f.get('chapter_established', '?')}] {f['content']}" for f in shown]
        if hidden:
            lines.append(f"(另有 {len(hidden)} 条更早的事实已省略,涉及时可自然照应)")
        parts.append(f"[角色已知事实(POV,不得越界;标注发生在第几章)]\n" + "\n".join(lines))
    if bundle.get("beliefs"):
        bl = "\n".join(f"- {b['content']}" for b in bundle["beliefs"])
        parts.append(f"[角色认知(可能包含误信)]\n{bl}")
    if bundle.get("vector_hits"):
        vh = "\n".join(
            f"- [ch{h.get('chapter_established', '?')}] {h['content']}"
            for h in bundle["vector_hits"][:5]
        )
        parts.append(
            "[世界背景(按本章要点向量召回的远期客观事实——"
            "可用于叙事描写;角色的言行与内心不得引用其中角色不该知晓的信息)]\n" + vh
        )
    if bundle.get("expanded_entities"):
        ents = "\n".join(
            f"- {e['name']}({_TYPE_ZH.get(e.get('type'), e.get('type', '?'))}):"
            f"{(e.get('content') or '')[:150]}"
            for e in bundle["expanded_entities"][:8]
        )
        parts.append(f"[相关设定(在场角色的关联实体,一跳邻居;照应设定,不得矛盾)]\n{ents}")
    if bundle.get("canonical_names"):
        canon = "\n".join(
            f"- {e['name']}({_TYPE_ZH.get(e.get('type'), e.get('type', '?'))})"
            + (f"(又称:{'/'.join(e['aliases'])})" if e.get("aliases") else "")
            for e in bundle["canonical_names"][:60]
        )
        parts.append(
            "[实体规范名(全书专有名词,叙述层一律用规范名;别名仅限角色对白口吻)]\n"
            + canon)
    threads = bundle.get("active_threads") or []
    adv = [t for t in threads if not t.get("_suspend")]
    susp = [t for t in threads if t.get("_suspend")]
    if adv:
        # 超龄伏笔标[应回收](ADR-0020 账龄梯度:回收优先级显式传导给写手)
        th = "\n".join(
            ("- [应回收] " if t.get("_overdue") else "- ") + t["description"]
            for t in adv)
        parts.append(f"[活跃伏笔(可推进;每章至多推进一条;标[应回收]的优先安排)]\n{th}")
    if susp:
        th = "\n".join(f"- {t['description']}" for t in susp)
        parts.append(f"[悬置伏笔(只许加深神秘感,严禁解释或回收)]\n{th}")
    if bundle.get("style_ban"):
        sb = "\n".join(f"- {p}" for p in bundle["style_ban"])
        parts.append(f"[禁用表达(近章已高频复现,本章一律不用)]\n{sb}")
    if bundle.get("carryover"):
        parts.append(f"[上期衔接]\n{bundle['carryover']}")
    feedback = state.get("quality_review", {}).get("feedback") or ""
    outline_fb = state.get("outline_review", {}).get("feedback") or ""
    if feedback or outline_fb:
        parts.append(f"[修改意见(优先处理)]\n大纲评审:{outline_fb}\n质量评审:{feedback}")
    return "\n\n".join(parts)


@register_agent
class WriterNode(BaseAgent):
    name = "writer"
    role = AgentRole.WRITER

    def __call__(self, state: dict, deps: NodeDeps) -> dict:
        # 草稿版本标记:评审回流重写时,前端凭此区分第 N 稿(而非把两稿糊在同一段流里)
        deps.emit("draft_start", {
            "chapter_no": state.get("chapter_no"),
            "round": state.get("rewrite_count", 0) + 1,
        })
        system = (
            "你是长篇网文执笔者。依据上下文写本章正文(2500-4000 字,网文单章体量),要求:\n"
            "1. [角色已知事实]是已经发生过的背景(标注了章号):角色只知道列出的内容,"
            "不得出现角色不该知道的信息;可以自然照应,但严禁把已发生的事件当作本章"
            "剧情重演或换措辞复写——本章必须推进新事件。\n"
            "2. 开场必须衔接[上期衔接]给出的上一章结尾状态(场景/时间/人物位置),"
            "时间线只能向前;开场环境描写不得与上一章重复。\n"
            "3. 伏笔纪律:[活跃伏笔]每章至多推进一条;[悬置伏笔]只许加深神秘感,"
            "严禁解释、回收或让角色讨论出真相。\n"
            "4. 能力边界:若给出[核心能力契约],主角能力严格遵守契约——能力只能提供"
            "线索、现象或部分信息,严禁直接给出'谁做的/为什么/该怎么办'级别的结论;"
            "关键突破必须来自观察、推理、试错或他人的言行,而非能力扫描。\n"
            "5. 人物自主:每个在场角色按[在场角色意图]中其自身目标和利益行动,"
            "其'不知'清单中的信息该角色不得引用;每章至少一个角色做一件符合自身"
            "利益但不利于主角的事。\n"
            "6. 反AI痕迹:严禁信息倾泻(连续三行以上的设定罗列/数值播报);解释性对白"
            "压到最低(角色不为读者上课);伪精确数值(百分比/倒计时/小数)仅当设定"
            "已建立且必要时使用;[禁用表达]清单中的短语一律不得出现。\n"
            "7. 本章结束时必须留有未解决的张力(未答之问/新麻烦/误判的后果);"
            "若[本章要点]标明受挫,必须落实失败及其延续后果,不得当场翻盘。"
            "直接输出正文,不要标题和说明。"
        )
        user = render_context(state)

        def _stream() -> str:
            chunks: list[str] = []
            for token in self.llm.stream(
                self.role,
                [ChatMessage("system", system), ChatMessage("user", user)],
                stage="draft", story_id=state.get("story_id", ""),
            ):
                chunks.append(token)
                deps.emit("token", {"text": token})
            return "".join(chunks)

        # SSE 监听时逐 token 流式;否则一次性
        streaming = any(t == deps._current_thread for t, _q in deps._subscribers)
        draft = _stream() if streaming else self.ask_text(
            system, user, stage="draft", story_id=state.get("story_id", ""))

        # 空输出防御:模型偶发返回空流(实测案例:35s/0 token)。
        # 重试一次(非流式,拿到完整内容再整段下发);仍空则显式报错——
        # 不把空草稿送进评审管道。
        if not draft.strip():
            draft = self.ask_text(system, user, stage="draft",
                                  story_id=state.get("story_id", ""))
            if streaming and draft.strip():
                deps.emit("token", {"text": draft})
        if not draft.strip():
            raise RuntimeError(
                f"写作模型连续两次返回空内容(第{state.get('chapter_no')}章"
                f"第{state.get('rewrite_count', 0) + 1}稿),已中止——请重试或换模型")
        draft = strip_markdown_title(draft)   # 剥离残留的 markdown 标题行
        return {"draft": draft}
