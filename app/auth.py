"""认证与多租户边界(ADR-0022,评审 6.1 P0)。

- 凭据:argon2id 密码哈希;登录签发 HS256 JWT(短期,默认 2h)。
- 授权:story 级归属(stories.owner_id + story_members);admin 全域。
- 撤销语义:短有效期 + 每请求校验 users.status(active),禁用即时生效,
  不引黑名单表(评审修复方案 §6 风险表裁决)。

边界:Agent ACL(ADR-0006)约束 Agent 对数据域的访问;本模块约束 HTTP
用户之间的数据访问——两者正交。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings
from app.core.security import hash_password, verify_password   # noqa: F401 (再导出)

_bearer = HTTPBearer(auto_error=False)

ADMIN_ROLE = "admin"


def create_token(*, user_id: str, username: str, role: str,
                 token_type: str = "access") -> str:
    """签发 JWT。token_type: access(短效,业务请求)| refresh(长效,仅换新用)。

    类型写入 payload 并在消费端互斥校验——refresh token 不能当 access 用,
    反之亦然(防降级:长效凭证顶替短效凭证绕过 2h 窗口)。
    """
    s = get_settings()
    now = datetime.now(timezone.utc)
    hours = (s.jwt_expire_hours if token_type == "access"
             else s.jwt_refresh_expire_hours)
    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
        "typ": token_type,
        "iat": now,
        "exp": now + timedelta(hours=hours),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, s.jwt_secret, algorithm="HS256")


def create_access_token(*, user_id: str, username: str, role: str) -> str:
    return create_token(user_id=user_id, username=username, role=role,
                        token_type="access")


def decode_token(token: str) -> dict[str, Any]:
    """解码并校验 JWT;无效/过期抛 401(不区分原因,避免给攻击者探针)。"""
    try:
        return jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "invalid or expired token")


class AuthUser(dict):
    """请求级身份(sub/username/role)。dict 子类便于直接序列化进日志。"""

    @property
    def id(self) -> str:
        return self["id"]

    @property
    def is_admin(self) -> bool:
        return self["role"] == ADMIN_ROLE


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> AuthUser:
    """Bearer 认证依赖:JWT 有效 + 是 access 类型 + 用户存在且 active(禁用即时失效)。"""
    if creds is None:
        raise HTTPException(401, "authentication required")
    payload = decode_token(creds.credentials)
    if payload.get("typ") != "access":
        raise HTTPException(401, "invalid or expired token")   # refresh 不能当 access 用
    from app.main import engine   # 延迟导入避免环
    deps, _ = engine()
    row = deps.conn.execute(
        "SELECT id, username, role, status FROM users WHERE id=?",
        (payload["sub"],)).fetchone()
    if row is None or row["status"] != "active":
        raise HTTPException(401, "invalid or expired token")
    return AuthUser(id=row["id"], username=row["username"], role=row["role"])


def get_refresh_payload(creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
                        ) -> dict[str, Any]:
    """refresh 端点专用:校验 refresh 类型 JWT(类型互斥,access 不能当 refresh 用)。"""
    if creds is None:
        raise HTTPException(401, "authentication required")
    payload = decode_token(creds.credentials)
    if payload.get("typ") != "refresh":
        raise HTTPException(401, "invalid or expired token")
    return payload


def get_admin_user(user: AuthUser = Depends(get_current_user)) -> AuthUser:
    if not user.is_admin:
        raise HTTPException(403, "admin privilege required")
    return user


def story_role(conn, story_id: str, user: AuthUser) -> str | None:
    """当前用户对 story 的角色:owner/member 角色;admin 恒 'admin';无权 None。"""
    if user.is_admin:
        row = conn.execute("SELECT 1 FROM stories WHERE id=?", (story_id,)).fetchone()
        return "admin" if row else None
    row = conn.execute(
        "SELECT role FROM story_members WHERE story_id=? AND user_id=?",
        (story_id, user.id)).fetchone()
    return row["role"] if row else None


def require_story(conn, story_id: str, user: AuthUser) -> None:
    """story 级访问校验:无权 404(不泄露存在性)。"""
    if story_role(conn, story_id, user) is None:
        raise HTTPException(404, "story not found")


def visible_story_ids(conn, user: AuthUser) -> list[str]:
    """当前用户可见的 story 集合(admin 全量)——审核队列等跨 story 端点用。"""
    if user.is_admin:
        rows = conn.execute("SELECT id FROM stories").fetchall()
    else:
        rows = conn.execute(
            "SELECT story_id AS id FROM story_members WHERE user_id=?",
            (user.id,)).fetchall()
    return [r["id"] for r in rows]
