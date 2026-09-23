"""精校 Agent(ADR-0018;ADR-0038 改造):局部替换编辑工具,替代全文重写。

模式 A(默认,部分替换):模型输出编辑列表 [{find, replace}],代码精确
匹配套用——未改动文本构造性原样保留(不可能引入新瑕疵),输出 token
从整章降到改动量。失配编辑回填重试一次,零命中回退模式 B。
模式 B(兜底,全文重写):旧行为,整章输出 ±10%。
输入仅需草稿+评审意见+用户意见+禁用清单(不带全套上下文),便宜模型。
"""

from __future__ import annotations

from app.core.config import AgentRole
from app.core.llm.base import ChatMessage
from app.graph.agents.base import BaseAgent, LLMFormatError, NodeDeps, register_agent
from app.graph.agents.schemas import PolishEdits
from app.graph.agents.writer import strip_markdown_title


def apply_edits(text: str, edits: list[dict]) -> tuple[str, list[dict], list[dict]]:
    """顺序套用局部替换;find 必须逐字精确且唯一。

    返回 (新文本, 已套用列表, 失配列表[{find, reason}])。唯一性以套用
    时刻的文本为准(前一条编辑可能改变后一条的命中数)。
    """
    applied: list[dict] = []
    failed: list[dict] = []
    for e in edits:
        find, repl = (e.get("find") or "").strip(), e.get("replace") or ""
        if find == repl:
            continue        # 空转编辑(模型偶发塞 find=replace):静默跳过,不烧重试
        if len(find) < 2:
            failed.append({"find": e.get("find", ""), "reason": "片段过短"})
            continue
        n = text.count(find)
        if n == 0:
            failed.append({"find": e.get("find", ""), "reason": "草稿中未找到(须逐字精确)"})
        elif n > 1:
            failed.append({"find": e.get("find", ""), "reason": f"命中 {n} 处,请加长片段使其唯一"})
        else:
            text = text.replace(find, repl)
            applied.append(e)
    return text, applied, failed


_SYSTEM_EDITS = (
    "你是小说精校编辑。对草稿做局部替换修订,严格按 JSON 输出:"
    '{"edits":[{"find":"原文精确片段","replace":"修订后片段"}]}\n'
    "铁律:\n"
    "1. find 必须是草稿中**逐字精确存在**的连续片段(建议 10-80 字),"
    "且在全文中**唯一**——不唯一就向两侧加长;不得复述、不得改写;"
    "不得输出 find 与 replace 相同的空编辑;想改一整段说明该拆成多条"
    "小编辑(find 超过 100 字即过长);\n"
    "2. 每条编辑只解决一个问题;评审'必须修改'中的处方('将X改为Y')"
    "逐条落成编辑,一条不落;\n"
    "3. [禁用表达]每处命中单独一条编辑(换写法,不删内容);\n"
    "4. 不得改动事实、人名、称谓、事件顺序、能力与信息边界(处方指定的"
    "精确修正除外);开头第一句与结尾最后一句不动;\n"
    "5. 无需修改时输出 {\"edits\":[]}。\n"
    "只输出 JSON,不要说明。"
)

# 模式 B 兜底(全文重写,ADR-0018 原行为)
_SYSTEM_FULL = (
    "你是小说精校编辑。对草稿做表达层修订与评审处方的局部执行:"
    "只改措辞、句式、节奏与冗余,并落实评审'必须修改'中给出的精确处方。"
    "铁律(违反任何一条即失败):\n"
    "1. 不得改变任何事实、人名、称谓、事件顺序、数值、能力边界与信息边界——"
    "唯一例外:评审意见或[用户修订意见]中以'将X改为Y'形式明确指定的修正"
    "必须逐条精确执行,除此之外的事实层面一律不动;\n"
    "2. 不得增删情节、场景或对白的信息量(可压缩冗余,不可新增设定);\n"
    "3. 字数浮动不超过 ±10%;\n"
    "4. [禁用表达]清单中的短语必须全部清除(换写法,不删内容;"
    "评审处方指定的称谓替换以处方为准);\n"
    "5. 开头第一句与结尾最后一句的场景锚点保持不变(保上下章衔接)。\n"
    "直接输出修订后的完整正文,不要说明与标题。"
)


@register_agent
class PolishDraftNode(BaseAgent):
    """文风返工通道:局部替换编辑为主,全文重写兜底。"""

    name = "polisher"
    role = AgentRole.POLISH

    def __call__(self, state: dict, deps: NodeDeps) -> dict:
        sid = state.get("story_id", "")
        deps.emit("draft_start", {
            "chapter_no": state.get("chapter_no"),
            "round": state.get("rewrite_count", 0) + 1,
        }, sid)
        user = self._build_user(state)
        streaming = deps.has_subscribers(sid)

        # 模式 A:局部替换(ADR-0038)。JSON 失败→模式 B 兜底,保证轮次推进
        try:
            batch = self.ask_json(_SYSTEM_EDITS, user, stage="polish",
                                  story_id=sid, schema=PolishEdits)
        except LLMFormatError:
            batch = None
        if batch is not None:
            edits = batch.get("edits", [])
            if not edits:   # 模型判定无需修改:原稿直返,复检兜底
                return {"draft": state.get("draft", "")}
            draft, applied, failed = apply_edits(state.get("draft", ""), edits)
            if failed:   # 失配回填重试一次(唯一一次)
                retry_user = (user + "\n\n[套用失败的编辑(修正 find 使其逐字"
                              "精确且唯一后重新输出全部失败项)]\n"
                              + "\n".join(f"- {f['find']!r}:{f['reason']}"
                                          for f in failed))
                try:
                    again = self.ask_json(_SYSTEM_EDITS, retry_user, stage="polish",
                                          story_id=sid, schema=PolishEdits)
                    draft, applied2, _ = apply_edits(draft, again.get("edits", []))
                    applied += applied2
                except LLMFormatError:
                    pass
            if applied:   # 命中任一编辑即成立;全部失配回退全文
                if streaming:
                    deps.emit("token", {"text": draft}, sid)
                # 编辑回执(ADR-0040):供复检评审核对"已落实的修改",防翻旧账
                return {"draft": draft, "last_polish_edits": applied}

        # 模式 B 兜底:全文重写(旧行为);无编辑回执可附
        draft = self._polish_full(state, deps, user, streaming)
        return {"draft": draft, "last_polish_edits": []}

    # ---- 内部 ----

    def _build_user(self, state: dict) -> str:
        outline_fb = state.get("outline_review", {}).get("feedback") or ""
        quality_fb = state.get("quality_review", {}).get("feedback") or ""
        decision = state.get("user_input") or {}
        user_fb = (decision.get("feedback") or ""
                   if decision.get("action") == "revise" else "")
        ban = "\n".join(f"- {p}" for p in
                        state.get("context_bundle", {}).get("style_ban", []))
        return (f"[待精校草稿(第{state.get('chapter_no')}章)]\n{state.get('draft', '')}\n\n"
                f"[评审意见(逐条解决其中的文风问题)]\n"
                f"大纲评审:{outline_fb}\n质量评审:{quality_fb}"
                + (f"\n\n[用户修订意见(最高优先级;'将X改为Y'式精确修正"
                   f"视同处方必须执行;表达层意见逐条落实)]\n{user_fb}" if user_fb else "")
                + (f"\n\n[禁用表达]\n{ban}" if ban else ""))

    def _polish_full(self, state: dict, deps: NodeDeps, user: str,
                     streaming: bool) -> str:
        sid = state.get("story_id", "")

        def _stream() -> str:
            chunks: list[str] = []
            for token in self.llm.stream(
                self.role,
                [ChatMessage("system", _SYSTEM_FULL), ChatMessage("user", user)],
                stage="polish", story_id=sid,
            ):
                chunks.append(token)
                deps.emit("token", {"text": token}, sid)
            return "".join(chunks)

        draft = _stream() if streaming else self.ask_text(
            _SYSTEM_FULL, user, stage="polish", story_id=sid)
        # 空输出防御:与写手同策略——重试一次,仍空显式报错,不把空稿送评审
        if not draft.strip():
            draft = self.ask_text(_SYSTEM_FULL, user, stage="polish", story_id=sid)
            if streaming and draft.strip():
                deps.emit("token", {"text": draft}, sid)
        if not draft.strip():
            raise RuntimeError(
                f"精校模型连续两次返回空内容(第{state.get('chapter_no')}章"
                f"第{state.get('rewrite_count', 0) + 1}轮),已中止")
        return strip_markdown_title(draft)
