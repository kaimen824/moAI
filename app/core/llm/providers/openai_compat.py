"""OpenAI 兼容客户端族:任何暴露 /chat/completions 与 /embeddings 的服务。

超时与重试显式化(评审 6.10 / ADR-0026):SDK 内置 429/5xx 指数退避
(max_retries),超时统一取 Settings.llm_timeout_seconds——偶发慢响应不再
无限挂起生成线程。
"""

from __future__ import annotations

import threading
from typing import Iterator, Sequence

from openai import OpenAI

from app.core.config import get_settings
from app.core.llm.base import (
    ChatClient,
    ChatMessage,
    EmbedClient,
    EmbeddingResult,
    LLMResponse,
    ProviderFamily,
)


def _cached_tokens(usage) -> int:
    """OpenAI 兼容 usage.prompt_tokens_details.cached_tokens(不支持的服务缺省 0)。"""
    details = getattr(usage, "prompt_tokens_details", None)
    return getattr(details, "cached_tokens", 0) or 0


class OpenAICompatChat(ChatClient):
    def __init__(self, api_key: str, base_url: str):
        settings = get_settings()
        self._client = OpenAI(
            api_key=api_key, base_url=base_url,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )
        # 最近一次 stream 的精确 usage(prompt, completion, cached);
        # 客户端经 factory 缓存复用,以 threading.local 隔离——多 story
        # 并发流式时各线程读到自己的 usage(非流式不经过此属性)
        self._usage_local = threading.local()

    @property
    def last_usage(self) -> tuple[int, int, int] | None:
        return getattr(self._usage_local, "usage", None)

    @staticmethod
    def _serialize(messages: Sequence[ChatMessage]) -> list[dict]:
        """ChatMessage -> OpenAI 消息(含 tool_calls / tool 回执,ADR-0031)。"""
        out: list[dict] = []
        for m in messages:
            if m.role == "assistant" and m.tool_calls:
                out.append({
                    "role": "assistant",
                    "content": m.content or "",
                    "tool_calls": [
                        {"id": tc["id"], "type": "function",
                         "function": {"name": tc["name"],
                                      "arguments": tc.get("arguments", "{}")}}
                        for tc in m.tool_calls],
                })
            elif m.role == "tool":
                out.append({"role": "tool", "tool_call_id": m.tool_call_id,
                            "name": m.name, "content": m.content or ""})
            else:
                out.append({"role": m.role, "content": m.content})
        return out

    def chat(
        self,
        model: str,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        response_format: dict | None = None,
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        kwargs: dict = {
            "model": model,
            "messages": self._serialize(messages),
            "temperature": temperature,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if response_format is not None:
            kwargs["response_format"] = response_format
        if tools is not None:
            kwargs["tools"] = tools
        resp = self._client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        usage = getattr(resp, "usage", None)
        calls = []
        for tc in (getattr(choice.message, "tool_calls", None) or []):
            fn = tc.function
            calls.append({"id": tc.id, "name": fn.name,
                          "arguments": fn.arguments or "{}"})
        return LLMResponse(
            content=choice.message.content or "",
            model=model,
            tokens_in=getattr(usage, "prompt_tokens", 0) or 0,
            tokens_out=getattr(usage, "completion_tokens", 0) or 0,
            cached_tokens=_cached_tokens(usage) if usage is not None else 0,
            finish_reason=choice.finish_reason or "",
            tool_calls=calls,
        )

    def stream(
        self,
        model: str,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> Iterator[str]:
        kwargs: dict = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "stream": True,
            # 最后一个 chunk 携带 usage(精确 token 统计;不支持的服务会忽略此参数)
            "stream_options": {"include_usage": True},
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        self._usage_local.usage = None
        stream = self._client.chat.completions.create(**kwargs)
        for chunk in stream:
            usage = getattr(chunk, "usage", None)
            if usage is not None:
                self._usage_local.usage = (
                    getattr(usage, "prompt_tokens", 0) or 0,
                    getattr(usage, "completion_tokens", 0) or 0,
                    _cached_tokens(usage))
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


class OpenAICompatEmbed(EmbedClient):
    def __init__(self, api_key: str, base_url: str):
        self._client = OpenAI(api_key=api_key, base_url=base_url)

    def embed(self, model: str, texts: Sequence[str]) -> EmbeddingResult:
        resp = self._client.embeddings.create(model=model, input=list(texts))
        vectors = [item.embedding for item in resp.data]
        usage = getattr(resp, "usage", None)
        return EmbeddingResult(
            vectors=vectors,
            model=model,
            tokens_in=getattr(usage, "prompt_tokens", 0) or 0,
        )


class OpenAICompatFamily(ProviderFamily):
    """通用 OpenAI 兼容工厂。"""

    def __init__(self, name: str, api_key: str, base_url: str):
        self.name = name
        self._api_key = api_key
        self._base_url = base_url

    def create_chat_client(self) -> ChatClient:
        return OpenAICompatChat(self._api_key, self._base_url)

    def create_embed_client(self) -> EmbedClient:
        return OpenAICompatEmbed(self._api_key, self._base_url)
