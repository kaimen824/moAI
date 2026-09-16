"""日志落盘(排障基建):novel.agent 与 uvicorn.error 双通道写 logs/novel.log。

控制台(uvicorn stdout)进程一关现场即失——2026-09-16 排查"生成重复输出"
时后端进程消失,无任何崩溃记录可查。RotatingFileHandler 按大小轮转;
目录/级别经 Settings(NOVEL_LOG_DIR / NOVEL_LOG_LEVEL)覆盖。
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.core.config import get_settings

_FMT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_FILES = ("novel.agent", "uvicorn.error")   # 业务日志 + 服务层错误(崩溃现场)
_MARKER = "_molan_file_handler"              # 幂等标记:重复 import 不叠加 handler


def setup_logging() -> None:
    """安装文件 handler(幂等)。main.py import 时调用一次。"""
    root_pkg = logging.getLogger("novel.agent")
    if any(getattr(h, _MARKER, False) for h in root_pkg.handlers):
        return
    s = get_settings()
    log_dir = Path(s.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_dir / "novel.log",
                                  maxBytes=5 * 1024 * 1024, backupCount=5,
                                  encoding="utf-8")
    handler.setFormatter(logging.Formatter(_FMT))
    setattr(handler, _MARKER, True)
    for name in _FILES:
        logging.getLogger(name).addHandler(handler)
    root_pkg.setLevel(getattr(logging, s.log_level.upper(), logging.INFO))


def reset_logging() -> None:
    """仅用于测试:摘除文件 handler,恢复未装配状态。"""
    for name in _FILES:
        lg = logging.getLogger(name)
        lg.handlers = [h for h in lg.handlers if not getattr(h, _MARKER, False)]
