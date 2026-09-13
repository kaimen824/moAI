"""checkpoint 重放数据层(只读生产库,ADR:checkpoint 重放离线路线)。

重建模型(自 langgraph-checkpoint sqlite v4 实测):
  - checkpoints 按 rowid 即执行时序;metadata 是纯 JSON,checkpoint 载荷是 msgpack;
  - writes 表挂在父 checkpoint 上 = 该 super-step 各 task 的产出(待写入下一 checkpoint):
      branch:to:X  → 实际走过的边(路由决策)
      __interrupt__ → 节点内 interrupt() 抛出(HITL 暂停)
      __resume__    → Command(resume=...) 的用户输入(task_id 全 0)
      __error__     → 节点异常
  - P.updated_channels 里的 branch:to:* = P 之后实际执行的节点集(fan-out 时多个)
  - 线程最后一个 checkpoint 若挂有未消化的 __interrupt__,即"当前停住的中断点"
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

serde = JsonPlusSerializer()
SYSTEM_CHANNELS = ("__interrupt__", "__resume__", "__error__")


def _branch_target(channel: str) -> str | None:
    if channel.startswith("branch:to:"):
        return channel[len("branch:to:"):]
    return None


def _trunc(value, limit=400, depth=0):
    """值清洗:截断长文本、降深、框架对象取 .value,保证可 JSON。"""
    try:
        if depth > 6:
            return str(value)[:limit]
        if isinstance(value, str):
            return value[:limit] + f"…(截断,共 {len(value)} 字)" if len(value) > limit else value
        if isinstance(value, dict):
            return {str(k): _trunc(v, limit, depth + 1) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_trunc(v, limit, depth + 1) for v in value[:20]]
        if value is None or isinstance(value, (bool, int, float)):
            return value
        inner = getattr(value, "value", None) if hasattr(value, "value") else None
        return _trunc(inner if inner is not None else repr(value), limit, depth + 1)
    except RecursionError:
        return "<递归过深>"


def _decode_value(row_value, row_type):
    try:
        v = serde.loads_typed((row_type, row_value))
        return _trunc(v)
    except Exception as exc:  # noqa: BLE001 —— 单值解码失败不拖垮整个重放
        return f"<decode 失败: {exc}>"


class ReplayIndex:
    """按 (db mtime) 缓存整库索引;state 详情按需解码。"""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._cache_mtime: float | None = None
        self.threads: list[dict] = []
        self.runs: dict[str, dict] = {}

    def refresh_if_stale(self) -> bool:
        mtime = self.db_path.stat().st_mtime
        if mtime == self._cache_mtime:
            return False
        self._cache_mtime = mtime
        self._rebuild()
        return True

    # ---- 重建 ----

    def _connect(self):
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def _rebuild(self):
        conn = self._connect()
        try:
            writes_by_ckpt: dict[str, list[sqlite3.Row]] = defaultdict(list)
            for w in conn.execute(
                    "SELECT thread_id, checkpoint_id, task_id, channel, type, value"
                    " FROM writes ORDER BY rowid"):
                writes_by_ckpt[(w["thread_id"], w["checkpoint_id"])].append(w)

            chapters: dict[str, int] = {
                r["story_id"]: r["n"] for r in conn.execute(
                    "SELECT story_id, COUNT(*) n FROM chapters"
                    " WHERE status='active' GROUP BY story_id")}

            by_thread: dict[str, list[sqlite3.Row]] = defaultdict(list)
            for c in conn.execute(
                    "SELECT rowid, thread_id, checkpoint_id, parent_checkpoint_id,"
                    " type, checkpoint, metadata FROM checkpoints ORDER BY rowid"):
                by_thread[c["thread_id"]].append(c)

            self.runs = {}
            for tid, rows in by_thread.items():
                steps, last_ts, last_step_no, stopped_at = self._thread_steps(
                    rows, writes_by_ckpt)
                self.runs[tid] = {
                    "thread_id": tid, "steps": steps,
                    "stopped_at": stopped_at,     # 当前停住的中断点(可能为 null)
                }
                self.threads.append({
                    "thread_id": tid,
                    "checkpoints": len(rows),
                    "chapters": chapters.get(tid, 0),
                    "steps": len(steps),
                    "last_ts": last_ts,
                    "last_step": last_step_no,
                    "stopped_at": stopped_at,
                })
            self.threads.sort(key=lambda t: t["last_ts"] or "", reverse=True)
        finally:
            conn.close()

    def _thread_steps(self, rows, writes_by_ckpt):
        steps, last_ts, last_step_no = [], None, None
        for i, cur in enumerate(rows):
            meta = json.loads(cur["metadata"])
            cp = serde.loads_typed((cur["type"], cur["checkpoint"]))
            last_ts = cp.get("ts", last_ts)
            last_step_no = meta.get("step", last_step_no)

            parent_id = cur["parent_checkpoint_id"]
            pw = writes_by_ckpt.get((cur["thread_id"], parent_id), [])
            parent_updated = self._parent_updated(rows, i, parent_id)

            executed = {t for t in parent_updated if t} if parent_id else set()
            edges, updated, interrupt, error = set(), [], None, None
            for w in pw:
                ch = w["channel"]
                tgt = _branch_target(ch)
                if tgt:
                    edges.add(tgt)
                    continue
                if ch == "__interrupt__":
                    interrupt = {
                        "node": next(iter(executed)) if executed else None,
                        "payload": _decode_value(w["value"], w["type"]),
                    }
                elif ch == "__error__":
                    error = _decode_value(w["value"], w["type"])
                elif ch == "__resume__":
                    pass          # resume 注入单独标
                else:
                    updated.append(ch)

            resumed = any(w["channel"] == "__resume__" for w in pw)
            if interrupt is not None:
                interrupt["resumed"] = resumed
            if i == 0:
                executed, edges = set(), set()   # 根 checkpoint 仅携带起始状态
            steps.append({
                "i": i,
                "checkpoint_id": cur["checkpoint_id"],
                "ts": cp.get("ts"),
                "step": meta.get("step"),
                "source": meta.get("source"),
                "executed": sorted(executed),
                "edges": sorted(edges),
                "updated": updated,
                "interrupt": interrupt,
                "error": error,
            })

        stopped_at = self._final_pending(rows, writes_by_ckpt)
        return steps, last_ts, last_step_no, stopped_at

    def _parent_updated(self, rows, i, parent_id):
        """父 checkpoint 的 branch:to:* 目标 = 本步执行的节点集。"""
        if not parent_id:
            return []
        for j in range(i - 1, -1, -1):
            if rows[j]["checkpoint_id"] == parent_id:
                cp = serde.loads_typed((rows[j]["type"], rows[j]["checkpoint"]))
                return [t for t in map(_branch_target, cp.get("updated_channels") or []) if t]
        return []

    def _final_pending(self, rows, writes_by_ckpt):
        """线程末尾挂着的未消化 writes:当前停在哪(中断点)。"""
        last = rows[-1]
        pw = writes_by_ckpt.get((last["thread_id"], last["checkpoint_id"]), [])
        interrupt = next((w for w in pw if w["channel"] == "__interrupt__"), None)
        if interrupt is None:
            return None
        cp = serde.loads_typed((last["type"], last["checkpoint"]))
        node = next((t for t in map(_branch_target, cp.get("updated_channels") or []) if t), None)
        return {
            "node": node,
            "checkpoint_id": last["checkpoint_id"],
            "ts": cp.get("ts"),
            "payload": _decode_value(interrupt["value"], interrupt["type"]),
        }

    # ---- 按需:某 checkpoint 的完整 state(与父的 diff) ----

    def state_detail(self, thread_id: str, checkpoint_id: str) -> dict | None:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT checkpoint_id, parent_checkpoint_id, type, checkpoint"
                " FROM checkpoints WHERE thread_id=? ORDER BY rowid", (thread_id,)).fetchall()
            cur = next((r for r in rows if r["checkpoint_id"] == checkpoint_id), None)
            if cur is None:
                return None
            cur_cp = serde.loads_typed((cur["type"], cur["checkpoint"]))
            parent = next((r for r in rows
                           if r["checkpoint_id"] == cur["parent_checkpoint_id"]), None)
            parent_vals = (serde.loads_typed((parent["type"], parent["checkpoint"]))
                           .get("channel_values", {})) if parent else {}
            cur_vals = cur_cp.get("channel_values", {})

            diff = {}
            for k in sorted(set(cur_vals) - {"__start__"} | set(parent_vals)):
                before, after = parent_vals.get(k), cur_vals.get(k)
                if k not in parent_vals:
                    diff[k] = {"op": "added", "after": _trunc(after)}
                elif k not in cur_vals:
                    diff[k] = {"op": "removed", "before": _trunc(before)}
                elif before != after:
                    diff[k] = {"op": "changed", "before": _trunc(before),
                               "after": _trunc(after)}
            return {
                "checkpoint_id": checkpoint_id,
                "ts": cur_cp.get("ts"),
                "values": {k: _trunc(v) for k, v in cur_vals.items() if k != "__start__"},
                "diff": diff,
            }
        finally:
            conn.close()
