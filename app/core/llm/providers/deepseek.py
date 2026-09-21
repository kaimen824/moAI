"""DeepSeek 官方 provider:复用 OpenAI 兼容层(ADR-0012 扩展点)。

官方 API 兼容 OpenAI 格式,base_url https://api.deepseek.com;/models 实查
(2026-09):deepseek-flash / deepseek-v4-pro / deepseek-chat。
V4 系双模**默认思考**——推理 token 挤占写作产出(ADR-0034 诊断:12385
out 中约 1 万是推理,散文仅 2222 字)。settings.deepseek_disable_thinking
开启时全部 chat 调用附 thinking=disabled(官方 Chat Completions 口径)。
"""

from __future__ import annotations

from app.core.config import get_settings
from app.core.llm.providers.openai_compat import (
    OpenAICompatChat,
    OpenAICompatFamily,
)

DEEPSEEK_BASE_URL = "https://api.deepseek.com"


class DeepSeekChat(OpenAICompatChat):
    """thinking 开关注入点:flag 开时请求体附 thinking disabled。

    chat 与 stream 都要覆盖——writer 在有 SSE 订阅者时走 stream 路径。
    """

    _disable_thinking = False

    def _body(self) -> dict | None:
        return {"thinking": {"type": "disabled"}} if self._disable_thinking else None

    def chat(self, model, messages, *, temperature=0.7, max_tokens=None,
             response_format=None, tools=None):
        return super().chat(model, messages, temperature=temperature,
                            max_tokens=max_tokens,
                            response_format=response_format, tools=tools,
                            extra_body=self._body())

    def stream(self, model, messages, *, temperature=0.7, max_tokens=None):
        return super().stream(model, messages, temperature=temperature,
                              max_tokens=max_tokens, extra_body=self._body())


class DeepSeekFamily(OpenAICompatFamily):
    def __init__(self, api_key: str):
        super().__init__(name="deepseek", api_key=api_key,
                         base_url=DEEPSEEK_BASE_URL)

    def create_chat_client(self) -> DeepSeekChat:
        client = DeepSeekChat(self._api_key, self._base_url)
        client._disable_thinking = get_settings().deepseek_disable_thinking
        return client
