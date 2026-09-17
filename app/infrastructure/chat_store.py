"""chat_messages 存取 + ReAct 决策链 JSONL 日志(ADR-0031 ChatDock P0)。

ChatStore:对话历史(单 story 单会话);tool_calls / tool_call_id 等
结构化字段入 meta_json,content 只存文本;还原为 ChatMessage 序列时
完整恢复工具链(下一轮 ReAct 可见上一轮调用过程)。

ChatTraceLog:每一步落 logs/chat/{story_id}.jsonl,落盘全量不截断
(debug 用);LLM 上下文回填才做摘要截断,两者独立。写失败不阻断
主流程(与 llm_failures 同策略)。与 agent_traces 分工:traces 表管
LLM 调用快照(可查询、关联 run_id),JSONL 管 agent 决策链时序——
打开文件即读完一轮对话的完整推理过程。
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.core.llm.base import ChatMessage

logger = logging.getLogger("novel.agent")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ChatStore:
    """chat_messages 表读写。"""

    def __init__(self, conn) -> None:
        self.conn = conn

    def append(self, story_id: str, user_id: str, role: str,
               content: str, meta: dict | None = None) -> None:
        self.conn.execute(
            "INSERT INTO chat_messages"
            " (id, story_id, user_id, role, content, meta_json, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (uuid.uuid4().hex, story_id, user_id, role, content,
             json.dumps(meta, ensure_ascii=False) if meta else None, _now()),
        )
        self.conn.commit()

    def history(self, story_id: str, limit: int = 200) -> list[dict]:
        """最近 limit 条,时间正序。同一秒内多条靠 rowid 保序。"""
        rows = self.conn.execute(
            "SELECT rowid, role, content, meta_json FROM ("
            "  SELECT rowid, role, content, meta_json FROM chat_messages"
            "  WHERE story_id=? ORDER BY rowid DESC LIMIT ?"
            ") ORDER BY rowid", (story_id, limit)).fetchall()
        out = []
        for r in rows:
            meta = json.loads(r["meta_json"]) if r["meta_json"] else {}
            out.append({"role": r["role"], "content": r["content"], "meta": meta})
        return out

    def history_messages(self, story_id: str, limit: int = 200) -> list[ChatMessage]:
        """还原为 ChatMessage 序列(含 assistant.tool_calls / tool 回执)。"""
        msgs: list[ChatMessage] = []
        for h in self.history(story_id, limit):
            meta = h["meta"]
            if h["role"] == "assistant" and meta.get("tool_calls"):
                msgs.append(ChatMessage(role="assistant", content=h["content"],
                                        tool_calls=meta["tool_calls"]))
            elif h["role"] == "tool":
                msgs.append(ChatMessage(role="tool", content=h["content"],
                                        tool_call_id=meta.get("tool_call_id", ""),
                                        name=meta.get("name", "")))
            else:
                msgs.append(ChatMessage(role=h["role"], content=h["content"]))
        return msgs


class ChatTraceLog:
    """ReAct 决策链 JSONL:每事件一行,按 story 分文件,只追加。"""

    def __init__(self, base_dir: Path) -> None:
        self._base = Path(base_dir)
        self._lock = threading.Lock()

    def log(self, story_id: str, event: dict) -> None:
        try:
            self._base.mkdir(parents=True, exist_ok=True)
            event = {"ts": _now(), **event}
            line = json.dumps(event, ensure_ascii=False, default=str)
            with self._lock:
                with open(self._base / f"{story_id}.jsonl", "a",
                          encoding="utf-8") as f:
                    f.write(line + "\n")
        except Exception:  # noqa: BLE001 — debug 日志失败不阻断主流程
            logger.warning("chat trace log write failed story=%s", story_id,
                           exc_info=True)
