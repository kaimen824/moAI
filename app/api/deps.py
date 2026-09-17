"""表现层装配(ADR-0030 阶段1):引擎单例、运行互斥集合、端点锁。

本模块是引擎状态的唯一宿主;app/api/main.py 再导出以保持
`import app.main as main` 的既有用法(测试经别名访问)。
"""

from __future__ import annotations

import functools
import threading
from typing import Any

from app.core.config import get_settings
from app.graph.build import build_graph
from app.graph.runtime import Deps, build_engine

_engine: tuple[Deps, Any] | None = None
_graph: Any = None
_active: set[str] = set()          # 运行中的 thread(= story_id)
_active_lock = threading.Lock()    # check-then-add 原子性(ADR-0023)


def engine() -> tuple[Deps, Any]:
    global _engine, _graph
    if _engine is None:
        deps, conn = build_engine(get_settings().db_path)
        _engine = (deps, conn)
        _graph = build_graph(deps, checkpointer=deps.checkpointer)
    return _engine


def install_engine(deps: Deps, conn, graph) -> None:
    """测试注入:替换引擎单例(生产不调用)。"""
    global _engine, _graph
    _engine, _graph = (deps, conn), graph


def reset_engine() -> None:
    """测试隔离:清空引擎单例,下次 engine() 懒加载重建(生产不调用)。"""
    global _engine, _graph
    _engine, _graph = None, None


def locked(fn):
    """同步端点统一套引擎锁(DB 访问与图执行线程互斥)。"""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        deps, _ = engine()
        with deps.run_lock:
            return fn(*args, **kwargs)
    return wrapper
