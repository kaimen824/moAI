"""usage_log 落库 sink:把 core/llm 上抛的 UsageRecord 写入 SQLite。"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone

from app.core.llm.base import UsageRecord


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def make_usage_sink(conn: sqlite3.Connection):
    """构造写入 usage_log 的 sink;返回闭包供 LLMFacade.set_usage_sink 注入。"""

    def sink(record: UsageRecord) -> None:
        conn.execute(
            "INSERT INTO usage_log "
            "(id, story_id, agent, model, tokens_in, tokens_out, latency_ms, trace_id, stage, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                uuid.uuid4().hex,
                record.story_id or None,
                record.agent,
                record.model,
                record.tokens_in,
                record.tokens_out,
                record.latency_ms,
                record.trace_id,
                record.stage,
                _now(),
            ),
        )
        conn.commit()

    return sink
