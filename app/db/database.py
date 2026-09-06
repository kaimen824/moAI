"""SQLite 连接管理:WAL 模式、外键开启、初始化与种子。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.db.ddl import SCHEMA_SQL
from app.db.seed import build_seed


def connect(db_path: str | Path) -> sqlite3.Connection:
    """打开连接:WAL + 外键 + 行字典。每次新连接(单写者场景足够)。"""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str | Path, *, seed_acl: bool = True) -> sqlite3.Connection:
    """初始化数据库:建全表 + (可选)写入 agent_acl 种子。幂等。"""
    conn = connect(db_path)
    conn.executescript(SCHEMA_SQL)
    if seed_acl:
        conn.executemany(
            "INSERT OR REPLACE INTO agent_acl (agent_name, data_domain, can_read, can_write) "
            "VALUES (?, ?, ?, ?)",
            build_seed(),
        )
    conn.commit()
    return conn
