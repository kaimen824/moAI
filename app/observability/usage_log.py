"""usage_log 落库 sink:把 core/llm 上抛的 UsageRecord 写入 SQLite。"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.core.llm.base import UsageRecord


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def make_usage_sink(conn: sqlite3.Connection, lock: Optional[threading.RLock] = None):
    """构造写入 usage_log 的 sink;返回闭包供 LLMFacade.set_usage_sink 注入。

    lock:引擎级锁——流式调用的埋点在 SSE 消费线程的 generator finally 中执行,
    必须与图线程/端点线程的 DB 访问共用同一把锁,否则 sqlite3 跨线程并发报错。
    """

    def sink(record: UsageRecord) -> None:
        def _write() -> None:
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

        if lock is not None:
            with lock:
                _write()
        else:
            _write()

    return sink
