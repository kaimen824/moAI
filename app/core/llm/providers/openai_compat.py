"""OpenAI 兼容客户端族:任何暴露 /chat/completions 与 /embeddings 的服务。"""

from __future__ import annotations

from typing import Iterator, Sequence

from openai import OpenAI

from app.core.llm.base import (
    ChatClient,
    ChatMessage,
    EmbedClient,
    EmbeddingResult,
    LLMResponse,
    ProviderFamily,
)


class OpenAICompatChat(ChatClient):
    def __init__(self, api_key: str, base_url: str):
        self._client = OpenAI(api_key=api_key, base_url=base_url)

    def chat(
        self,
        model: str,
        messages: Sequence[ChatMessage],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        response_format: dict | None = None,
    ) -> LLMResponse:
        kwargs: dict = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if response_format is not None:
            kwargs["response_format"] = response_format
        resp = self._client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        usage = getattr(resp, "usage", None)
        return LLMResponse(
            content=choice.message.content or "",
            model=model,
            tokens_in=getattr(usage, "prompt_tokens", 0) or 0,
            tokens_out=getattr(usage, "completion_tokens", 0) or 0,
            finish_reason=choice.finish_reason or "",
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
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        stream = self._client.chat.completions.create(**kwargs)
        for chunk in stream:
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
