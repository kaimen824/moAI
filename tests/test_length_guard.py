"""字数下限三层守卫(ADR-0034):writer 自查补写 / 评审契约标注 / merge 兜底。

conftest 默认 chapter_min_chars=0(既有测试零回归);本文件显式设阈值。
背景(《古真神》实查):MiniMax 平均 ~3300 字 → v4-pro 默认思考,散文
3711→3031→2222 逐章缩水,评审无篇幅维度照样 pass。
"""

from __future__ import annotations

import pytest
from app.core.config import get_settings
from app.core.llm.base import LLMResponse
from app.core.llm.facade import LLMFacade
from app.graph.agents.writer import WriterNode
from app.graph.nodes import struct_merge
from app.graph.runtime import build_engine

R = LLMResponse


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "chapter_min_chars", 100, raising=False)
    deps, conn = build_engine(tmp_path / "guard.db", llm=LLMFacade())
    yield deps, conn
    conn.close()


def test_writer_self_retry_on_short_draft(env):
    """短稿 → 带反馈补写一次,取达标二稿。"""
    deps, conn = env
    calls = {"draft": 0}
    long_text = "沈砚在暴雨中前行。" * 12      # 204 字 > 100 下限

    def override(stage: str):
        if stage != "draft":
            return None
        calls["draft"] += 1
        text = "太短了" if calls["draft"] == 1 else long_text
        return R(content=text, model="fake")

    deps.llm._response_override = override
    node = WriterNode(deps.llm)
    out = node({"story_id": "s1", "chapter_no": 1, "rewrite_count": 0,
                "context_bundle": {}}, deps)
    assert calls["draft"] == 2                    # 自查补写恰好一次
    assert len(out["draft"]) >= 100


def test_writer_keeps_first_when_retry_not_longer(env):
    """补写稿不比一稿长(仍短/更短)→ 保留一稿进评审,由 merge 兜底。"""
    deps, conn = env
    calls = {"draft": 0}

    def override(stage: str):
        if stage != "draft":
            return None
        calls["draft"] += 1
        return R(content="短稿" if calls["draft"] == 1 else "更", model="fake")

    deps.llm._response_override = override
    node = WriterNode(deps.llm)
    out = node({"story_id": "s1", "chapter_no": 1, "rewrite_count": 0,
                "context_bundle": {}}, deps)
    assert out["draft"] == "短稿"


def test_struct_gate_forces_revise_on_short_draft_despite_pass(env):
    """结构评审 pass 但草稿低于下限 → 结构闸强制 revise,feedback 带实际字数
    (ADR-0036 串行化后字数兜底从 merge 移入结构闸——字数是内容问题)。"""
    deps, conn = env
    state = {"story_id": "s1", "draft": "短", "rewrite_count": 0,
             "outline_review": {"verdict": "pass"}}
    out = struct_merge(state, deps)
    assert out["struct_verdict"] == "revise"
    assert out["rewrite_count"] == 1
    row = conn.execute(
        "SELECT feedback FROM review_results ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    assert "字数不足" in row["feedback"]


def test_struct_gate_pass_unchanged_when_floor_disabled(env, monkeypatch):
    """floor=0(测试隔离口径):结构 pass 不受守卫影响。"""
    deps, conn = env
    monkeypatch.setattr(get_settings(), "chapter_min_chars", 0, raising=False)
    state = {"story_id": "s1", "draft": "短", "rewrite_count": 0,
             "outline_review": {"verdict": "pass"}}
    assert struct_merge(state, deps)["struct_verdict"] == "pass"


def test_reviewer_prompt_carries_length_note(env):
    """评审输入携带[字数下限]标注(软约束层可见)。"""
    deps, conn = env
    from app.graph.agents.quality_reviewer import QualityReviewNode
    captured = {}

    def override(stage: str):
        if stage != "review_quality":
            return None
        return R(content='{"verdict":"pass","scores":{"consistency":9,'
                         '"foreshadow":9,"style":9},"fix_scope":"style",'
                         '"feedback":"ok"}', model="fake")

    deps.llm._response_override = override
    orig = deps.llm.chat

    def spy(role, messages, **kw):
        captured["user"] = messages[-1].content
        return orig(role, messages, **kw)

    deps.llm.chat = spy
    QualityReviewNode(deps.llm)(
        {"story_id": "s1", "draft": "短", "context_bundle": {}}, deps)
    assert "[字数下限:100 字" in captured["user"]
    assert "实际 1 字" in captured["user"]


def test_reviewer_prompt_carries_edit_receipt(env):
    """上轮编辑回执进入评审输入(ADR-0040 复检防翻旧账)。"""
    deps, conn = env
    from app.graph.agents.quality_reviewer import QualityReviewNode
    captured = {}

    def override(stage: str):
        if stage != "review_quality":
            return None
        return R(content='{"verdict":"pass","scores":{"consistency":9,'
                         '"foreshadow":9,"style":9},"fix_scope":"style",'
                         '"feedback":"ok"}', model="fake")

    deps.llm._response_override = override
    orig = deps.llm.chat

    def spy(role, messages, **kw):
        captured["user"] = messages[-1].content
        return orig(role, messages, **kw)

    deps.llm.chat = spy
    QualityReviewNode(deps.llm)(
        {"story_id": "s1", "draft": "正文", "context_bundle": {},
         "last_polish_edits": [{"find": "昏黄的灯", "replace": "暖黄的灯"}]},
        deps)
    assert "[上轮已套用编辑" in captured["user"]
    assert "昏黄的灯" in captured["user"]
