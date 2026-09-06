"""配置层(ADR-0008 / DESIGN_FINAL §6)。

配置优先级:运行时覆盖(前端配置页/测试注入)> 环境变量 > 代码默认值。
模型按 Agent 角色独立配置:环境变量 ``MODEL__<ROLE>``。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from threading import Lock


def _load_dotenv(path: Path | None = None) -> None:
    """极简 .env 加载(已设置的环境变量优先,不覆盖)。测试隔离时置 NOVEL_NO_DOTENV=1 禁用。"""
    if os.environ.get("NOVEL_NO_DOTENV"):
        return
    env_file = path or Path(__file__).resolve().parents[2] / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


class AgentRole(str, Enum):
    """模型路由角色(ADR-0008 分级表)。"""

    SUPERVISOR = "SUPERVISOR"      # 主控:细纲/裁决 — 强
    OUTLINE = "OUTLINE"            # 大纲评审 — 强
    REVIEWER = "REVIEWER"          # 审校 — 强
    WRITER = "WRITER"              # 写作 — 强(默认,可配置降档)
    EVENT = "EVENT"                # 事实抽取 — 中
    CHARACTER = "CHARACTER"        # 角色管理 — 便宜
    SUMMARY = "SUMMARY"            # 摘要 — 便宜
    EMBEDDING = "EMBEDDING"        # embedding 独立配置


# 代码默认值(最低优先级);实际部署通过 .env / 环境变量 / 前端配置页覆盖
# 默认走阿里云百炼(qwen 系);切换 GLM 只需 MODEL__* 环境变量覆盖
DEFAULT_MODELS: dict[AgentRole, str] = {
    AgentRole.SUPERVISOR: "qwen-max",
    AgentRole.OUTLINE: "qwen-max",
    AgentRole.REVIEWER: "qwen-max",
    AgentRole.WRITER: "qwen-max",
    AgentRole.EVENT: "qwen-plus",
    AgentRole.CHARACTER: "qwen-turbo",
    AgentRole.SUMMARY: "qwen-turbo",
    AgentRole.EMBEDDING: "text-embedding-v3",
}

DEFAULT_DB_PATH = Path("data") / "novel_agent.db"

MODEL_ENV_PREFIX = "MODEL__"


@dataclass
class Settings:
    """全局配置。读取顺序:runtime_overrides > 环境变量 > 默认值。"""

    glm_api_key: str = ""
    dashscope_api_key: str = ""
    db_path: Path = DEFAULT_DB_PATH
    _model_overrides: dict[AgentRole, str] = field(default_factory=dict)

    # ---- 模型路由 ----
    def model_for(self, role: AgentRole | str) -> str:
        role = AgentRole(role)
        # 1) 运行时覆盖(最高优先级)
        if role in self._model_overrides:
            return self._model_overrides[role]
        # 2) 环境变量
        env_val = os.environ.get(f"{MODEL_ENV_PREFIX}{role.value}")
        if env_val:
            return env_val
        # 3) 代码默认值
        return DEFAULT_MODELS[role]

    def set_model_override(self, role: AgentRole | str, model: str) -> None:
        """前端配置页写入运行时覆盖。"""
        self._model_overrides[AgentRole(role)] = model

    def clear_overrides(self) -> None:
        self._model_overrides.clear()

    # ---- 上下文管理器:测试/临时切换 ----
    def with_overrides(self, **models: str) -> "_OverrideContext":
        return _OverrideContext(self, models)


class _OverrideContext:
    def __init__(self, settings: Settings, models: dict[str, str]):
        self._settings = settings
        self._models = models

    def __enter__(self) -> Settings:
        for role, model in self._models.items():
            self._settings.set_model_override(role, model)
        return self._settings

    def __exit__(self, *exc) -> None:
        for role in self._models:
            self._settings._model_overrides.pop(AgentRole(role), None)


_lock = Lock()
_settings: Settings | None = None


def get_settings() -> Settings:
    """进程级单例。环境变量在此时读取(.env 预加载,已设置的环境变量优先)。"""
    global _settings
    with _lock:
        if _settings is None:
            _load_dotenv()
            _settings = Settings(
                glm_api_key=os.environ.get("GLM_API_KEY", ""),
                dashscope_api_key=os.environ.get("DASHSCOPE_API_KEY", ""),
                db_path=Path(os.environ.get("NOVEL_DB_PATH", str(DEFAULT_DB_PATH))),
            )
        return _settings


def reset_settings() -> None:
    """仅用于测试。"""
    global _settings
    with _lock:
        _settings = None
