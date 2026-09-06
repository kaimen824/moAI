"""P0 验收:配置优先级(运行时覆盖 > 环境变量 > 默认值)与角色路由。"""

from __future__ import annotations

import pytest

from app.core.config import AgentRole, DEFAULT_MODELS, get_settings


def test_default_models_when_no_env(isolated_settings):
    s = get_settings()
    for role in AgentRole:
        assert s.model_for(role) == DEFAULT_MODELS[role]


def test_env_overrides_default(monkeypatch, isolated_settings):
    monkeypatch.setenv("MODEL__WRITER", "glm-x")
    s = get_settings()
    assert s.model_for(AgentRole.WRITER) == "glm-x"
    # 其他角色不受影响
    assert s.model_for(AgentRole.EVENT) == DEFAULT_MODELS[AgentRole.EVENT]


def test_runtime_overrides_env(monkeypatch, isolated_settings):
    monkeypatch.setenv("MODEL__WRITER", "from-env")
    s = get_settings()
    s.set_model_override(AgentRole.WRITER, "from-runtime")
    assert s.model_for(AgentRole.WRITER) == "from-runtime"


def test_override_context_manager(isolated_settings):
    s = get_settings()
    with s.with_overrides(WRITER="temp-model", REVIEWER="temp-model"):
        assert s.model_for(AgentRole.WRITER) == "temp-model"
        assert s.model_for(AgentRole.REVIEWER) == "temp-model"
    # 退出后恢复
    assert s.model_for(AgentRole.WRITER) == DEFAULT_MODELS[AgentRole.WRITER]


def test_role_accepts_string(isolated_settings):
    s = get_settings()
    assert s.model_for("WRITER") == DEFAULT_MODELS[AgentRole.WRITER]


def test_unknown_role_raises(isolated_settings):
    s = get_settings()
    with pytest.raises(ValueError):
        s.model_for("NOT_A_ROLE")
