"""认证与用户自助端点(ADR-0022/0029)。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import engine
from app.auth import (AuthUser, create_token, get_current_user,
                      get_refresh_payload, hash_password, verify_password)
from app.infrastructure.queries import UserStore

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


def _issue_tokens(row) -> dict:
    """为 users 行签发 access+refresh 对(ADR-0029)。"""
    kw = {"user_id": row["id"], "username": row["username"], "role": row["role"]}
    return {"access_token": create_token(**kw, token_type="access"),
            "refresh_token": create_token(**kw, token_type="refresh"),
            "token_type": "bearer",
            "username": row["username"], "role": row["role"]}


@router.post("/auth/login")
def login(req: LoginRequest):
    """用户名密码换 JWT 对(access 短效 + refresh 长效);禁用账户拒绝登录。"""
    deps, _ = engine()
    row = UserStore(deps.conn).find_by_username(req.username)
    if row is None or row["status"] != "active" or not verify_password(
            req.password, row["password_hash"]):
        raise HTTPException(401, "invalid credentials")
    return _issue_tokens(row)


@router.post("/auth/refresh")
def refresh(payload: dict = Depends(get_refresh_payload)):
    """refresh token 换新对(滑动续期);签发时再查 users.status,禁用即时失效。"""
    deps, _ = engine()
    row = UserStore(deps.conn).find_by_id(payload["sub"])
    if row is None or row["status"] != "active":
        raise HTTPException(401, "invalid or expired token")
    return _issue_tokens(row)


@router.post("/auth/change-password")
def change_password(req: ChangePasswordRequest,
                    user: AuthUser = Depends(get_current_user)):
    """当前用户自助改密(旧密码校验;改后需重新登录取新 token)。"""
    deps, _ = engine()
    users = UserStore(deps.conn)
    stored = users.password_hash(user.id)
    if stored is None or not verify_password(req.old_password, stored):
        raise HTTPException(401, "invalid credentials")
    if len(req.new_password) < 8:
        raise HTTPException(400, "new password must be at least 8 characters")
    users.set_password(user.id, hash_password(req.new_password))
    return {"ok": True}
