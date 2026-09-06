"""LLM 接入层(ADR-0008):抽象工厂 + 策略路由 + 埋点装饰器。

三件套分工:
- factory  抽象工厂:按 provider 创建客户端族(chat + embedding)
- router   策略:Agent 角色 -> (provider, model, params)
- observed 装饰器:usage 埋点(经 sink 回调上抛,core 不依赖 db —— 依赖倒置)

Agent 侧唯一入口:`llm.chat(role=..., messages=...)`。
"""

from app.core.llm.base import ChatMessage, EmbeddingResult, LLMResponse, UsageRecord
from app.core.llm.facade import LLMFacade, llm

__all__ = [
    "ChatMessage",
    "EmbeddingResult",
    "LLMResponse",
    "UsageRecord",
    "LLMFacade",
    "llm",
]
