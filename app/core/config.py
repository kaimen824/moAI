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
DEFAULT_MODELS: dict[AgentRole, str] = {
    AgentRole.SUPERVISOR: "glm-4.6",
    AgentRole.OUTLINE: "glm-4.6",
    AgentRole.REVIEWER: "glm-4.6",
    AgentRole.WRITER: "glm-4.6",
    AgentRole.EVENT: "glm-4.5-air",
    AgentRole.CHARACTER: "glm-4.5-flash",
    AgentRole.SUMMARY: "glm-4.5-flash",
    AgentRole.EMBEDDING: "embedding-3",
}

DEFAULT_DB_PATH = Path("data") / "novel_agent.db"

MODEL_ENV_PREFIX = "MODEL__"


@dataclass
class Settings:
    """全局配置。读取顺序:runtime_overrides > 环境变量 > 默认值。"""

    glm_api_key: str = ""
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
    """进程级单例。环境变量在此时读取(GLM_API_KEY / NOVEL_DB_PATH)。"""
    global _settings
    with _lock:
        if _settings is None:
            _settings = Settings(
                glm_api_key=os.environ.get("GLM_API_KEY", ""),
                db_path=Path(os.environ.get("NOVEL_DB_PATH", str(DEFAULT_DB_PATH))),
            )
        return _settings


def reset_settings() -> None:
    """仅用于测试。"""
    global _settings
    with _lock:
        _settings = None
