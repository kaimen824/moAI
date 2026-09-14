"""批次 4(LLM 强契约与弹性)回归:评审 6.9/6.10 + ADR-0026。

- parse_json_loose 加固:代码栅栏/前后杂文容忍,截断输出抛错留痕
- ask_json schema 校验 + 错误回喂自纠一次 + 仍失败 LLMFormatError(trace_id)
- 节点包装层统一落 llm_failures 台账;SSE error 携带 error_code/trace_id
- 评审类节点解析失败安全降级(quality→revise,thread→零账本动作),绝不静默 pass
- 弹性:provider client 缓存复用 + 显式 timeout/max_retries;
  target_chapters 边界 422;每日 token 预算 429
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import app.main as main
import tests.test_graph_e2e as replay
from app.core.config import AgentRole
from tests.test_api import client   # noqa: F401  (复用认证 client 夹具)
from app.core.llm.base import LLMResponse
from app.core.llm.factory import build_default_factory
from app.core.llm.facade import LLMFacade
from app.graph import build
from app.graph.agents.base import BaseAgent, LLMFormatError, parse_json_loose
from app.graph.agents.quality_reviewer import QualityReviewNode
from app.graph.agents.schemas import FactChanges, ReviewVerdict
from app.graph.agents.thread_reviewer import ThreadReviewNode
from app.graph.runtime import build_engine

R = LLMResponse


# ---------- parse_json_loose 加固 ----------

def test_parse_json_loose_tolerates_fence_and_prose():
    assert parse_json_loose('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_loose('好的,以下是结果:\n{"a": {"b": 2}}\n以上。') == {"a": {"b": 2}}


def test_parse_json_loose_rejects_truncated_json():
    with pytest.raises(json.JSONDecodeError):
        parse_json_loose('{"facts": [{"content": "截断了')   # 无完整对象


# ---------- ask_json schema 校验 + 自纠 ----------

class _Probe(BaseAgent):
    name = "probe"
    role = AgentRole.REVIEWER

    def __call__(self, state, deps):   # pragma: no cover - 直调 ask_json,不经图
        return {}


def test_ask_json_self_corrects_on_schema_violation():
    calls = {"n": 0}
    bad = json.dumps({"verdict": "PASS"})   # 枚举违规(大写)
    good = json.dumps({"verdict": "pass", "scores": {"consistency": 9, "structure": 9},
                       "feedback": "ok"}, ensure_ascii=False)

    def ov(stage: str):
        calls["n"] += 1
        return R(content=bad if calls["n"] == 1 else good, model="fake")

    probe = _Probe(LLMFacade(response_override=ov))
    out = probe.ask_json("s", "u", stage="review_quality", schema=ReviewVerdict)
    assert out["verdict"] == "pass" and out["scores"]["consistency"] == 9
    assert calls["n"] == 2                       # 校验错误回喂,自纠重试恰好一次


def test_ask_json_raises_llm_format_error_after_retry():
    calls = {"n": 0}

    def ov(stage: str):
        calls["n"] += 1
        return R(content=f"依然不是 JSON 第{calls['n']}次", model="fake")

    probe = _Probe(LLMFacade(response_override=ov))
    with pytest.raises(LLMFormatError) as ei:
        probe.ask_json("s", "u", stage="extract_facts", schema=FactChanges)
    assert calls["n"] == 2
    assert ei.value.error_code == "llm_format"
    assert ei.value.trace_id
    assert "第2次" in ei.value.raw_output        # 原始输出留痕(最后一次)


# ---------- 失败台账 + 节点包装层 ----------

def test_node_wrapper_logs_llm_failure(tmp_path):
    facade = LLMFacade(response_override=lambda s: None)
    deps, conn = build_engine(tmp_path / "n.db", llm=facade)

    def boom(state, deps):   # 与图节点同构:(state, deps)
        raise LLMFormatError(stage="extract_facts", raw="坏输出原文", error="no json")

    with pytest.raises(LLMFormatError):
        build._node(boom, deps)({"story_id": "s1"})
    row = conn.execute(
        "SELECT stage, node, raw_output, error, trace_id, story_id"
        " FROM llm_failures").fetchone()
    assert row["stage"] == "extract_facts"
    assert row["node"] == "boom"
    assert row["raw_output"] == "坏输出原文"
    assert row["trace_id"] and row["story_id"] == "s1"


# ---------- 评审节点安全降级(绝不静默 pass)----------

def test_quality_review_degrades_to_revise(tmp_path):
    facade = LLMFacade(response_override=lambda s: R(content="不是 JSON", model="fake"))
    deps, conn = build_engine(tmp_path / "q.db", llm=facade)
    story_id, _branch = deps.repo.create_story("质量降级", "测试")
    node = QualityReviewNode(facade)
    out = node({"story_id": story_id, "chapter_no": 1, "draft": "正文",
                "rewrite_count": 0, "context_bundle": {}}, deps)
    review = out["quality_review"]
    assert review["verdict"] == "revise"          # 安全默认返工,绝非 pass
    assert review["fix_scope"] == "content"
    assert "trace=" in review["feedback"]
    assert conn.execute("SELECT COUNT(*) c FROM llm_failures"
                        " WHERE stage='review_quality'").fetchone()["c"] == 1
    # 降级裁决同样进 review_results 审计(不产生无记录的静默轮次)
    assert conn.execute("SELECT COUNT(*) c FROM review_results").fetchone()["c"] == 1


def test_thread_review_degrades_to_noop(tmp_path):
    facade = LLMFacade(response_override=lambda s: R(content="[{[截断", model="fake"))
    deps, conn = build_engine(tmp_path / "t.db", llm=facade)
    story_id, _branch = deps.repo.create_story("伏笔降级", "测试")
    node = ThreadReviewNode(facade)
    out = node({"story_id": story_id, "chapter_no": 1, "draft": "正文",
                "rewrite_count": 0, "context_bundle": {"active_threads": []}}, deps)
    assert out["thread_review"]["thread_changes"] == []   # 零账本动作,不虚构变更
    assert out["thread_review"]["reviews"] == []
    assert conn.execute("SELECT COUNT(*) c FROM llm_failures"
                        " WHERE stage='review_threads'").fetchone()["c"] == 1


# ---------- 弹性(6.10)----------

def test_provider_client_cached_with_timeout_and_retries():
    factory = build_default_factory(glm_api_key="k", dashscope_api_key="")
    c1 = factory.chat_client("glm")
    assert factory.chat_client("glm") is c1       # 按 provider 缓存复用
    assert c1._client.timeout == 120.0            # 显式超时(默认)
    assert c1._client.max_retries == 3            # SDK 内置 429/5xx 退避,显式化


def test_generate_target_chapters_rejected_out_of_range(client):
    c, _deps = client
    story_id = c.post("/stories", json={"title": "边界", "premise": "测试"}).json()["story_id"]
    r = c.post(f"/stories/{story_id}/generate", json={"target_chapters": 0})
    assert r.status_code == 422
    r = c.post(f"/stories/{story_id}/generate", json={"target_chapters": 51})
    assert r.status_code == 422
    r = c.post(f"/stories/{story_id}/generate", json={"target_chapters": 50})
    assert r.status_code == 200                   # 上限内放行


def test_daily_budget_returns_429(client, monkeypatch):
    c, deps = client
    story_id = c.post("/stories", json={"title": "预算", "premise": "测试"}).json()["story_id"]
    # 今日已用 token 达到 story 级预算(1000)
    monkeypatch.setattr(main.get_settings(), "story_daily_token_budget", 1000,
                        raising=False)
    deps.conn.execute(
        "INSERT INTO usage_log (id, story_id, agent, model, tokens_in, tokens_out,"
        " cached_tokens, latency_ms, trace_id, stage, created_at)"
        " VALUES ('u1', ?, 'writer', 'm', 400, 600, 0, 5, 't', 'draft',"
        " strftime('%Y-%m-%dT%H:%M:%SZ','now'))", (story_id,))
    deps.conn.commit()
    r = c.post(f"/stories/{story_id}/generate", json={"target_chapters": 1})
    assert r.status_code == 429
    assert "预算" in r.text
    # 无消费的另一 story 不受影响(story 级隔离验证)
    other = c.post("/stories", json={"title": "预算外", "premise": "测试"}).json()["story_id"]
    r = c.post(f"/stories/{other}/generate", json={"target_chapters": 1})
    assert r.status_code == 200
