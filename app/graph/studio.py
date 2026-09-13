"""LangGraph Studio 入口(验证用,graph_canvas 路线第一步)。

复用生产引擎装配,但默认跑在故事库的备份副本上:
  - 备份用 sqlite3 backup API(WAL 安全,主程序运行中也能拷);
  - Studio 跑的新 run 写副本库,生产数据零污染;
  - 每次启动重新备份,副本始终是最新生产快照。

注意:不带自定义 checkpointer 编译 —— langgraph dev 的 API 运行时
强制平台接管持久化,自带 SqliteSaver 会直接 GraphLoadError。代价:
历史 506 个 checkpoint 在 Studio 不可见,只能对 Studio 里新开的 run
做节点流转/time-travel/interrupt 表单。想直连生产库时
NOVEL_STUDIO_DB 指向原库(自担写入风险)。

langgraph.json: {"graphs": {"novel": "app.graph.studio:graph"}}
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from app.core.config import get_settings
from app.graph.build import build_graph
from app.graph.runtime import build_engine

REPLAY_DB = Path("data") / "studio_replay.db"


def _backup(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    s = sqlite3.connect(str(src))
    d = sqlite3.connect(str(dst))
    try:
        with d:
            s.backup(d)
    finally:
        s.close()
        d.close()


def _resolve_db() -> Path:
    """NOVEL_STUDIO_DB 显式指定 > 生产库备份副本。"""
    explicit = os.environ.get("NOVEL_STUDIO_DB")
    if explicit:
        return Path(explicit)
    src = get_settings().db_path
    if not src.exists():
        raise FileNotFoundError(f"故事库不存在: {src}(先跑一次生成)")
    # 副本不落后于生产库(WAL 的 -shm/-wal 不计)则跳过备份:
    # dev 服务器热重载会反复重导入本模块,不守卫会每次重拷近 300MB
    src_mtime = src.stat().st_mtime
    if REPLAY_DB.exists() and REPLAY_DB.stat().st_mtime >= src_mtime:
        return REPLAY_DB
    _backup(src, REPLAY_DB)
    print(f"[studio] 已备份 {src} -> {REPLAY_DB}", flush=True)
    return REPLAY_DB


deps, _conn = build_engine(_resolve_db())
graph = build_graph(deps)   # 不带自定义 checkpointer(见模块 docstring)
