"""LLM 抽象接口:provider 客户端族的契约(抽象工厂的产品族)。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterator, Sequence


@dataclass
class ChatMessage:
    role: str                      # system | user | assistant | tool
    content: str
    # function calling(ADR-0031 ChatDock):assistant 的工具调用请求 /
    # tool 消息的工具回执——不参与调用时保持默认,序列化即原三字段
    tool_calls: list[dict] | None = None   # [{id, name, arguments(json str)}]
    tool_call_id: str = ""                 # role=tool 时对应请求的 id
    name: str = ""                         # role=tool 时的工具名


@dataclass
class LLMResponse:
    content: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    cached_tokens: int = 0        # prompt 命中缓存的 token 数(不支持的服务为 0)
    finish_reason: str = ""
    # 模型请求的工具调用(OpenAI 兼容格式;content 为空、finish_reason=tool_calls)
    tool_calls: list[dict] = field(default_factory=list)


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
        tools: list[dict] | None = None,       # function calling 工具表(透传)
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
