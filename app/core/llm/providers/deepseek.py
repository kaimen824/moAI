"""DeepSeek 官方 provider:复用 OpenAI 兼容层(ADR-0012 扩展点)。

官方 API 兼容 OpenAI 格式,base_url https://api.deepseek.com;
模型名 deepseek-chat(通用)/ deepseek-reasoner(推理,输出思维链)。
注意:官方模型名与百炼聚合名(deepseek-v3)不同——走本 provider 时
用 MODEL__<ROLE> 环境变量或前端配置页指定官方名。
"""

from __future__ import annotations

from app.core.llm.providers.openai_compat import OpenAICompatFamily

DEEPSEEK_BASE_URL = "https://api.deepseek.com"


class DeepSeekFamily(OpenAICompatFamily):
    def __init__(self, api_key: str):
        super().__init__(name="deepseek", api_key=api_key,
                         base_url=DEEPSEEK_BASE_URL)
