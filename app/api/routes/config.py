"""模型配置端点(ADR-0008:运行时覆盖;全局配置,admin 专用)。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import engine, locked
from app.auth import AuthUser, get_admin_user

router = APIRouter()


class ModelOverride(BaseModel):
    role: str
    model: str


@router.get("/config/models")
@locked
def get_models(admin: AuthUser = Depends(get_admin_user)):
    from app.core.config import AgentRole, get_settings
    s = get_settings()
    return {role.value: s.model_for(role) for role in AgentRole}


@router.post("/config/models")
@locked
def set_model(req: ModelOverride, admin: AuthUser = Depends(get_admin_user)):
    from app.core.config import AgentRole, get_settings
    try:
        role = AgentRole(req.role)
    except ValueError:
        raise HTTPException(400, f"unknown role: {req.role}")
    get_settings().set_model_override(role, req.model)
    return {"role": req.role, "model": req.model}
