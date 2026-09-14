"""批次 5(run 状态持久化 + run_id 贯通)回归:评审 6.4/6.13 + ADR-0027。

- generate/resume 全程写入 story_run_state:running -> waiting(payload 落库)-> idle
- run-state 端点:DB 为主、_active 为辅(等待+在跑→running;running 不在跑→idle);
  中断卡数据优先 DB payload,事件快照兜底
- 启动收敛:进程崩溃残留 running → idle;waiting 行保留(中断卡跨重启不丢)
- run_id 贯通 usage_log / agent_traces / review_results / story_run_state
"""

from __future__ import annotations

import json

import app.main as main
import tests.test_graph_e2e as replay
from app.core.llm.base import LLMResponse
from tests.test_api import client, parse_sse, run_until   # noqa: F401  (复用夹具)
from app.core.llm.facade import LLMFacade
from app.graph.runtime import build_engine


def _state(conn, sid):
    return conn.execute(
        "SELECT * FROM story_run_state WHERE story_id=?", (sid,)).fetchone()


# ---------- 写入点:running / waiting / idle ----------

def test_generate_interrupt_persists_waiting(client):
    c, deps = client
    sid = c.post("/stories", json={"title": "运行状态", "premise": "测试"}).json()["story_id"]
    run_until(c, f"/stories/{sid}/generate",
              {"target_chapters": 1, "initial_input": "东方奇幻"},
              "confirm_master_outline")
    row = _state(deps.conn, sid)
    assert row["status"] == "waiting"
    assert row["interrupt_type"] == "confirm_master_outline"
    payload = json.loads(row["interrupt_payload"])          # 完整可解析(还原中断卡)
    assert payload["type"] == "confirm_master_outline" and payload["outline"]
    assert row["run_id"]                                    # 批次 2 的 run_id 落库
    assert row["target_chapters"] == 1
    assert row["started_at"] and row["updated_at"]


def test_resume_chain_transitions_to_idle(client):
    c, deps = client
    sid = c.post("/stories", json={"title": "状态链", "premise": "测试"}).json()["story_id"]
    run_until(c, f"/stories/{sid}/generate", {"target_chapters": 1}, "confirm_master_outline")
    run_until(c, f"/stories/{sid}/resume", {"action": "confirm"}, "confirm_stage_outline")
    assert _state(deps.conn, sid)["status"] == "waiting"
    run_until(c, f"/stories/{sid}/resume", {"action": "confirm"}, "user_review_chapter")
    assert _state(deps.conn, sid)["status"] == "waiting"
    r = c.post(f"/stories/{sid}/resume",
               json={"action": "confirm", "threads": []})
    assert any(k == "done" for k, _ in parse_sse(r.text))
    row = _state(deps.conn, sid)
    assert row["status"] == "idle" and row["interrupt_type"] is None
    assert row["interrupt_payload"] is None                 # 完成后中断卡清空


def test_error_persists_error_code(client):
    c, deps = client
    sid = c.post("/stories", json={"title": "错误落库", "premise": "测试"}).json()["story_id"]
    # 契约节点持续产出非法 JSON:ask_json 双败 -> LLMFormatError -> worker 落错误码
    def bad(stage: str):
        if stage == "capability_contract":
            return LLMResponse(content="依然不是 JSON", model="fake")
        return replay.override(stage)
    deps.llm._response_override = bad
    r = c.post(f"/stories/{sid}/generate", json={"target_chapters": 1})
    events = parse_sse(r.text)
    assert any(k == "error" for k, _ in events)
    row = _state(deps.conn, sid)
    assert row["status"] == "idle"
    assert row["error_code"] == "llm_format"
    assert row["error_stage"]                               # 异常自带 stage


# ---------- run-state 端点:DB 为主、_active 为辅 ----------

def test_run_state_endpoint_waiting_from_db(client):
    c, deps = client
    sid = c.post("/stories", json={"title": "端点恢复", "premise": "测试"}).json()["story_id"]
    run_until(c, f"/stories/{sid}/generate", {"target_chapters": 1}, "confirm_master_outline")
    body = c.get(f"/stories/{sid}/run-state").json()
    assert body["status"] == "waiting"
    assert body["interrupt"]["type"] == "confirm_master_outline"
    assert body["run_id"] == _state(deps.conn, sid)["run_id"]
    assert any(e["kind"] == "interrupt" for e in body["events"])


def test_run_state_endpoint_db_priority_over_snapshot(client):
    """DB idle 优先于事件快照里的旧 interrupt(修复:完成后误报 waiting)。"""
    c, deps = client
    sid = c.post("/stories", json={"title": "快照纠偏", "premise": "测试"}).json()["story_id"]
    run_until(c, f"/stories/{sid}/generate", {"target_chapters": 1}, "confirm_master_outline")
    run_until(c, f"/stories/{sid}/resume", {"action": "confirm"}, "confirm_stage_outline")
    run_until(c, f"/stories/{sid}/resume", {"action": "confirm"}, "user_review_chapter")
    r = c.post(f"/stories/{sid}/resume", json={"action": "confirm", "threads": []})
    assert any(k == "done" for k, _ in parse_sse(r.text))
    # 事件快照仍含旧 interrupt 事件(下次 generate 才 clear),但 DB 已 idle
    body = c.get(f"/stories/{sid}/run-state").json()
    assert body["status"] == "idle"
    assert body["interrupt"] is None                        # 不再拿旧卡误报


def test_run_state_endpoint_merges_active_and_stale(client):
    """读时兜底合并:waiting+在跑→running;running 不在跑(崩溃)→idle。

    手动构造状态而非跑真实 generate:worker finally 的 _active.discard 与
    测试线程的 _active.add 存在竞态,绕开 worker 生命周期。
    """
    c, deps = client
    sid = c.post("/stories", json={"title": "合并逻辑", "premise": "测试"}).json()["story_id"]
    deps.set_run_state(sid, status="waiting", run_id="r1",
                       interrupt_type="confirm_master_outline",
                       interrupt_payload='{"type": "confirm_master_outline"}')
    # 场景 1:DB waiting 但用户已 resume(worker 刚启动未覆写)→ running
    main._active.add(sid)
    body = c.get(f"/stories/{sid}/run-state").json()
    assert body["status"] == "running"
    # 场景 2:DB running 但进程已不在跑(崩溃残留)→ idle
    main._active.discard(sid)
    deps.set_run_state(sid, status="running", run_id="ghost")
    body = c.get(f"/stories/{sid}/run-state").json()
    assert body["status"] == "idle"


# ---------- 启动收敛(崩溃残留)----------

def test_startup_convergence_resets_running_keeps_waiting(tmp_path):
    facade = LLMFacade(response_override=replay.override)
    deps, conn = build_engine(tmp_path / "crash.db", llm=facade)
    sid, _branch = deps.repo.create_story("崩溃收敛", "测试")
    # 崩溃前:last run 已把行覆写为 running(同一 story 单行 upsert)
    deps.set_run_state(sid, status="waiting", run_id="r1",
                       interrupt_type="user_review_chapter",
                       interrupt_payload='{"type": "user_review_chapter"}')
    deps.set_run_state(sid, status="running", run_id="r2")
    # 进程重启:重建引擎 -> running 残留收敛为 idle
    _deps2, conn2 = build_engine(tmp_path / "crash.db", llm=facade)
    row = conn2.execute(
        "SELECT * FROM story_run_state WHERE story_id=?", (sid,)).fetchone()
    assert row["status"] == "idle"
    assert row["interrupt_type"] is None and row["interrupt_payload"] is None
    assert row["run_id"] == "r2"            # run_id 保留(可回溯崩溃时的 run)


def test_startup_convergence_preserves_waiting(tmp_path):
    """waiting 行(用户未裁决的中断卡)重启后必须原样保留——刷新/重启不丢卡。"""
    facade = LLMFacade(response_override=replay.override)
    deps, _conn = build_engine(tmp_path / "keep.db", llm=facade)
    sid, _branch = deps.repo.create_story("保留等待", "测试")
    deps.set_run_state(sid, status="waiting", run_id="r9",
                       interrupt_type="confirm_master_outline",
                       interrupt_payload='{"type": "confirm_master_outline"}')
    _deps2, conn2 = build_engine(tmp_path / "keep.db", llm=facade)
    row = conn2.execute(
        "SELECT * FROM story_run_state WHERE story_id=?", (sid,)).fetchone()
    assert row["status"] == "waiting"
    assert row["interrupt_type"] == "confirm_master_outline"
    assert json.loads(row["interrupt_payload"])["type"] == "confirm_master_outline"


# ---------- run_id 贯通(usage_log / agent_traces / review_results)----------

def test_run_id_flows_through_ledger_tables(client):
    c, deps = client
    sid = c.post("/stories", json={"title": "贯通验证", "premise": "测试"}).json()["story_id"]
    run_until(c, f"/stories/{sid}/generate", {"target_chapters": 1}, "confirm_master_outline")
    run_until(c, f"/stories/{sid}/resume", {"action": "confirm"}, "confirm_stage_outline")
    run_until(c, f"/stories/{sid}/resume", {"action": "confirm"}, "user_review_chapter")
    c.post(f"/stories/{sid}/resume", json={"action": "confirm", "threads": []})

    for table in ("usage_log", "agent_traces", "review_results"):
        total = deps.conn.execute(
            f"SELECT COUNT(*) c FROM {table} WHERE story_id=?", (sid,)).fetchone()["c"]
        with_run = deps.conn.execute(
            f"SELECT COUNT(*) c FROM {table} WHERE story_id=? AND run_id IS NOT NULL",
            (sid,)).fetchone()["c"]
        assert total > 0 and with_run == total, f"{table}: {with_run}/{total} 缺 run_id"

    # 同一次 LLM 调用双 sink 同 run_id:按 trace_id 关联交叉校验
    mismatch = deps.conn.execute(
        "SELECT COUNT(*) c FROM usage_log u JOIN agent_traces a"
        " ON u.trace_id = a.trace_id"
        " WHERE u.story_id=? AND u.run_id IS NOT a.run_id", (sid,)).fetchone()["c"]
    assert mismatch == 0
    assert _state(deps.conn, sid)["run_id"]                 # 第四表:状态表本身
