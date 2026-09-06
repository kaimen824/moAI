"""P1 验收:策略路由(角色 -> provider/model/温度)。"""

from __future__ import annotations

from app.core.config import AgentRole
from app.core.llm.router import ModelRouter, resolve_provider


def test_default_models_route_to_dashscope(isolated_settings):
    router = ModelRouter()
    route = router.route(AgentRole.WRITER)
    assert route.provider == "dashscope"
    assert route.model.startswith("qwen")
    assert router.route(AgentRole.EMBEDDING).provider == "dashscope"


def test_env_override_changes_route(monkeypatch, isolated_settings):
    monkeypatch.setenv("MODEL__WRITER", "glm-4.6")
    route = ModelRouter().route(AgentRole.WRITER)
    assert route.provider == "glm"
    monkeypatch.setenv("MODEL__WRITER", "gpt-4o-mini")
    assert ModelRouter().route(AgentRole.WRITER).provider == "openai"


def test_role_temperatures(isolated_settings):
    router = ModelRouter()
    assert router.route(AgentRole.EVENT).temperature < router.route(AgentRole.WRITER).temperature
    assert router.route(AgentRole.REVIEWER).temperature <= 0.5


def test_resolve_provider_unknown_defaults_openai():
    assert resolve_provider("some-model") == "openai"
    assert resolve_provider("glm-4.6") == "glm"
