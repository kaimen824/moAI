"""管理员端点:用户管理、运营观测(ADR-0022/0028)。"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import _active, engine, locked
from app.auth import AuthUser, get_admin_user, hash_password

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
        deps.conn.execute(
            "INSERT INTO users (id, username, password_hash, role, status, created_at)"
            " VALUES (?, ?, ?, ?, 'active', ?)",
            (uuid.uuid4().hex, req.username.strip(),
             hash_password(req.password), req.role,
             datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")))
        deps.conn.commit()
    except Exception as exc:   # UNIQUE 冲突等
        raise HTTPException(400, f"cannot create user: {exc}")
    return {"ok": True, "username": req.username.strip(), "role": req.role}


@router.get("/admin/users")
def admin_list_users(admin: AuthUser = Depends(get_admin_user)):
    deps, _ = engine()
    rows = deps.conn.execute(
        "SELECT id, username, role, status, created_at FROM users ORDER BY created_at"
    ).fetchall()
    return [dict(r) for r in rows]


@router.patch("/admin/users/{user_id}")
def admin_update_user(user_id: str, req: UpdateUserRequest,
                      admin: AuthUser = Depends(get_admin_user)):
    """禁用/启用、重置密码。禁用即时生效(每请求校验 status)。"""
    deps, _ = engine()
    row = deps.conn.execute(
        "SELECT id FROM users WHERE id=?", (user_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "user not found")
    if req.status and req.status not in ("active", "disabled"):
        raise HTTPException(400, "status must be active|disabled")
    if req.status == "disabled" and user_id == admin.id:
        raise HTTPException(400, "cannot disable yourself")
    changed = 0
    if req.status:
        changed += deps.conn.execute(
            "UPDATE users SET status=? WHERE id=?", (req.status, user_id)).rowcount
    if req.new_password:
        if len(req.new_password) < 8:
            raise HTTPException(400, "new password at least 8 characters")
        changed += deps.conn.execute(
            "UPDATE users SET password_hash=? WHERE id=?",
            (hash_password(req.new_password), user_id)).rowcount
    if not changed:
        raise HTTPException(400, "nothing to update")
    deps.conn.commit()
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
    c = deps.conn
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    calls_24h = c.execute(
        "SELECT COUNT(*) n FROM usage_log WHERE created_at>=?", (since,)).fetchone()["n"]
    failures_24h = c.execute(
        "SELECT COUNT(*) n FROM llm_failures WHERE created_at>=?", (since,)).fetchone()["n"]
    waiting = c.execute(
        "SELECT COUNT(*) n FROM story_run_state WHERE status='waiting'").fetchone()["n"]
    error_runs = c.execute(
        "SELECT COUNT(*) n FROM story_run_state WHERE error_code IS NOT NULL").fetchone()["n"]
    cost = c.execute(
        "SELECT substr(u.created_at,1,10) AS day, u.story_id, s.title, s.owner_id,"
        " COUNT(*) AS calls, SUM(COALESCE(u.tokens_in,0)) AS tokens_in,"
        " SUM(COALESCE(u.tokens_out,0)) AS tokens_out"
        " FROM usage_log u JOIN stories s ON s.id = u.story_id"
        " GROUP BY day, u.story_id ORDER BY day DESC, tokens_in + tokens_out DESC"
        " LIMIT 200").fetchall()
    return {
        "active_runs": len(_active),
        "active_story_ids": sorted(_active),
        "waiting_interruptions": waiting,
        "error_runs": error_runs,
        "llm_calls_24h": calls_24h,
        "llm_failures_24h": failures_24h,
        "llm_failure_rate_24h": (round(failures_24h / calls_24h, 4)
                                 if calls_24h else None),
        "token_cost": [dict(r) for r in cost],
    }
