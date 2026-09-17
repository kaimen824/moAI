"""管理员端点:用户管理、运营观测(ADR-0022/0028)。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import _active, engine, locked
from app.auth import AuthUser, get_admin_user, hash_password
from app.infrastructure.queries import ObservabilityQueries, UserStore

router = APIRouter()


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "user"               # admin|user


class UpdateUserRequest(BaseModel):
    status: str = ""                 # active|disabled(空=不改)
    new_password: str = ""           # 空=不改


@router.post("/admin/users")
def admin_create_user(req: CreateUserRequest,
                      admin: AuthUser = Depends(get_admin_user)):
    """管理员开户(ADR-0022 注册策略:无自助注册)。"""
    deps, _ = engine()
    if not req.username.strip() or len(req.password) < 8:
        raise HTTPException(400, "username required; password at least 8 characters")
    if req.role not in ("admin", "user"):
        raise HTTPException(400, "role must be admin|user")
    try:
        UserStore(deps.conn).create(
            req.username.strip(), hash_password(req.password), req.role)
    except Exception as exc:   # UNIQUE 冲突等
        raise HTTPException(400, f"cannot create user: {exc}")
    return {"ok": True, "username": req.username.strip(), "role": req.role}


@router.get("/admin/users")
def admin_list_users(admin: AuthUser = Depends(get_admin_user)):
    deps, _ = engine()
    return UserStore(deps.conn).list_all()


@router.patch("/admin/users/{user_id}")
def admin_update_user(user_id: str, req: UpdateUserRequest,
                      admin: AuthUser = Depends(get_admin_user)):
    """禁用/启用、重置密码。禁用即时生效(每请求校验 status)。"""
    deps, _ = engine()
    users = UserStore(deps.conn)
    if not users.exists(user_id):
        raise HTTPException(404, "user not found")
    if req.status and req.status not in ("active", "disabled"):
        raise HTTPException(400, "status must be active|disabled")
    if req.status == "disabled" and user_id == admin.id:
        raise HTTPException(400, "cannot disable yourself")
    changed = 0
    if req.status:
        changed += users.set_status(user_id, req.status)
    if req.new_password:
        if len(req.new_password) < 8:
            raise HTTPException(400, "new password at least 8 characters")
        changed += users.update_password(user_id, hash_password(req.new_password))
    if not changed:
        raise HTTPException(400, "nothing to update")
    users.commit()
    return {"ok": True, "user_id": user_id}


@router.get("/admin/stats")
@locked
def admin_stats(admin: AuthUser = Depends(get_admin_user)):
    """运营观测(ADR-0028,评审 6.13):JSON 口径,不引外部 metrics 栈。

    active runs / 等待中断数 / 错误 run 数 / 近 24h LLM 调用与失败率 /
    token 成本(按日+story+owner)。失败率分母为 usage_log 调用次数,
    分子为 llm_failures 台账条数。
    """
    deps, _ = engine()
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    obs = ObservabilityQueries(deps.conn)
    stats = obs.ops_stats(since)
    calls_24h, failures_24h = stats["llm_calls"], stats["llm_failures"]
    return {
        "active_runs": len(_active),
        "active_story_ids": sorted(_active),
        "waiting_interruptions": stats["waiting_interruptions"],
        "error_runs": stats["error_runs"],
        "llm_calls_24h": calls_24h,
        "llm_failures_24h": failures_24h,
        "llm_failure_rate_24h": (round(failures_24h / calls_24h, 4)
                                 if calls_24h else None),
        "token_cost": obs.token_cost(),
    }
