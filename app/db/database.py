"""SQLite 连接管理:WAL 模式、外键开启、初始化与种子。"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.db.ddl import migrate
from app.db.seed import build_seed


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_admin_seed(conn) -> None:
    """管理员账户种子 + 存量 story 归属回填(ADR-0022)。

    users 表为空时按环境变量创建初始管理员(NOVEL_ADMIN_USER / NOVEL_ADMIN_PASSWORD,
    默认 admin/admin123——生产部署必须通过环境变量覆盖);owner_id 为空的存量
    story 回填给首个管理员(单机时代的书都归管理员,协作归属后续人工调整)。
    """
    import logging
    import os

    from app.auth import hash_password

    row = conn.execute("SELECT COUNT(*) c FROM users").fetchone()
    if row["c"] == 0:
        username = os.environ.get("NOVEL_ADMIN_USER", "admin")
        password = os.environ.get("NOVEL_ADMIN_PASSWORD", "admin123")
        if password == "admin123":
            logging.getLogger(__name__).warning(
                "使用默认管理员密码;公网部署必须设置 NOVEL_ADMIN_PASSWORD")
        conn.execute(
            "INSERT INTO users (id, username, password_hash, role, status, created_at)"
            " VALUES (?, ?, ?, 'admin', 'active', ?)",
            (uuid.uuid4().hex, username, hash_password(password), _utcnow()))
    # 存量 story 归属回填(幂等:只补 NULL)
    admin = conn.execute(
        "SELECT id FROM users WHERE role='admin' ORDER BY created_at LIMIT 1").fetchone()
    if admin:
        conn.execute(
            "UPDATE stories SET owner_id=? WHERE owner_id IS NULL", (admin["id"],))
        conn.execute(
            "INSERT OR IGNORE INTO story_members (story_id, user_id, role, created_at)"
            " SELECT id, ?, 'owner', ? FROM stories WHERE owner_id=?",
            (admin["id"], _utcnow(), admin["id"]))
    conn.commit()


def connect(db_path: str | Path) -> sqlite3.Connection:
    """打开连接:WAL + 外键 + 行字典。每次新连接(单写者场景足够)。"""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False:LangGraph 读写可能在 worker 线程;
    # 安全前提 = 单写者 + 章节生成串行(实现期备忘的既有约束)
    # isolation_level=None:autocommit——显式事务(定稿单事务)由调用方 BEGIN/COMMIT 管理
    conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str | Path, *, seed_acl: bool = True) -> sqlite3.Connection:
    """初始化数据库:按版本补齐 schema(ADR-0021)+ 种子(acl 与管理员,ADR-0022)。幂等。"""
    conn = connect(db_path)
    migrate(conn)   # baseline 重放 + 增量版本(含首次建全表)
    if seed_acl:
        conn.executemany(
            "INSERT OR REPLACE INTO agent_acl (agent_name, data_domain, can_read, can_write) "
            "VALUES (?, ?, ?, ?)",
            build_seed(),
        )
        _ensure_admin_seed(conn)
    return conn
