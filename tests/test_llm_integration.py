"""P1 验收:真实模型调用集成测试。

DASHSCOPE_API_KEY(百炼,默认路由)或 GLM_API_KEY 任一可用即运行;均未设置时跳过。
注意:本文件需要 .env 真实加载(绕过 conftest 的测试隔离)。
"""

from __future__ import annotations

import pytest

from app.core.config import AgentRole, get_settings, reset_settings
from app.core.llm.base import ChatMessage
from app.core.llm.facade import LLMFacade


def _has_key() -> bool:
    s = get_settings()
    return bool(s.dashscope_api_key or s.glm_api_key)


pytestmark = pytest.mark.skipif(not _has_key(), reason="DASHSCOPE/GLM API key 未设置,跳过集成测试")


@pytest.fixture(autouse=True)
def restore_dotenv(monkeypatch):
    """覆盖全局隔离:集成测试必须读到 .env 的真实 key。"""
    import os

    monkeypatch.delenv("NOVEL_NO_DOTENV", raising=False)
    reset_settings()
    get_settings()          # 重新加载 .env
    yield
    reset_settings()


def test_glm_chat_real():
    facade = LLMFacade(settings=get_settings())
    resp = facade.chat(
        AgentRole.SUMMARY,
        [ChatMessage("user", "回复两个字:收到")],
        stage="integration_test",
        max_tokens=16,
    )
    assert resp.content.strip()
    assert resp.model


def test_glm_embed_real():
    facade = LLMFacade(settings=get_settings())
    result = facade.embed(["这是一个用于测试的句子。"])
    assert len(result.vectors) == 1
    assert len(result.vectors[0]) >= 256   # embedding-3 输出维度检查(宽松)
