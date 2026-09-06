"""P1 验收:Facade 唯一入口 + usage 埋点(fake 客户端,不调真实 API)。"""

from __future__ import annotations

import json
from typing import Sequence

from app.core.config import AgentRole
from app.core.llm.base import (
    ChatClient,
    ChatMessage,
    EmbedClient,
    EmbeddingResult,
    LLMResponse,
    ProviderFamily,
)
from app.core.llm.facade import LLMFacade
from app.observability.usage_log import make_usage_sink


class FakeChat(ChatClient):
    def __init__(self):
        self.calls: list[dict] = []

    def chat(self, model, messages, *, temperature=0.7, max_tokens=None, response_format=None):
        self.calls.append({"model": model, "temperature": temperature, "n": len(messages)})
        return LLMResponse(content=json.dumps({"ok": True}), model=model,
                           tokens_in=10, tokens_out=5)

    def stream(self, model, messages, *, temperature=0.7, max_tokens=None):
        for token in ["你好", "世界"]:
            yield token


class FakeEmbed(EmbedClient):
    def embed(self, model, texts: Sequence[str]):
        return EmbeddingResult(vectors=[[0.1, 0.2] for _ in texts], model=model, tokens_in=len(texts))


class FakeFamily(ProviderFamily):
    name = "dashscope"   # 冒充默认 provider,让路由命中

    def __init__(self):
        self.chat = FakeChat()
        self.embed = FakeEmbed()

    def create_chat_client(self):
        return self.chat

    def create_embed_client(self):
        return self.embed


def make_facade(db) -> tuple[LLMFacade, FakeFamily]:
    family = FakeFamily()
    from app.core.llm.factory import ProviderFactory, ProviderRegistry

    registry = ProviderRegistry()
    registry.register(family)
    facade = LLMFacade(factory=ProviderFactory(registry))
    facade.set_usage_sink(make_usage_sink(db))
    return facade, family


def test_chat_emits_usage_to_sink(db, isolated_settings):
    facade, family = make_facade(db)
    resp = facade.chat(AgentRole.WRITER, [ChatMessage("user", "写一句")], stage="draft")
    assert resp.content
    row = db.execute("SELECT * FROM usage_log").fetchone()
    assert row["agent"] == "WRITER"
    assert row["model"] == "glm-5"
    assert row["tokens_in"] == 10 and row["tokens_out"] == 5
    assert row["stage"] == "draft"
    assert row["latency_ms"] >= 0


def test_stream_emits_usage_after_consumption(db, isolated_settings):
    facade, _ = make_facade(db)
    chunks = list(facade.stream(AgentRole.WRITER, [ChatMessage("user", "hi")], stage="draft"))
    assert chunks == ["你好", "世界"]
    row = db.execute("SELECT * FROM usage_log").fetchone()
    assert row["agent"] == "WRITER"


def test_embed_emits_usage(db, isolated_settings):
    facade, _ = make_facade(db)
    result = facade.embed(["文本一", "文本二"])
    assert len(result.vectors) == 2
    row = db.execute("SELECT * FROM usage_log").fetchone()
    assert row["agent"] == "EMBEDDING"
    assert row["tokens_in"] == 2


def test_sink_failure_does_not_break_call(db, isolated_settings):
    facade, _ = make_facade(db)
    facade.set_usage_sink(lambda r: (_ for _ in ()).throw(RuntimeError("db down")))
    resp = facade.chat(AgentRole.WRITER, [ChatMessage("user", "hi")])
    assert resp.content  # 埋点失败,主流程不受影响


def test_role_routing_reaches_client(db, isolated_settings):
    facade, family = make_facade(db)
    facade.chat(AgentRole.EVENT, [ChatMessage("user", "抽取")])
    assert family.chat.calls[0]["temperature"] < 0.5   # 抽取走低温
