"""阿里云百炼(DashScope)provider:OpenAI 兼容模式。"""

from __future__ import annotations

from app.core.llm.providers.openai_compat import OpenAICompatFamily

DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


class DashScopeFamily(OpenAICompatFamily):
    def __init__(self, api_key: str):
        super().__init__(name="dashscope", api_key=api_key, base_url=DASHSCOPE_BASE_URL)
