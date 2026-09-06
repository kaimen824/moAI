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
    branch_id: str = ""


class ResumeRequest(BaseModel):
    action: str                      # confirm | revise
    feedback: str = ""
    threads: list[dict] = []         # 人工确认的伏笔变更


class FactReview(BaseModel):
    approve: bool


# ================= SSE 驱动 =================

def _sse_run(graph_input: Any, thread_id: str) -> StreamingResponse:
    """worker 线程跑图,SSE 转发 stage/token/interrupt/done 事件。"""
    deps, _ = engine()
    q: queue.Queue = queue.Queue()
    deps.set_event_queue(q)
    cfg = {"configurable": {"thread_id": thread_id}}

    def worker() -> None:
        try:
            # 不全程持锁:LangGraph fan-out 节点在独立线程,DB 访问
            # 已在 repo/sink/checkpointer 层与引擎锁互斥
            for chunk in _graph.stream(graph_input, cfg, stream_mode="updates"):
                for node, update in chunk.items():
                    if node == "__interrupt__":
                        intr = update[0]
                        q.put(("interrupt", intr.value))
                        deps.set_event_queue(None)
                        return
                    q.put(("stage", {"node": node}))
            q.put(("done", {"ok": True}))
        except Exception as exc:  # noqa: BLE001
            q.put(("error", {"message": str(exc)}))
        finally:
            deps.set_event_queue(None)

    threading.Thread(target=worker, daemon=True).start()

    def gen():
        while True:
            kind, data = q.get(timeout=600)
            yield f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            if kind in ("interrupt", "done", "error"):
                break

    return StreamingResponse(gen(), media_type="text/event-stream")


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
    rows = deps.conn.execute("SELECT * FROM stories ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


@app.get("/stories/{story_id}")
@locked
def story_detail(story_id: str):
    deps, _ = engine()
    story = deps.conn.execute("SELECT * FROM stories WHERE id=?", (story_id,)).fetchone()
    if not story:
        raise HTTPException(404, "story not found")
    chapters = deps.conn.execute(
        "SELECT id, chapter_no, version_no, title, status, updated_at FROM chapters"
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
    return _sse_run(
        {"story_id": story_id, "branch_id": branch,
         "target_chapters": req.target_chapters, "initial_input": req.initial_input},
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


@app.get("/stories/{story_id}/reviews")
@locked
def reviews(story_id: str):
    deps, _ = engine()
    rows = deps.conn.execute(
        "SELECT * FROM review_results WHERE story_id=? ORDER BY created_at",
        (story_id,)).fetchall()
    return [dict(r) for r in rows]
