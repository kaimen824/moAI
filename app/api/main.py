"""FastAPI 服务层入口(ADR-0009/0030 阶段1):create_app 工厂装配各域路由。

运行:uvicorn app.main:app(app.main 经 sys.modules 别名指向本模块);
图经 thread_id(= story_id)驱动;中断事件经 SSE 下发,resume 由客户端回传。
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from app.api.routes import admin, auth, chat, config, entities, facts, observability, stories
from app.core.logsetup import setup_logging

setup_logging()                     # 日志落盘(logs/novel.log):崩溃/卡死现场可追溯
logger = logging.getLogger("novel.agent")


def create_app() -> FastAPI:
    app = FastAPI(title="novel-agent", version="0.1.0")
    for r in (stories.router, auth.router, admin.router, facts.router,
              entities.router, config.router, observability.router,
              chat.router):
        app.include_router(r)
    return app


app = create_app()

# ---- 兼容再导出(app.main 经 sys.modules 别名指向本模块,测试仍以
# `import app.main as main` 访问引擎单例与互斥集合;状态真身在 api.deps)----
from app.api.deps import (  # noqa: E402
    _active,
    _active_lock,
    engine,
    install_engine,
    locked,
    reset_engine,
)
from app.core.config import get_settings  # noqa: E402  (settings 单例入口)
