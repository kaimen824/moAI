"""密码哈希(ADR-0022,评审 6.1 P0)。

置于 core 层(分层契约的最底层):app.db 的管理员种子与 app.auth 的
认证端点共用,db 层不得反向依赖 auth/main(app.auth 持有 FastAPI 依赖,
会拖出 api 层导入链——import-linter 曾实测抓到 db->auth->main->graph)。
argon2id 默认参数;verify 对损坏哈希按验证失败处理,不外泄异常。
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_hasher = PasswordHasher()          # argon2id 默认参数


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(plain: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, plain)
    except VerifyMismatchError:
        return False
    except Exception:   # noqa: BLE001 — 哈希格式损坏等,按验证失败处理
        return False
