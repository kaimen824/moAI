"""ChatDock ReAct 循环(ADR-0031 P0):对话式驱动 + 工具调用 + 决策链日志。

双模式单内核:Agent 模式与一键生成共享同一图/闸门/落库;P0 工具三类——
查询(只读)、流程状态(stop_run)、指令通道(record_directive)。生成型
节点工具与对话内确认卡在 P1(NodeRunner)接入。手写循环,不用框架
(ADR-0030 精神);每步落 logs/chat/{story_id}.jsonl 供 debug。
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Callable, Iterator

from app.core.config import AgentRole, get_settings
from app.core.llm.base import ChatMessage
from app.core.llm.facade import LLMFacade
from app.infrastructure.chat_store import ChatStore, ChatTraceLog
from app.infrastructure.queries import ObservabilityQueries, StoryQueries

# 工具观察回填 LLM 的截断(落盘 JSONL 仍是全文,两者独立)
_OBS_CLIP = 3000

_SYSTEM_TEMPLATE = """你是网文《{title}》的创作助理,工作在墨澜写作系统内。

当前进度:{chapter_count} 章已完成定稿,生成状态 {run_status}。{directive_note}

你可以使用工具查询书籍信息、汇报生成状态、记录创作指令、请求停止生成。
行为准则:
- 确认与审核操作(大纲确认、章节人审、定稿)只能由作者在界面上完成,
  你不能代替作者确认,只能建议。
- 启动新一轮生成目前请引导作者点击「开始生成」按钮;你无法代为启动。
- record_directive 记录的指令会在下一次生成构建上下文时生效。
- 回答基于工具查询结果,不要编造书中不存在的情节或设定。"""


def _clip(text: str, limit: int = _OBS_CLIP) -> str:
    return text if len(text) <= limit else text[:limit] + f"…(截断,共 {len(text)} 字)"


class ChatToolbox:
    """P0 工具箱:查询 / 流程状态 / 指令通道。执行异常作为观察回填。"""

    def __init__(self, deps, conn, story_id: str) -> None:
        self._deps = deps
        self._story_id = story_id
        self._stories = StoryQueries(conn)
        self._obs = ObservabilityQueries(conn)

    # ---- 工具实现(参数经关键字注入,返回观察文本) ----

    def query_book_detail(self) -> str:
        d = self._stories.detail(self._story_id)
        story = d["story"]
        return _clip(json.dumps({
            "title": story.get("title"), "genre": story.get("genre"),
            "status": story.get("status"),
            "target_chapters": story.get("target_chapters"),
            "chapters": [
                {k: c.get(k) for k in ("chapter_no", "title", "status", "clen")}
                for c in d["chapters"]],
            "characters": [
                {"name": c["name"], "profile": _clip(c.get("profile") or "", 300)}
                for c in d["characters"]],
            "plot_threads": d["plot_threads"],
            "outline_chars": len(d["outline"] or ""),
        }, ensure_ascii=False))

    def query_chapter(self, chapter_no: int) -> str:
        row = self._stories.active_chapter(self._story_id, int(chapter_no))
        if row is None:
            return f"第 {chapter_no} 章不存在或未定稿"
        r = dict(row)
        r["content"] = _clip(r.get("content") or "")
        return json.dumps(r, ensure_ascii=False)

    def query_codex(self) -> str:
        branch = self._stories.main_branch(self._story_id)
        codex = self._stories.codex(self._story_id, branch)
        codex["facts"] = codex["facts"][:80]
        codex["outline"] = _clip(codex.get("outline") or "", 1500)
        for c in codex["characters"]:
            c["profile"] = _clip(c.get("profile") or "", 300)
        return _clip(json.dumps(codex, ensure_ascii=False))

    def query_usage(self) -> str:
        rows = self._obs.usage_by_agent(self._story_id)
        return json.dumps(rows, ensure_ascii=False)

    def query_run_status(self) -> str:
        state = self._deps.get_run_state(self._story_id) or {"status": "idle"}
        pending = self._deps.peek_pending_directives(self._story_id)
        return json.dumps({
            "run_state": {k: state.get(k) for k in
                          ("status", "run_id", "interrupt_type", "updated_at")},
            "pending_directives": [p["content"] for p in pending],
        }, ensure_ascii=False)

    def record_directive(self, content: str) -> str:
        did = self._deps.record_directive(self._story_id, content)
        return f"指令已记录(id={did}),下一次生成构建上下文时生效"

    def stop_run(self) -> str:
        self._deps.request_stop(self._story_id)
        return "停止请求已发出:若生成正在运行,当前步骤收尾后停止(断点保留,可续跑)"

    # ---- 注册表 ----

    def registry(self) -> list[dict]:
        """(name, description, parameters, handler) 列表。"""
        return [
            ("query_book_detail", "查询书籍概览:标题/类型/章节列表/角色/伏笔台账摘要", {}, self.query_book_detail),
            ("query_chapter", "查询单章定稿内容", {"chapter_no": {"type": "integer", "description": "章节号"}}, self.query_chapter),
            ("query_codex", "查询设定集(Codex):角色卡/伏笔/有效事实/实体图/大纲节选", {}, self.query_codex),
            ("query_usage", "查询本书 LLM 用量(按 agent 聚合)", {}, self.query_usage),
            ("query_run_status", "查询生成状态与待生效指令", {}, self.query_run_status),
            ("record_directive", "记录作者的创作指令,下一次生成时生效", {"content": {"type": "string", "description": "指令内容"}}, self.record_directive),
            ("stop_run", "请求停止当前生成(协作式停止,断点保留)", {}, self.stop_run),
        ]

    def schemas(self) -> list[dict]:
        return [{"type": "function", "function": {"name": n, "description": d, "parameters": {"type": "object", "properties": p, "required": list(p)}}}
                for n, d, p, _ in self.registry()]

    def execute(self, name: str, arguments_json: str) -> str:
        """执行工具;异常/未知工具/参数错误一律作为观察文本回填。"""
        handler = next((h for n, _, _, h in self.registry() if n == name), None)
        if handler is None:
            return f"未知工具:{name}"
        try:
            args = json.loads(arguments_json or "{}")
        except json.JSONDecodeError as e:
            return f"工具参数不是合法 JSON:{e}"
        try:
            return str(handler(**args))
        except TypeError as e:
            return f"工具参数不匹配:{e}"
        except Exception as e:  # noqa: BLE001 — 工具失败回填,循环继续
            return f"工具执行失败:{e}"


class ChatService:
    """单轮 ReAct:用户消息 → (思考 → 工具 → 观察)* → 最终回复。

    run_turn 是 SSE 事件生成器,事件 kind:step / tool_call / tool_result /
    reply / error。每一步写 ChatTraceLog(JSONL,全文)与 chat_messages 表。
    """

    def __init__(self, deps, llm: LLMFacade, log_dir, stories_conn=None) -> None:
        self._deps = deps
        self._llm = llm
        self._trace = ChatTraceLog(log_dir)
        self._max_steps = get_settings().chat_max_steps
        # 测试可注入独立 conn;缺省用引擎连接
        self._conn = stories_conn if stories_conn is not None else deps.conn

    # ---- 单轮入口 ----

    def run_turn(self, story_id: str, user_id: str, text: str) -> Iterator[dict]:
        turn_id = uuid.uuid4().hex[:12]
        started = time.perf_counter()
        store = ChatStore(self._conn)
        toolbox = ChatToolbox(self._deps, self._conn, story_id)

        self._trace.log(story_id, {
            "event": "user_message", "turn_id": turn_id, "user_id": user_id,
            "content": text})

        # 历史先读后写(不含本轮消息)
        messages = [self._system_prompt(story_id)]
        messages += store.history_messages(story_id)
        messages.append(ChatMessage(role="user", content=text))
        store.append(story_id, user_id, "user", text)

        step = 0
        schemas = toolbox.schemas()
        try:
            while step < self._max_steps:
                step += 1
                t0 = time.perf_counter()
                resp = self._llm.chat(AgentRole.CHAT, messages, stage="chat",
                                      story_id=story_id, tools=schemas)
                self._trace.log(story_id, {
                    "event": "step", "turn_id": turn_id, "step": step,
                    "content": resp.content,
                    "tool_calls": resp.tool_calls,
                    "tokens_in": resp.tokens_in, "tokens_out": resp.tokens_out,
                    "duration_ms": int((time.perf_counter() - t0) * 1000)})
                if not resp.tool_calls:
                    yield {"kind": "step", "data": {"content": resp.content}}
                    store.append(story_id, user_id, "assistant", resp.content)
                    self._trace.log(story_id, {
                        "event": "final_reply", "turn_id": turn_id,
                        "steps": step, "tokens_in": resp.tokens_in,
                        "tokens_out": resp.tokens_out,
                        "duration_ms": int((time.perf_counter() - started) * 1000)})
                    yield {"kind": "reply", "data": {"content": resp.content}}
                    return

                # assistant 请求工具:落库(含 tool_calls)+ 循环执行
                messages.append(ChatMessage(role="assistant", content=resp.content,
                                            tool_calls=resp.tool_calls))
                store.append(story_id, user_id, "assistant", resp.content,
                             meta={"tool_calls": resp.tool_calls})
                yield {"kind": "step", "data": {"content": resp.content}}
                for tc in resp.tool_calls:
                    yield {"kind": "tool_call", "data": {
                        "id": tc["id"], "name": tc["name"]}}
                    self._trace.log(story_id, {
                        "event": "tool_call", "turn_id": turn_id, "step": step,
                        "name": tc["name"], "arguments": tc.get("arguments", "")})
                    t1 = time.perf_counter()
                    observation = toolbox.execute(tc["name"], tc.get("arguments", ""))
                    self._trace.log(story_id, {
                        "event": "tool_result", "turn_id": turn_id, "step": step,
                        "name": tc["name"], "observation": observation,
                        "duration_ms": int((time.perf_counter() - t1) * 1000)})
                    messages.append(ChatMessage(
                        role="tool", content=_clip(observation),
                        tool_call_id=tc["id"], name=tc["name"]))
                    store.append(story_id, user_id, "tool", observation,
                                 meta={"tool_call_id": tc["id"], "name": tc["name"]})
                    yield {"kind": "tool_result", "data": {
                        "id": tc["id"], "name": tc["name"],
                        "observation": _clip(observation, 600)}}

            # 步数上限:不带 tools 再要一次总结,保证有最终回复
            messages.append(ChatMessage(
                role="user",
                content="(系统提示:单轮工具调用已达上限,请直接根据已获信息作答,"
                        "不要再调用工具)"))
            resp = self._llm.chat(AgentRole.CHAT, messages, stage="chat",
                                  story_id=story_id)
            store.append(story_id, user_id, "assistant", resp.content)
            self._trace.log(story_id, {
                "event": "final_reply", "turn_id": turn_id, "steps": step,
                "max_steps_reached": True,
                "duration_ms": int((time.perf_counter() - started) * 1000)})
            yield {"kind": "reply", "data": {"content": resp.content}}
        except Exception as e:  # noqa: BLE001 — SSE error 事件 + 日志
            self._trace.log(story_id, {
                "event": "error", "turn_id": turn_id, "step": step,
                "error": str(e)}, )
            yield {"kind": "error", "data": {"message": f"对话处理失败:{e}"}}

    # ---- 上下文 ----

    def _system_prompt(self, story_id: str) -> ChatMessage:
        q = StoryQueries(self._conn)
        try:
            d = q.detail(story_id)
            title = d["story"].get("title") or "未命名"
            chapter_count = len(d["chapters"])
            outline_note = "已有定稿大纲。" if d["outline"] else "尚无定稿大纲。"
        except LookupError:
            title, chapter_count, outline_note = "未命名", 0, ""
        state = self._deps.get_run_state(story_id) or {"status": "idle"}
        pending = self._deps.peek_pending_directives(story_id)
        directive_note = (
            f"待生效指令 {len(pending)} 条。" if pending else "")
        return ChatMessage(role="system", content=_SYSTEM_TEMPLATE.format(
            title=title, chapter_count=chapter_count,
            run_status=state.get("status", "idle"),
            directive_note=directive_note) + outline_note)
