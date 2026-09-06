"""写作 Agent:上下文拼装 + 正文生成。"""

from __future__ import annotations

from app.core.config import AgentRole
from app.core.llm.base import ChatMessage
from app.graph.agents.base import BaseAgent, NodeDeps, register_agent


def render_context(state: dict) -> str:
    """把检索服务组装的 context_bundle 渲染为 prompt 片段(POV 已在检索层过滤)。"""
    bundle = state.get("context_bundle", {})
    parts: list[str] = []
    if state.get("master_outline"):
        parts.append(f"[全书大纲]\n{state['master_outline']}")
    if state.get("chapter_brief"):
        parts.append(f"[本章要点]\n{state['chapter_brief']}")
    if bundle.get("characters"):
        chars = "\n".join(f"- {c['name']}: {c.get('profile','')[:200]}" for c in bundle["characters"])
        parts.append(f"[在场角色卡]\n{chars}")
    if bundle.get("pov_facts"):
        facts = "\n".join(f"- {f['content']}" for f in bundle["pov_facts"])
        parts.append(f"[角色已知事实(POV,不得越界)]\n{facts}")
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
        system = (
            "你是小说执笔者。依据上下文写本章正文(1500-2500 字),要求:"
            "严格遵守[角色已知事实]的视角边界——角色只知道列出的内容,不得出现角色不该知道的信息;"
            "自然照应活跃伏笔;文风连贯。直接输出正文,不要标题和说明。"
        )
        user = render_context(state)
        # SSE 监听时逐 token 流式;否则一次性
        if deps._subscribers:
            chunks: list[str] = []
            for token in self.llm.stream(
                self.role,
                [ChatMessage("system", system), ChatMessage("user", user)],
                stage="draft", story_id=state.get("story_id", ""),
            ):
                chunks.append(token)
                deps.emit("token", {"text": token})
            return {"draft": "".join(chunks)}
        draft = self.ask_text(system, user, stage="draft",
                              story_id=state.get("story_id", ""))
        return {"draft": draft}
