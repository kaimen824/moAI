"""GLM(智谱)provider:复用 OpenAI 兼容层。"""

from __future__ import annotations

from app.core.llm.providers.openai_compat import OpenAICompatFamily

GLM_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"


class GLMFamily(OpenAICompatFamily):
    def __init__(self, api_key: str):
        super().__init__(name="glm", api_key=api_key, base_url=GLM_BASE_URL)
