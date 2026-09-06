"""provider 注册与工厂:新增厂商 = 注册一个新的 ProviderFamily。"""

from __future__ import annotations

from app.core.llm.base import ChatClient, EmbedClient, ProviderFamily
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
    """抽象工厂的入口:按 provider 名取客户端族。"""

    def __init__(self, registry: ProviderRegistry):
        self._registry = registry

    def chat_client(self, provider: str) -> ChatClient:
        return self._registry.get(provider).create_chat_client()

    def embed_client(self, provider: str) -> EmbedClient:
        return self._registry.get(provider).create_embed_client()


def build_default_factory(glm_api_key: str) -> ProviderFactory:
    """默认装配:GLM + 通用 OpenAI 兼容。"""
    registry = ProviderRegistry()
    registry.register(GLMFamily(api_key=glm_api_key))
    registry.register(
        OpenAICompatFamily(
            name="openai", api_key=glm_api_key, base_url="https://api.openai.com/v1"
        )
    )
    return ProviderFactory(registry)
