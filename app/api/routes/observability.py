"""可观测性端点:用量聚合、全节点调用回溯、评审记录(ADR-0028)。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import engine, locked
from app.auth import AuthUser, get_current_user, require_story
from app.infrastructure.queries import ObservabilityQueries

router = APIRouter()


@router.get("/stories/{story_id}/usage")
@locked
def usage(story_id: str, user: AuthUser = Depends(get_current_user)):
    deps, _ = engine()
    require_story(deps.conn, story_id, user)
    return ObservabilityQueries(deps.conn).usage_by_agent(story_id)


@router.get("/stories/{story_id}/traces")
@locked
def traces(story_id: str, limit: int = 100,
           user: AuthUser = Depends(get_current_user)):
    """全节点调用回溯:LLM 输入/输出快照(会话恢复/事后诊断)。"""
    deps, _ = engine()
    require_story(deps.conn, story_id, user)
    return ObservabilityQueries(deps.conn).recent_traces(story_id, min(limit, 500))


@router.get("/stories/{story_id}/reviews")
@locked
def reviews(story_id: str, user: AuthUser = Depends(get_current_user)):
    deps, _ = engine()
    require_story(deps.conn, story_id, user)
    return ObservabilityQueries(deps.conn).reviews(story_id)
