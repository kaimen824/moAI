"""P1 验收:GLM 真实调用集成测试。

需要环境变量 GLM_API_KEY;未设置时整组跳过(mark.skipif)。
模型名可通过 MODEL__SUMMARY 等覆盖(默认值若在账号上不可用)。
"""

from __future__ import annotations

import os

import pytest

from app.core.config import AgentRole, get_settings
from app.core.llm.base import ChatMessage
from app.core.llm.facade import LLMFacade

pytestmark = pytest.mark.skipif(
    not os.environ.get("GLM_API_KEY"), reason="GLM_API_KEY 未设置,跳过集成测试"
)


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
