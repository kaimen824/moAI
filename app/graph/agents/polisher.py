"""精校 Agent(ADR-0018):文风类返工的表达层修订,替代全量重写。

触发条件(merge 节点确定性裁决):双评审均 revise 且 fix_scope=style。
只改措辞/句式/节奏/冗余,不动事实与剧情——产物回同一双评审复检兜底。
输入仅需草稿+评审意见+禁用清单(不带全套上下文),便宜模型。
"""

from __future__ import annotations

from app.core.config import AgentRole
from app.core.llm.base import ChatMessage
from app.graph.agents.base import BaseAgent, NodeDeps, register_agent
from app.graph.agents.writer import strip_markdown_title

_SYSTEM = (
    "你是小说精校编辑。对草稿做表达层修订与评审处方的局部执行:"
    "只改措辞、句式、节奏与冗余,并落实评审'必须修改'中给出的精确处方。"
    "铁律(违反任何一条即失败):\n"
    "1. 不得改变任何事实、人名、称谓、事件顺序、数值、能力边界与信息边界——"
    "唯一例外:评审意见中以'将X改为Y'形式明确指定的修正必须逐条精确执行,\n"
    "除此之外的事实层面一律不动;\n"
    "2. 不得增删情节、场景或对白的信息量(可压缩冗余,不可新增设定);\n"
    "3. 字数浮动不超过 ±10%;\n"
    "4. [禁用表达]清单中的短语必须全部清除(换写法,不删内容;"
    "评审处方指定的称谓替换以处方为准);\n"
    "5. 开头第一句与结尾最后一句的场景锚点保持不变(保上下章衔接)。\n"
    "直接输出修订后的完整正文,不要说明与标题。"
)


@register_agent
class PolishDraftNode(BaseAgent):
    """文风返工通道:精校已有草稿,而非按要点全量重写。"""

    name = "polisher"
    role = AgentRole.POLISH

    def __call__(self, state: dict, deps: NodeDeps) -> dict:
        deps.emit("draft_start", {
            "chapter_no": state.get("chapter_no"),
            "round": state.get("rewrite_count", 0) + 1,
        })
        outline_fb = state.get("outline_review", {}).get("feedback") or ""
        quality_fb = state.get("quality_review", {}).get("feedback") or ""
        ban = "\n".join(f"- {p}" for p in
                        state.get("context_bundle", {}).get("style_ban", []))
        user = (f"[待精校草稿(第{state.get('chapter_no')}章)]\n{state.get('draft', '')}\n\n"
                f"[评审意见(逐条解决其中的文风问题)]\n"
                f"大纲评审:{outline_fb}\n质量评审:{quality_fb}"
                + (f"\n\n[禁用表达]\n{ban}" if ban else ""))

        def _stream() -> str:
            chunks: list[str] = []
            for token in self.llm.stream(
                self.role,
                [ChatMessage("system", _SYSTEM), ChatMessage("user", user)],
                stage="polish", story_id=state.get("story_id", ""),
            ):
                chunks.append(token)
                deps.emit("token", {"text": token})
            return "".join(chunks)

        streaming = any(t == deps._current_thread for t, _q in deps._subscribers)
        draft = _stream() if streaming else self.ask_text(
            _SYSTEM, user, stage="polish", story_id=state.get("story_id", ""))

        # 空输出防御:与写手同策略——重试一次,仍空显式报错,不把空稿送评审
        if not draft.strip():
            draft = self.ask_text(_SYSTEM, user, stage="polish",
                                  story_id=state.get("story_id", ""))
            if streaming and draft.strip():
                deps.emit("token", {"text": draft})
        if not draft.strip():
            raise RuntimeError(
                f"精校模型连续两次返回空内容(第{state.get('chapter_no')}章"
                f"第{state.get('rewrite_count', 0) + 1}轮),已中止")
        draft = strip_markdown_title(draft)
        return {"draft": draft}
