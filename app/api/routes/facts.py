"""事实抽检队列端点(E3)。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import engine, locked
from app.auth import AuthUser, get_current_user, story_role, visible_story_ids
from app.infrastructure.queries import ReviewQueues

router = APIRouter()


class FactReview(BaseModel):
    approve: bool


@router.get("/facts/pending")
@locked
def pending_facts(user: AuthUser = Depends(get_current_user)):
    """待审事实队列:只返回当前用户可见 story 的条目(ADR-0022 租户过滤)。"""
    deps, _ = engine()
    ids = visible_story_ids(deps.conn, user)
    if not ids:
        return []
    return ReviewQueues(deps.conn).pending_facts(ids)


@router.post("/facts/{fact_id}/review")
@locked
def review_fact(fact_id: str, req: FactReview,
                user: AuthUser = Depends(get_current_user)):
    """审核裁决:校验该 fact 所属 story 在当前用户可见集合内(防跨租户审核)。"""
    deps, _ = engine()
    q = ReviewQueues(deps.conn)
    story_id = q.fact_story_id(fact_id)
    if story_id is None or story_role(deps.conn, story_id, user) is None:
        raise HTTPException(404, "pending fact not found")
    new_status = "confirmed" if req.approve else "rejected"
    if q.decide_fact(fact_id, new_status) == 0:
        raise HTTPException(404, "pending fact not found")
    return {"fact_id": fact_id, "status": new_status}
