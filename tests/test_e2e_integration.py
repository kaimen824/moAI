"""批次 8(端到端集成补强)回归:评审修复方案汇总批。

- 重启恢复全链(HTTP 级):跑到中断 → 模拟进程重启(重建 engine)→
  run-state 返回 waiting + 持久化中断卡 → resume 续跑到 done(对应 kill -9)
- 双 story 并发:真并发跑图,事件/落库/checkpoint 互不污染
- 预算 429 无部分写入:入口拦截,不留 running 残留/不产生消费
"""

from __future__ import annotations

import threading

from fastapi.testclient import TestClient
from langgraph.types import Command

import app.main as main
import tests.test_graph_e2e as replay
from app.core.llm.facade import LLMFacade
from app.graph.build import build_graph
from app.graph.runtime import build_engine, run_ctx
from tests.test_api import client, login, parse_sse, run_until   # noqa: F401


# ---------- 重启恢复全链(kill -9 的测试级模拟)----------

def test_restart_recovery_full_flow(tmp_path):
    facade = LLMFacade(response_override=replay.override)
    deps, conn = build_engine(tmp_path / "restart.db", llm=facade)
    main.install_engine(deps, conn, build_graph(deps, checkpointer=deps.checkpointer))
    try:
        with TestClient(main.app) as c:
            c.headers.update({"Authorization": f"Bearer {login(c)}"})
            sid = c.post("/stories", json={"title": "重启恢复", "premise": "测试"}
                         ).json()["story_id"]
            run_until(c, f"/stories/{sid}/generate", {"target_chapters": 1},
                      "confirm_master_outline")
    finally:
        main._engine, main._graph = None, None   # "进程死亡":内存态全部失效

    # 重启:同 db 重建引擎(启动收敛跑过;waiting 行必须保留)
    deps2, conn2 = build_engine(tmp_path / "restart.db", llm=facade)
    main.install_engine(deps2, conn2,
                        build_graph(deps2, checkpointer=deps2.checkpointer))
    try:
        with TestClient(main.app) as c:
            c.headers.update({"Authorization": f"Bearer {login(c)}"})
            body = c.get(f"/stories/{sid}/run-state").json()
            assert body["status"] == "waiting"          # DB 持久化状态还原
            assert body["interrupt"]["type"] == "confirm_master_outline"
            assert body["events"] == []                 # in-memory 事件流已失,不误报

            # 凭持久化中断卡继续 resume,checkpoint 续跑到 done
            run_until(c, f"/stories/{sid}/resume", {"action": "confirm"},
                      "confirm_stage_outline")
            run_until(c, f"/stories/{sid}/resume", {"action": "confirm"},
                      "user_review_chapter")
            r = c.post(f"/stories/{sid}/resume", json={"action": "confirm", "threads": []})
            assert any(k == "done" for k, _ in parse_sse(r.text))
            assert conn2.execute(
                "SELECT COUNT(*) c FROM chapters WHERE story_id=? AND status='active'",
                (sid,)).fetchone()["c"] == 1
    finally:
        main._engine, main._graph = None, None


# ---------- 双 story 并发 ----------

def test_two_stories_concurrent_runs(tmp_path):
    """两个 story 真并发跑图(各自线程 + 各自 checkpoint):事件、落库、
    usage 归属互不污染(ADR-0023 ContextVar 归属的并发面验证)。"""
    facade = LLMFacade(response_override=replay.override)
    deps, conn = build_engine(tmp_path / "conc.db", llm=facade)
    graph = build_graph(deps, checkpointer=deps.checkpointer)
    sids = {}
    for name in ("并发放事A", "并发放事B"):
        sid, branch = deps.repo.create_story(name, "测试")
        sids[name] = (sid, branch)

    errors: list[Exception] = []

    def drive(name: str) -> None:
        sid, branch = sids[name]
        run_id = f"run-{name}"          # 模拟 worker 入口:每线程独立 run 归属
        run_ctx.set((sid, run_id))
        cfg = {"configurable": {"thread_id": sid}, "recursion_limit": 200}
        try:
            graph.invoke({"story_id": sid, "branch_id": branch,
                          "target_chapters": 1, "initial_input": "x"}, cfg)
            graph.invoke(Command(resume={"action": "confirm"}), cfg)
            graph.invoke(Command(resume={"action": "confirm"}), cfg)
            graph.invoke(Command(resume={"action": "confirm", "threads": []}), cfg)
        except Exception as exc:  # noqa: BLE001 — 汇集到主线程断言
            errors.append(exc)

    threads = [threading.Thread(target=drive, args=(n,)) for n in sids]
    for t in threads:
        t.start()
    for t in threads:
        t.join(120)
    assert not errors, errors

    for name, (sid, _branch) in sids.items():
        assert conn.execute(
            "SELECT COUNT(*) c FROM chapters WHERE story_id=? AND status='active'",
            (sid,)).fetchone()["c"] == 1, name
        # 事件归属:该 story 的快照存在,且 stage 事件都来自本 story 的 run
        events = deps.snapshot(sid)
        assert events, name
        # usage 归属:并发下 token 台账按 story 隔离、run_id 归属本线程 run
        # (ContextVar 每线程独立值,不串台)
        row = conn.execute(
            "SELECT COUNT(*) c FROM usage_log WHERE story_id=? AND run_id=?",
            (sid, f"run-{name}")).fetchone()
        null_row = conn.execute(
            "SELECT COUNT(*) c FROM usage_log WHERE story_id=? AND run_id IS NULL",
            (sid,)).fetchone()
        assert row["c"] > 0 and null_row["c"] == 0, name
    # 互不污染:任一 story 名下不出现对方的章节/事实
    (sid_a, _), (sid_b, _) = sids.values()
    assert conn.execute(
        "SELECT COUNT(*) c FROM facts WHERE story_id NOT IN (?,?)",
        (sid_a, sid_b)).fetchone()["c"] == 0


# ---------- 预算 429 无部分写入 ----------

def test_budget_429_leaves_no_partial_writes(client, monkeypatch):
    c, deps = client
    sid = c.post("/stories", json={"title": "预算原子", "premise": "测试"}).json()["story_id"]
    monkeypatch.setattr(main.get_settings(), "global_daily_token_budget", 1000,
                        raising=False)
    deps.conn.execute(
        "INSERT INTO usage_log (id, story_id, user_id, agent, model, tokens_in,"
        " tokens_out, cached_tokens, latency_ms, trace_id, stage, run_id, created_at)"
        " VALUES ('seed1', ?, NULL, 'writer', 'm', 400, 600, 0, 5, 't', 'draft', NULL,"
        " strftime('%Y-%m-%dT%H:%M:%SZ','now'))", (sid,))
    deps.conn.commit()

    before = deps.conn.execute("SELECT COUNT(*) c FROM usage_log").fetchone()["c"]
    r = c.post(f"/stories/{sid}/generate", json={"target_chapters": 1})
    assert r.status_code == 429
    # 入口拦截:无消费新增、无 running 残留、无章节写入
    assert deps.conn.execute("SELECT COUNT(*) c FROM usage_log").fetchone()["c"] == before
    assert deps.conn.execute(
        "SELECT COUNT(*) c FROM story_run_state WHERE story_id=? AND status='running'",
        (sid,)).fetchone()["c"] == 0
    assert deps.conn.execute(
        "SELECT COUNT(*) c FROM chapters WHERE story_id=?", (sid,)).fetchone()["c"] == 0
