"""批次 6(可观测性补全)回归:评审 6.13 + ADR-0028。

- usage_log/agent_traces 补 user_id(worker user_ctx 注入,按触发用户归因)
- llm_failures 补 provider_status_code/retry_count;通用节点异常同样落台账
- worker 异常服务端 logging.exception 全栈;SSE error 结构化
  (error_code/run_id/trace_id/message 截断)
- /admin/stats:权限 + 数值口径
"""

from __future__ import annotations

import logging
import re

import pytest

import tests.test_graph_e2e as replay
from app.core.config import AgentRole
from app.core.llm.base import LLMResponse
from app.core.llm.facade import LLMFacade
from app.graph.agents.base import BaseAgent, LLMFormatError
from app.graph.agents.schemas import ReviewVerdict
from app.graph import build
from app.graph.runtime import build_engine
from tests.test_api import client, login, parse_sse, run_until   # noqa: F401

R = LLMResponse


def _full_run(c, sid: str) -> None:
    """HTTP 全流程跑到 done(1 章)。"""
    run_until(c, f"/stories/{sid}/generate",
              {"target_chapters": 1, "initial_input": "东方奇幻"},
              "confirm_master_outline")
    run_until(c, f"/stories/{sid}/resume", {"action": "confirm"},
              "confirm_stage_outline")
    run_until(c, f"/stories/{sid}/resume", {"action": "confirm"},
              "user_review_chapter")
    r = c.post(f"/stories/{sid}/resume", json={"action": "confirm", "threads": []})
    assert any(k == "done" for k, _ in parse_sse(r.text))


# ---------- user_id 贯通 ----------

def test_usage_and_traces_carry_user_id(client):
    c, deps = client
    sid = c.post("/stories", json={"title": "用户归因", "premise": "测试"}).json()["story_id"]
    _full_run(c, sid)
    admin_id = deps.conn.execute(
        "SELECT id FROM users WHERE username='admin'").fetchone()["id"]
    for table in ("usage_log", "agent_traces"):
        total = deps.conn.execute(
            f"SELECT COUNT(*) c FROM {table} WHERE story_id=?", (sid,)).fetchone()["c"]
        matched = deps.conn.execute(
            f"SELECT COUNT(*) c FROM {table} WHERE story_id=? AND user_id=?",
            (sid, admin_id)).fetchone()["c"]
        assert total > 0 and matched == total, f"{table}: {matched}/{total} 缺 user_id"


# ---------- llm_failures 补列 ----------

def test_llm_format_error_carries_retry_count():
    calls = {"n": 0}

    def ov(stage: str):
        calls["n"] += 1
        return R(content=f"第{calls['n']}次都不是 JSON", model="fake")

    class _Probe(BaseAgent):
        name = "probe"
        role = AgentRole.REVIEWER

        def __call__(self, state, deps):   # pragma: no cover
            return {}

    probe = _Probe(LLMFacade(response_override=ov))
    with pytest.raises(LLMFormatError) as ei:
        probe.ask_json("s", "u", stage="x", schema=ReviewVerdict)
    assert calls["n"] == 2
    assert ei.value.retry_count == 1            # 自纠重试恰好一次(应用内口径)
    assert ei.value.status_code is None         # 非 provider 错误,无 HTTP 状态


def test_generic_node_exception_lands_in_ledger(tmp_path):
    """provider 429/超时等非坏-JSON 异常也留痕(provider_status_code 取异常自带属性)。"""
    class _ProviderError(Exception):
        status_code = 429

    facade = LLMFacade(response_override=lambda s: None)
    deps, conn = build_engine(tmp_path / "obs.db", llm=facade)

    def boom(state, deps):
        raise _ProviderError("rate limited")

    with pytest.raises(_ProviderError):
        build._node(boom, deps)({"story_id": "s1"})
    row = conn.execute(
        "SELECT node, error, provider_status_code, retry_count"
        " FROM llm_failures").fetchone()
    assert row["node"] == "boom"
    assert row["provider_status_code"] == 429
    assert row["retry_count"] is None           # SDK 内部退避不虚报


# ---------- SSE error 结构化 + 服务端全栈日志 ----------

def test_sse_error_structured_and_server_logged(client, caplog):
    c, deps = client
    sid = c.post("/stories", json={"title": "结构化错误", "premise": "测试"}).json()["story_id"]

    def bad(stage: str):
        if stage == "capability_contract":
            return R(content="依然不是 JSON", model="fake")
        return replay.override(stage)
    deps.llm._response_override = bad

    with caplog.at_level(logging.ERROR, logger="novel.agent"):
        r = c.post(f"/stories/{sid}/generate", json={"target_chapters": 1})
    err = [d for k, d in parse_sse(r.text) if k == "error"][0]
    assert err["error_code"] == "llm_format"
    assert re.fullmatch(r"[0-9a-f]{32}", err["run_id"])      # run_id 可关联四表
    assert err.get("trace_id")                               # 与台账/审计关联
    assert len(err["message"]) <= 300                        # 脱敏截断,不发全栈
    assert any("run failed" in rec.message and rec.levelno == logging.ERROR
               for rec in caplog.records)                    # 服务端有全栈诊断


# ---------- /admin/stats ----------

def test_admin_stats_numbers_and_permission(client):
    c, deps = client
    sid = c.post("/stories", json={"title": "统计口径", "premise": "测试"}).json()["story_id"]
    _full_run(c, sid)

    r = c.get("/admin/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["llm_calls_24h"] > 0
    assert 0 <= body["llm_failure_rate_24h"] < 1
    assert body["active_runs"] == 0                  # run 已结束
    assert body["waiting_interruptions"] == 0        # 无挂起中断
    assert any(row["story_id"] == sid for row in body["token_cost"])
    assert all({"day", "story_id", "owner_id", "tokens_in", "tokens_out"}
               <= set(row) for row in body["token_cost"])

    # 非 admin 403
    c.post("/admin/users", json={"username": "viewer_obs", "password": "password123",
                                 "role": "user"})
    token = login(c, "viewer_obs", "password123")
    r = c.get("/admin/stats", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
