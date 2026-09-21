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
    AgentRole.ENTITY: 0.2,       # 消歧裁决:确定性优先(ADR-0015)
    AgentRole.POLISH: 0.3,       # 文风精校:忠实改写优先(ADR-0018)
    AgentRole.SUMMARY: 0.3,
    AgentRole.CHAT: 0.6,         # 对话助理:稳定但有温度
    AgentRole.EMBEDDING: 0.0,    # 无意义,占位
}


@dataclass
class Route:
    provider: str
    model: str
    temperature: float


def resolve_provider(model: str) -> str:
    """模型名前缀 -> provider(仅按名字猜;优先级低于 key 可用性判定,见 ModelRouter)。"""
    # 阿里云百炼模型族:qwen-* / deepseek-* / glm-*(百炼聚合)/ *-text-embedding
    if (model.startswith(("qwen", "deepseek", "embedding-", "text-embedding"))
            or model.startswith("glm")):
        return "dashscope"
    return "openai"


class ModelRouter:
    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()

    def route(self, role: AgentRole | str) -> Route:
        role = AgentRole(role)
        model = self._settings.model_for(role)
        # 专属 key 优先于聚合(ADR-0033,所有者 2026-09-20 委托拍板):
        # deepseek-* 配了官方 key 直连 api.deepseek.com;其余模型百炼
        # (DashScope)聚合优先(聚合了 glm/qwen/MiniMax 全系),无百炼
        # key 时 glm-* 落智谱直连,再兜底 openai 兼容。
        s = self._settings
        if model.startswith("deepseek") and s.deepseek_api_key:
            # 思考白名单(ADR-0037):一致性核对/数值推演的评审角色恢复思考
            thinking = {r.strip() for r in s.deepseek_thinking_roles.split(",")
                        if r.strip()}
            provider = ("deepseek_think" if role.value in thinking
                        else "deepseek")
        elif s.dashscope_api_key:
            provider = "dashscope"
        elif model.startswith("glm") and s.glm_api_key:
            provider = "glm"
        else:
            provider = resolve_provider(model)
        return Route(
            provider=provider,
            model=model,
            temperature=ROLE_TEMPERATURES.get(role, 0.5),
        )
