"""provider 注册与工厂:新增厂商 = 注册一个新的 ProviderFamily。"""

from __future__ import annotations

from app.core.llm.base import ChatClient, EmbedClient, ProviderFamily
from app.core.llm.providers.dashscope import DashScopeFamily
from app.core.llm.providers.deepseek import DeepSeekFamily
from app.core.llm.providers.glm import GLMFamily
from app.core.llm.providers.openai_compat import OpenAICompatFamily


class ProviderRegistry:
    """provider 注册表(可插拔点之一,ADR-0012)。"""

    def __init__(self) -> None:
        self._families: dict[str, ProviderFamily] = {}

    def register(self, family: ProviderFamily) -> None:
        self._families[family.name] = family

    def get(self, name: str) -> ProviderFamily:
        if name not in self._families:
            raise KeyError(
                f"unknown provider: {name!r}, registered: {sorted(self._families)}"
            )
        return self._families[name]

    def names(self) -> list[str]:
        return sorted(self._families)


class ProviderFactory:
    """抽象工厂的入口:按 provider 名取客户端族。

    客户端按 provider 缓存复用(评审 6.10 / ADR-0026):此前每次调用 new 一个
    OpenAI 客户端,连接池/预热全部浪费。单任务串行约束下复用安全;流式
    last_usage 以 threading.local 隔离,多 story 并发不串台。
    """

    def __init__(self, registry: ProviderRegistry):
        self._registry = registry
        self._chat: dict[str, ChatClient] = {}
        self._embed: dict[str, EmbedClient] = {}

    def chat_client(self, provider: str) -> ChatClient:
        if provider not in self._chat:
            self._chat[provider] = self._registry.get(provider).create_chat_client()
        return self._chat[provider]

    def embed_client(self, provider: str) -> EmbedClient:
        if provider not in self._embed:
            self._embed[provider] = self._registry.get(provider).create_embed_client()
        return self._embed[provider]


def build_default_factory(glm_api_key: str, dashscope_api_key: str = "",
                          deepseek_api_key: str = "") -> ProviderFactory:
    """默认装配:GLM + 百炼(DashScope)+ DeepSeek 官方 + 通用 OpenAI 兼容。"""
    registry = ProviderRegistry()
    registry.register(GLMFamily(api_key=glm_api_key))
    if dashscope_api_key:
        registry.register(DashScopeFamily(api_key=dashscope_api_key))
    if deepseek_api_key:
        registry.register(DeepSeekFamily(api_key=deepseek_api_key))
    registry.register(
        OpenAICompatFamily(
            name="openai", api_key=glm_api_key, base_url="https://api.openai.com/v1"
        )
    )
    return ProviderFactory(registry)
