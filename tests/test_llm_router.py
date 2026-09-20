"""P1 验收:策略路由(角色 -> provider/model/温度)。"""

from __future__ import annotations

from app.core.config import AgentRole
from app.core.llm.router import ModelRouter, resolve_provider


def test_default_models_and_tiers(isolated_settings):
    router = ModelRouter()
    for role in (AgentRole.SUPERVISOR, AgentRole.OUTLINE, AgentRole.REVIEWER, AgentRole.WRITER):
        assert router.route(role).model == "glm-5"          # 强
    assert router.route(AgentRole.EVENT).model == "deepseek-v3"      # 中
    assert router.route(AgentRole.SUMMARY).model == "deepseek-v3"    # 便宜
    assert router.route(AgentRole.EMBEDDING).model == "qwen3.7-text-embedding"


def test_dashscope_key_routes_everything_to_bailian(monkeypatch, isolated_settings):
    """配了百炼 key:一切模型(含 glm-*)走百炼聚合。"""
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "dashscope_api_key", "sk-test", raising=False)
    router = ModelRouter()
    assert router.route(AgentRole.WRITER).provider == "dashscope"    # glm-5 经百炼


def test_no_dashscope_key_glm_goes_direct(monkeypatch, isolated_settings):
    """无百炼 key、有智谱 key:glm-* 直连智谱。"""
    from app.core.config import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "dashscope_api_key", "", raising=False)
    monkeypatch.setattr(s, "glm_api_key", "zpu-test", raising=False)
    assert ModelRouter().route(AgentRole.WRITER).provider == "glm"


def test_env_override_changes_route(monkeypatch, isolated_settings):
    monkeypatch.setenv("MODEL__WRITER", "gpt-4o-mini")
    route = ModelRouter().route(AgentRole.WRITER)
    assert route.provider == "openai"
    assert route.model == "gpt-4o-mini"


def test_role_temperatures(isolated_settings):
    router = ModelRouter()
    assert router.route(AgentRole.EVENT).temperature < router.route(AgentRole.WRITER).temperature
    assert router.route(AgentRole.REVIEWER).temperature <= 0.5


def test_resolve_provider_by_prefix():
    """纯前缀判定(生产路径中 key 可用性优先,见 ModelRouter)。"""
    assert resolve_provider("some-model") == "openai"
    assert resolve_provider("glm-4.6") == "dashscope"     # 百炼聚合
    assert resolve_provider("deepseek-v3") == "dashscope"
    assert resolve_provider("qwen3.7-text-embedding") == "dashscope"


def test_deepseek_official_key_routes_direct(monkeypatch, isolated_settings):
    """无百炼 key、有 DeepSeek 官方 key:deepseek-* 直连 api.deepseek.com(ADR-0033)。"""
    from app.core.config import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "dashscope_api_key", "", raising=False)
    monkeypatch.setattr(s, "deepseek_api_key", "sk-ds", raising=False)
    assert ModelRouter().route(AgentRole.SUMMARY).provider == "deepseek"
    # glm-* 不受 DeepSeek key 影响:无智谱 key 时按名字前缀落 dashscope(既有语义)
    assert ModelRouter().route(AgentRole.WRITER).provider == "dashscope"


def test_deepseek_direct_wins_over_bailian(monkeypatch, isolated_settings):
    """专属优先(ADR-0033,2026-09-20 拍板):官方 key 在,deepseek-* 直连
    官方,即使百炼 key 也配了;glm/qwen/MiniMax 系不受影响仍走百炼。"""
    from app.core.config import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "dashscope_api_key", "sk-bailian", raising=False)
    monkeypatch.setattr(s, "deepseek_api_key", "sk-ds", raising=False)
    assert ModelRouter().route(AgentRole.SUMMARY).provider == "deepseek"
    assert ModelRouter().route(AgentRole.WRITER).provider == "dashscope"   # glm-5 经百炼
    # deepseek-* 无官方 key:回落百炼聚合
    monkeypatch.setattr(s, "deepseek_api_key", "", raising=False)
    assert ModelRouter().route(AgentRole.SUMMARY).provider == "dashscope"


def test_deepseek_family_registered_with_key():
    """工厂:key 在则注册 deepseek 族;不在则无此 provider。"""
    import pytest

    from app.core.llm.factory import build_default_factory
    f1 = build_default_factory(glm_api_key="k", deepseek_api_key="sk-ds")
    assert "deepseek" in f1._registry.names()
    client = f1.chat_client("deepseek")
    assert client is f1.chat_client("deepseek")      # 缓存复用
    f2 = build_default_factory(glm_api_key="k")
    with pytest.raises(KeyError):
        f2.chat_client("deepseek")
