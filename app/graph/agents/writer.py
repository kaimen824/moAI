"""写作 Agent:上下文拼装 + 正文生成。"""

from __future__ import annotations

from app.core.config import AgentRole
from app.core.llm.base import ChatMessage
from app.graph.agents.base import BaseAgent, NodeDeps, register_agent


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
    if bundle.get("active_threads"):
        th = "\n".join(f"- {t['description']}" for t in bundle["active_threads"])
        parts.append(f"[活跃伏笔]\n{th}")
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
            "3. 自然照应活跃伏笔;文风连贯。直接输出正文,不要标题和说明。"
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
        return {"draft": draft}
