"""FastAPI 服务层(ADR-0009):SSE 流式、中断点交互、抽检队列。

运行:uvicorn app.main:app --reload
图经 thread_id(= story_id)驱动;中断事件经 SSE 下发,resume 由客户端回传。
"""

from __future__ import annotations

import json
import queue
import threading
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from pydantic import BaseModel

from app.core.config import get_settings
from app.graph.build import build_graph
from app.graph.runtime import Deps, build_engine

app = FastAPI(title="novel-agent", version="0.1.0")


def locked(fn):
    """同步端点统一套引擎锁(DB 访问与图执行线程互斥)。"""
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        deps, _ = engine()
        with deps.run_lock:
            return fn(*args, **kwargs)
    return wrapper

_engine: tuple[Deps, Any] | None = None
_graph: Any = None
_active: set[str] = set()          # 运行中的 thread(= story_id)


def engine() -> tuple[Deps, Any]:
    global _engine, _graph
    if _engine is None:
        deps, conn = build_engine(get_settings().db_path)
        _engine = (deps, conn)
        _graph = build_graph(deps, checkpointer=deps.checkpointer)
    return _engine


def install_engine(deps: Deps, conn, graph) -> None:
    """测试注入:替换引擎单例(生产不调用)。"""
    global _engine, _graph
    _engine, _graph = (deps, conn), graph


# ================= Schemas =================

class CreateStory(BaseModel):
    title: str
    premise: str = ""


class GenerateRequest(BaseModel):
    target_chapters: int = 1
    initial_input: str = ""
    tags: list[str] = []              # 题材标签(Dify 式 token):独立单元,可多选
    branch_id: str = ""


class ResumeRequest(BaseModel):
    action: str                      # confirm | revise
    feedback: str = ""
    threads: list[dict] = []         # 人工确认的伏笔变更


class FactReview(BaseModel):
    approve: bool


# ================= SSE 驱动 =================

_PAYLOAD_KEYS = (
    "world_settings", "character_drafts", "master_outline", "stage_outline",
    "chapter_brief", "outline_review", "quality_review", "merged_verdict",
    "fact_changes", "character_changes", "chapter_summary", "stage_summary",
    "thread_changes", "context_stats", "user_directives",
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


def _sse_run(graph_input: Any, thread_id: str) -> StreamingResponse:
    """worker 线程跑图,事件按 thread 广播(多订阅+历史);本响应为主订阅。"""
    deps, _ = engine()
    from app.graph.runtime import StopRequested
    deps.clear_events(thread_id)        # 新一轮生成:该 thread 历史从零
    deps.clear_stop(thread_id)          # 新一轮运行清除上一轮的中断请求
    q = deps.subscribe(thread_id)
    deps._current_thread = thread_id    # 节点内 emit(token)归属本 thread
    cfg = {"configurable": {"thread_id": thread_id},
           # 硬兜底:任何未预见的图循环超步数即抛错(单章全流程约 20 步,
           # 200 步 ≈ 8-10 章 + 评审循环余量;业务级循环各有更早的自动转人工)
           "recursion_limit": 200}
    _active.add(thread_id)

    def worker() -> None:
        try:
            # 不全程持锁:LangGraph fan-out 节点在独立线程,DB 访问
            # 已在 repo/sink/checkpointer 层与引擎锁互斥
            for chunk in _graph.stream(graph_input, cfg, stream_mode="updates"):
                for node, update in chunk.items():
                    if node == "__interrupt__":
                        intr = update[0]
                        deps.emit("interrupt", intr.value, thread_id)
                        return
                    deps.emit("stage", {"node": node, "payload": _extract_payload(update)}, thread_id)
            deps.emit("done", {"ok": True}, thread_id)
        except StopRequested:
            # 用户中断:断点已由 checkpointer 保留,重新生成即续跑
            deps.emit("stopped", {"message": "已按用户请求中断;重新点「生成」可从断点续跑"}, thread_id)
        except Exception as exc:  # noqa: BLE001
            deps.emit("error", {"message": str(exc)}, thread_id)
        finally:
            _active.discard(thread_id)
            deps.clear_stop(thread_id)

    threading.Thread(target=worker, daemon=True).start()
    return _sse_response(deps, q)


def _sse_response(deps, q) -> StreamingResponse:
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


@app.get("/stories/{story_id}/run-state")
@locked
def run_state(story_id: str):
    """会话恢复:前端挂载时查询 —— 运行中 / 等待中断(含中断卡数据)/ 空闲 + 事件历史。"""
    deps, _ = engine()
    events = [{"kind": k, "data": d} for k, d in deps.snapshot(story_id)]
    last_interrupt = None
    for k, d in reversed(deps.snapshot(story_id)):
        if k == "interrupt":
            last_interrupt = d
            break
    status = "running" if story_id in _active else (
        "waiting" if last_interrupt else "idle")
    return {"status": status, "interrupt": last_interrupt, "events": events}


@app.post("/stories/{story_id}/attach")
def attach(story_id: str):
    """断线/刷新后重新订阅事件流(只收不发,不驱动图)。"""
    deps, _ = engine()
    return _sse_response(deps, deps.subscribe(story_id))


# ================= 路由 =================

@app.post("/stories")
@locked
def create_story(req: CreateStory):
    deps, _ = engine()
    story_id, branch_id = deps.repo.create_story(req.title, req.premise)
    return {"story_id": story_id, "branch_id": branch_id}


@app.get("/stories")
@locked
def list_stories():
    deps, _ = engine()
    rows = deps.conn.execute(
        "SELECT s.*, (SELECT COUNT(*) FROM chapters c"
        "  WHERE c.story_id = s.id AND c.status='active') AS chapter_count"
        " FROM stories s ORDER BY s.created_at DESC").fetchall()
    return [dict(r) for r in rows]


@app.get("/stories/{story_id}")
@locked
def story_detail(story_id: str):
    deps, _ = engine()
    story = deps.conn.execute("SELECT * FROM stories WHERE id=?", (story_id,)).fetchone()
    if not story:
        raise HTTPException(404, "story not found")
    chapters = deps.conn.execute(
        "SELECT id, chapter_no, version_no, title, status, updated_at,"
        " length(content) AS clen FROM chapters"
        " WHERE story_id=? AND status='active' ORDER BY chapter_no", (story_id,)).fetchall()
    outline = deps.conn.execute(
        "SELECT content FROM outlines WHERE story_id=? AND status='confirmed'"
        " ORDER BY version_no DESC LIMIT 1", (story_id,)).fetchone()
    characters = deps.conn.execute(
        "SELECT id, name, profile FROM characters WHERE story_id=?", (story_id,)).fetchall()
    threads = deps.conn.execute(
        "SELECT * FROM plot_threads WHERE story_id=?", (story_id,)).fetchall()
    return {
        "story": dict(story),
        "outline": outline["content"] if outline else None,
        "chapters": [dict(c) for c in chapters],
        "characters": [dict(c) for c in characters],
        "plot_threads": [dict(t) for t in threads],
    }


@app.post("/stories/{story_id}/generate")
@locked
def generate(story_id: str, req: GenerateRequest):
    deps, _ = engine()
    story = deps.conn.execute("SELECT * FROM stories WHERE id=?", (story_id,)).fetchone()
    if not story:
        raise HTTPException(404, "story not found")
    branch = req.branch_id or story["main_branch_id"]
    tags_part = ("题材标签:" + " ".join("#" + t for t in req.tags) + "\n") if req.tags else ""
    return _sse_run(
        {"story_id": story_id, "branch_id": branch,
         "target_chapters": req.target_chapters,
         "initial_input": tags_part + req.initial_input},
        thread_id=story_id,
    )


@app.post("/stories/{story_id}/resume")
@locked
def resume(story_id: str, req: ResumeRequest):
    return _sse_run(
        Command(resume={"action": req.action, "feedback": req.feedback,
                        "threads": req.threads}),
        thread_id=story_id,
    )


@app.post("/stories/{story_id}/stop")
@locked
def stop(story_id: str):
    """协作式中断:置位停止请求,图在下一个节点边界安全退出(断点可续跑)。"""
    deps, _ = engine()
    deps.request_stop(story_id)
    return {"ok": True, "active": story_id in _active}


@app.get("/stories/{story_id}/chapters/{chapter_no}")
@locked
def get_chapter(story_id: str, chapter_no: int):
    deps, _ = engine()
    row = deps.conn.execute(
        "SELECT * FROM chapters WHERE story_id=? AND chapter_no=? AND status='active'",
        (story_id, chapter_no)).fetchone()
    if not row:
        raise HTTPException(404, "chapter not found")
    return dict(row)


@app.get("/stories/{story_id}/codex")
@locked
def codex(story_id: str):
    """设定集(Codex):角色卡 + 伏笔台账 + 当前有效世界记忆(排除被推翻/拒绝)。

    facts 推翻链与 world 回放同口径:有后续版本指向即失效,任一时点只呈现有效记忆。
    """
    deps, _ = engine()
    story = deps.conn.execute("SELECT main_branch_id FROM stories WHERE id=?",
                              (story_id,)).fetchone()
    if not story:
        raise HTTPException(404, "story not found")
    branch = story["main_branch_id"]
    characters = deps.conn.execute(
        "SELECT id, name, profile FROM characters WHERE story_id=? ORDER BY created_at",
        (story_id,)).fetchall()
    threads = deps.conn.execute(
        "SELECT id, description, planted_chapter, resolved_chapter, status"
        " FROM plot_threads WHERE story_id=? ORDER BY planted_chapter",
        (story_id,)).fetchall()
    facts = deps.conn.execute(
        "SELECT f.type, f.content, f.chapter_established, f.confidence"
        " FROM facts f"
        " WHERE f.story_id=? AND f.branch_id=? AND f.status!='rejected'"
        "   AND NOT EXISTS ("
        "     SELECT 1 FROM facts g"
        "     WHERE g.prev_version_id = f.id AND g.branch_id = f.branch_id)"
        " ORDER BY f.chapter_established DESC, f.rowid DESC LIMIT 300",
        (story_id, branch)).fetchall()
    outline = deps.conn.execute(
        "SELECT content FROM outlines WHERE story_id=? AND status='confirmed'"
        " ORDER BY version_no DESC LIMIT 1", (story_id,)).fetchone()
    return {
        "characters": [dict(r) for r in characters],
        "plot_threads": [dict(r) for r in threads],
        "facts": [dict(r) for r in facts],
        "outline": outline["content"] if outline else None,
    }


# ================= 抽检队列(E3)=================

@app.get("/facts/pending")
@locked
def pending_facts():
    deps, _ = engine()
    rows = deps.conn.execute(
        "SELECT f.*, s.title AS story_title FROM facts f JOIN stories s ON s.id=f.story_id"
        " WHERE f.status='pending_review' ORDER BY f.created_at").fetchall()
    return [dict(r) for r in rows]


@app.post("/facts/{fact_id}/review")
@locked
def review_fact(fact_id: str, req: FactReview):
    deps, _ = engine()
    new_status = "confirmed" if req.approve else "rejected"
    cur = deps.conn.execute(
        "UPDATE facts SET status=? WHERE id=? AND status='pending_review'",
        (new_status, fact_id))
    deps.conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(404, "pending fact not found")
    return {"fact_id": fact_id, "status": new_status}


class DirectiveRequest(BaseModel):
    text: str


@app.post("/stories/{story_id}/directive")
@locked
def post_directive(story_id: str, req: DirectiveRequest):
    """用户指令通道:任意时刻提交,下一次章节生成的上下文中被主控消费。"""
    deps, _ = engine()
    if req.text.strip():
        deps.record_directive(story_id, req.text.strip())
    n = deps.conn.execute(
        "SELECT COUNT(*) c FROM user_directives WHERE story_id=? AND consumed_at IS NULL",
        (story_id,),
    ).fetchone()["c"]
    return {"ok": True, "pending": n}


# ================= 模型配置(ADR-0008:运行时覆盖)=================

@app.get("/config/models")
@locked
def get_models():
    from app.core.config import AgentRole, get_settings
    s = get_settings()
    return {role.value: s.model_for(role) for role in AgentRole}


class ModelOverride(BaseModel):
    role: str
    model: str


@app.post("/config/models")
@locked
def set_model(req: ModelOverride):
    from app.core.config import AgentRole, get_settings
    try:
        role = AgentRole(req.role)
    except ValueError:
        raise HTTPException(400, f"unknown role: {req.role}")
    get_settings().set_model_override(role, req.model)
    return {"role": req.role, "model": req.model}


# ================= 可观测性 =================

@app.get("/stories/{story_id}/usage")
@locked
def usage(story_id: str):
    deps, _ = engine()
    by_agent = deps.conn.execute(
        "SELECT agent, model, COUNT(*) calls, SUM(tokens_in) tin, SUM(tokens_out) tout,"
        " SUM(latency_ms) latency FROM usage_log WHERE story_id=? GROUP BY agent, model",
        (story_id,)).fetchall()
    return [dict(r) for r in by_agent]


@app.get("/stories/{story_id}/traces")
@locked
def traces(story_id: str, limit: int = 100):
    """全节点调用回溯:LLM 输入/输出快照(会话恢复/事后诊断)。"""
    deps, _ = engine()
    rows = deps.conn.execute(
        "SELECT agent, model, stage, input_text, output_text,"
        " tokens_in, tokens_out, latency_ms, created_at"
        " FROM agent_traces WHERE story_id=? ORDER BY created_at DESC LIMIT ?",
        (story_id, min(limit, 500))).fetchall()
    return [dict(r) for r in rows]


@app.get("/stories/{story_id}/reviews")
@locked
def reviews(story_id: str):
    deps, _ = engine()
    rows = deps.conn.execute(
        "SELECT * FROM review_results WHERE story_id=? ORDER BY created_at",
        (story_id,)).fetchall()
    return [dict(r) for r in rows]
