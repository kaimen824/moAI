"""实体合并提案端点(ADR-0015:LLM uncertain 才入队)。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import engine, locked
from app.auth import AuthUser, get_current_user, story_role, visible_story_ids
from app.infrastructure.queries import ReviewQueues

router = APIRouter()


class EntityProposalReview(BaseModel):
    action: str                   # merge | new | ignore


@router.get("/entities/pending")
@locked
def pending_entity_proposals(user: AuthUser = Depends(get_current_user)):
    """AI 拿不准的实体合并提案(先写后合并:候选已独立落库,裁决后归一)。
    只返回当前用户可见 story 的条目。"""
    deps, _ = engine()
    ids = visible_story_ids(deps.conn, user)
    if not ids:
        return []
    return ReviewQueues(deps.conn).pending_proposals(ids)


@router.post("/entities/{proposal_id}/review")
@locked
def review_entity_proposal(proposal_id: str, req: EntityProposalReview,
                           user: AuthUser = Depends(get_current_user)):
    """人工裁决:merge=执行归一(链接重定向/别名吸收/候选置 merged);
    new=独立实体;ignore=维持现状且不再重复提案。裁决持久生效。"""
    deps, _ = engine()
    if req.action not in ("merge", "new", "ignore"):
        raise HTTPException(400, "action must be merge|new|ignore")
    q = ReviewQueues(deps.conn)
    story_id = q.proposal_story_id(proposal_id)
    if story_id is None or story_role(deps.conn, story_id, user) is None:
        raise HTTPException(404, "pending proposal not found")
    try:
        proposal = deps.resolve_entity_proposal(proposal_id, req.action)
    except LookupError:
        raise HTTPException(404, "pending proposal not found")
    status = "merged" if req.action == "merge" else (
        "new" if req.action == "new" else "ignored")
    return {"proposal_id": proposal_id, "status": status,
            "candidate": proposal.get("candidate_name")}
