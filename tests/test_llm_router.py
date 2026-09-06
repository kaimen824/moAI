"""P1 验收:策略路由(角色 -> provider/model/温度)。"""

from __future__ import annotations

from app.core.config import AgentRole
from app.core.llm.router import ModelRouter, resolve_provider


def test_glm_models_route_to_glm_provider(isolated_settings):
    router = ModelRouter()
    route = router.route(AgentRole.WRITER)
    assert route.provider == "glm"
    assert route.model.startswith("glm")


def test_env_override_changes_route(monkeypatch, isolated_settings):
    monkeypatch.setenv("MODEL__WRITER", "gpt-4o-mini")
    route = ModelRouter().route(AgentRole.WRITER)
    assert route.provider == "openai"
    assert route.model == "gpt-4o-mini"


def test_role_temperatures(isolated_settings):
    router = ModelRouter()
    assert router.route(AgentRole.EVENT).temperature < router.route(AgentRole.WRITER).temperature
    assert router.route(AgentRole.REVIEWER).temperature <= 0.5


def test_resolve_provider_unknown_defaults_openai():
    assert resolve_provider("some-model") == "openai"
    assert resolve_provider("glm-4.6") == "glm"
