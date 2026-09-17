"""可观测性端点:用量聚合、全节点调用回溯、评审记录(ADR-0028)。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import engine, locked
from app.auth import AuthUser, get_current_user, require_story

router = APIRouter()


@router.get("/stories/{story_id}/usage")
@locked
def usage(story_id: str, user: AuthUser = Depends(get_current_user)):
    deps, _ = engine()
    require_story(deps.conn, story_id, user)
    by_agent = deps.conn.execute(
        "SELECT agent, model, COUNT(*) calls, SUM(tokens_in) tin, SUM(tokens_out) tout,"
        " SUM(cached_tokens) cached, SUM(latency_ms) latency,"
        " CAST(ROUND(100.0 * SUM(cached_tokens) / NULLIF(SUM(tokens_in), 0)) AS INTEGER)"
        "   AS cache_hit_pct"
        " FROM usage_log WHERE story_id=? GROUP BY agent, model",
        (story_id,)).fetchall()
    return [dict(r) for r in by_agent]


@router.get("/stories/{story_id}/traces")
@locked
def traces(story_id: str, limit: int = 100,
           user: AuthUser = Depends(get_current_user)):
    """全节点调用回溯:LLM 输入/输出快照(会话恢复/事后诊断)。"""
    deps, _ = engine()
    require_story(deps.conn, story_id, user)
    rows = deps.conn.execute(
        "SELECT agent, model, stage, input_text, output_text,"
        " tokens_in, tokens_out, latency_ms, created_at"
        " FROM agent_traces WHERE story_id=? ORDER BY created_at DESC LIMIT ?",
        (story_id, min(limit, 500))).fetchall()
    return [dict(r) for r in rows]


@router.get("/stories/{story_id}/reviews")
@locked
def reviews(story_id: str, user: AuthUser = Depends(get_current_user)):
    deps, _ = engine()
    require_story(deps.conn, story_id, user)
    rows = deps.conn.execute(
        "SELECT * FROM review_results WHERE story_id=? ORDER BY created_at",
        (story_id,)).fetchall()
    return [dict(r) for r in rows]
