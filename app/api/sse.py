"""SSE 流式传输(ADR-0009/0030 阶段1):worker 线程跑图,事件按 thread 广播。

互斥(ADR-0023):同一 story 同时只允许一个 active run——generate/resume
双击/并发触发第二个请求 409。运行状态持久化与错误结构化契约见
ADR-0027/0028(原样保留)。
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from typing import Any

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from langgraph.types import Command

from app.api.deps import _active, _active_lock, engine

logger = logging.getLogger("novel.agent")

_PAYLOAD_KEYS = (
    "world_settings", "character_drafts", "master_outline", "stage_outline",
    "chapter_brief", "outline_review", "quality_review", "merged_verdict",
    "fact_changes", "character_changes", "chapter_summary", "stage_summary",
    "thread_changes", "context_stats", "user_directives", "entity_changes",
    "rewrite_exhausted", "stage_end_chapter", "stage_regen_count",
    "chapter_no",            # 前端时间线按章分组的依据
)


def _extract_payload(update: dict | None) -> dict:
    """从节点 state 增量中提取可展示的产出(截断超长文本)。"""
    if not isinstance(update, dict):
        return {}
    out = {}
    for k in _PAYLOAD_KEYS:
        if k in update:
            v = update[k]
            if isinstance(v, str) and len(v) > 4000:
                v = v[:4000] + "…(已截断)"
            out[k] = v
    return out


def sse_run(graph_input: Any, thread_id: str, user_id: str = "",
            *, subscribe: bool = True, fresh: bool = True):
    """worker 线程跑图,事件按 thread 广播(多订阅+历史);本响应为主订阅。

    subscribe=False(ReAct 工具触发续跑,ADR-0031 P1):只起 worker 不建
    订阅——返回 None,事件照常广播给既有订阅者(工作台时间线可见)。
    fresh=False:保留事件历史(revamp 是本轮延续,时间线不清零)。
    """
    deps, _ = engine()
    from app.graph.runtime import StopRequested, run_ctx, user_ctx
    with _active_lock:
        if thread_id in _active:
            raise HTTPException(409, "story run already active")
        _active.add(thread_id)
    if fresh:
        deps.clear_events(thread_id)        # 新一轮生成:该 thread 历史从零
    deps.clear_stop(thread_id)          # 新一轮运行清除上一轮的中断请求
    q = deps.subscribe(thread_id) if subscribe else None
    run_id = uuid.uuid4().hex
    cfg = {"configurable": {"thread_id": thread_id},
           # 硬兜底:任何未预见的图循环超步数即抛错(单章全流程约 20 步,
           # 200 步 ≈ 8-10 章 + 评审循环余量;业务级循环各有更早的自动转人工)
           "recursion_limit": 200}

    def worker() -> None:
        from app.api.deps import _graph   # 函数内 import:读取最新单例绑定
        # 事件归属(ADR-0023):本 run 的所有节点/fan-out/回调线程经
        # ContextVar 读取 (thread_id, run_id),不再依赖全局单值;
        # user_ctx(ADR-0028)供 usage/traces 按触发用户归因
        run_ctx.set((thread_id, run_id))
        user_ctx.set(user_id)
        try:
            # run 状态持久化(ADR-0027,评审 6.4/6.13):启动即落 running,
            # 进程崩溃后由启动收敛/读时兜底复位;interrupt/error 整体清空
            deps.set_run_state(
                thread_id, status="running", run_id=run_id,
                target_chapters=graph_input.get("target_chapters")
                if isinstance(graph_input, dict) else None)
            # 不全程持锁:LangGraph fan-out 节点在独立线程,DB 访问
            # 已在 repo/sink/checkpointer 层与引擎锁互斥
            for chunk in _graph.stream(graph_input, cfg, stream_mode="updates"):
                for node, update in chunk.items():
                    if node == "__interrupt__":
                        intr = update[0]
                        payload = intr.value
                        # revamp 轮(ADR-0031 P1,拍板 b):章节人审卡附冲突
                        # 标注——重写稿对照后续章找矛盾,供作者逐章决定重写
                        if (isinstance(payload, dict)
                                and payload.get("type") == "user_review_chapter"):
                            snap_values = _graph.get_state(cfg).values or {}
                            if snap_values.get("revamp_pending"):
                                from app.graph.noderunner import run_conflict_check
                                payload = {**payload, "conflict_report":
                                           run_conflict_check(deps, snap_values)}
                        deps.emit("interrupt", payload, thread_id)
                        # waiting + 完整 payload 落库:跨重启中断卡可还原(前端不再丢卡)
                        deps.set_run_state(
                            thread_id, status="waiting",
                            interrupt_type=(payload.get("type")
                                            if isinstance(payload, dict) else None),
                            interrupt_payload=json.dumps(
                                payload, ensure_ascii=False, default=str))
                        return
                    deps.emit("stage", {"node": node, "payload": _extract_payload(update)},
                              thread_id)
            deps.emit("done", {"ok": True, "run_id": run_id}, thread_id)
            deps.set_run_state(thread_id, status="idle")
        except StopRequested:
            # 用户中断:断点已由 checkpointer 保留,重新生成即续跑
            deps.emit("stopped",
                      {"message": "已按用户请求中断;重新点「生成」可从断点续跑"}, thread_id)
            deps.set_run_state(thread_id, status="idle")
        except Exception as exc:  # noqa: BLE001
            # 结构化错误(ADR-0028,评审 6.13):服务端 logging.exception 留
            # 全栈诊断;SSE 只发脱敏摘要 + 稳定标识(error_code/run_id/trace_id)
            logger.exception("run failed story=%s run=%s", thread_id, run_id)
            payload = {
                "message": str(exc)[:300],
                "error_code": getattr(exc, "error_code", "internal_error"),
                "run_id": run_id,
            }
            if getattr(exc, "trace_id", None):
                payload["trace_id"] = exc.trace_id
            deps.emit("error", payload, thread_id)
            deps.set_run_state(thread_id, status="idle",
                               error_code=str(getattr(exc, "error_code", "internal_error")),
                               error_stage=str(getattr(exc, "stage", "") or ""))
        finally:
            with _active_lock:
                _active.discard(thread_id)
            deps.clear_stop(thread_id)

    threading.Thread(target=worker, daemon=True).start()
    return sse_response(deps, q) if subscribe else None


def sse_response(deps, q) -> StreamingResponse:
    def gen():
        try:
            while True:
                kind, data = q.get(timeout=3600)
                yield f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                if kind in ("interrupt", "done", "error", "stopped"):
                    break
        finally:
            deps.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream")


# 兼容别名(原模块内私有名;stories 路由与测试经此引用)
_sse_run = sse_run
_sse_response = sse_response


def resume_command(req) -> Command:
    """ResumeRequest -> LangGraph Command(表现层 DTO 与图的转接点)。"""
    return Command(resume={"action": req.action, "feedback": req.feedback,
                           "threads": req.threads})
