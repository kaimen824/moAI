"""策略路由:Agent 角色 -> provider + 模型参数。

provider 判定规则(与 ADR-0008 一致):模型名以 glm- 开头 -> glm provider;
其余走 openai 兼容。角色到模型名的解析由 Settings(三级优先级)完成。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import AgentRole, Settings, get_settings

# 各角色的默认温度:评审/抽取要稳,写作要活
ROLE_TEMPERATURES: dict[AgentRole, float] = {
    AgentRole.SUPERVISOR: 0.6,
    AgentRole.OUTLINE: 0.3,       # 评审:确定性优先
    AgentRole.REVIEWER: 0.3,     # 评审:确定性优先
    AgentRole.WRITER: 0.8,       # 创作:多样性
    AgentRole.EVENT: 0.2,        # 结构化抽取:最稳
    AgentRole.CHARACTER: 0.4,
    AgentRole.SUMMARY: 0.3,
    AgentRole.EMBEDDING: 0.0,    # 无意义,占位
}


@dataclass
class Route:
    provider: str
    model: str
    temperature: float


def resolve_provider(model: str) -> str:
    # 智谱模型族:glm-* 系列 + embedding-*
    if model.startswith("glm") or model.startswith("embedding-"):
        return "glm"
    return "openai"


class ModelRouter:
    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()

    def route(self, role: AgentRole | str) -> Route:
        role = AgentRole(role)
        model = self._settings.model_for(role)
        return Route(
            provider=resolve_provider(model),
            model=model,
            temperature=ROLE_TEMPERATURES.get(role, 0.5),
        )
