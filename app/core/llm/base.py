"""LLM 抽象接口:provider 客户端族的契约(抽象工厂的产品族)。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterator, Sequence


@dataclass
class ChatMessage:
    role: str                      # system | user | assistant
    content: str


@dataclass
class LLMResponse:
    content: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    cached_tokens: int = 0        # prompt 命中缓存的 token 数(不支持的服务为 0)
    finish_reason: str = ""


@dataclass
class EmbeddingResult:
    vectors: list[list[float]]
    model: str
    tokens_in: int = 0


@dataclass
class UsageRecord:
    """单次 LLM 调用的埋点记录(由上层 sink 落 usage_log)。"""

    agent: str                     # AgentRole 名
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    cached_tokens: int = 0         # prompt 缓存命中 token(缓存命中率 = cached/tokens_in)
    latency_ms: int = 0
    trace_id: str = ""
    stage: str = ""                # 调用环节:outline/draft/review/extract/summary/...
    story_id: str = ""
    usage_meta: dict = field(default_factory=dict)


class ChatClient(ABC):
    """chat 客户端抽象。"""

    @abstractmethod
    def chat(
        self,
        model: str,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        response_format: dict | None = None,   # {"type": "json_object"} 等
    ) -> LLMResponse: ...

    @abstractmethod
    def stream(
        self,
        model: str,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> Iterator[str]: ...


class EmbedClient(ABC):
    """embedding 客户端抽象。"""

    @abstractmethod
    def embed(self, model: str, texts: Sequence[str]) -> EmbeddingResult: ...


class ProviderFamily(ABC):
    """抽象工厂:每个 provider 提供一族客户端(chat + embedding)。"""

    name: str

    @abstractmethod
    def create_chat_client(self) -> ChatClient: ...

    @abstractmethod
    def create_embed_client(self) -> EmbedClient: ...
