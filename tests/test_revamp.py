"""ReAct 独占节点 revamp_chapter(ADR-0031 P1,所有者拍板)。

三面验证:
- 端到端:组装输入 → 续跑生产管道(写作/三评审/人审中断)→ 确认后
  revamp 落库(旧版归档、新版 version_no+1 挂链)、chapters_done 不增、
  route_next 短路 END——一键模式拓扑零变化。
- 守卫:未定稿/越界章号/缺意见 → 观察文本,launcher 不触发。
- 冲突标注(拍板 b):LLM 找矛盾解析为清单;坏 JSON 降级 None 不阻断。
"""

from __future__ import annotations

import json

import pytest
from langgraph.types import Command

import tests.test_graph_e2e as replay
from app.core.llm.base import LLMResponse
from app.core.llm.facade import LLMFacade
from app.graph.build import build_graph
from app.graph.noderunner import NodeRunner, run_conflict_check
from app.graph.runtime import build_engine

R = LLMResponse


@pytest.fixture()
def env(tmp_path):
    deps, conn = build_engine(tmp_path / "revamp.db",
                              llm=LLMFacade(response_override=replay.override))
    graph = build_graph(deps, checkpointer=deps.checkpointer)
    sid, branch = deps.repo.create_story("重构测试书", "测试")
    # 手工落库"已定稿第 1 章"(v1,active)
    conn.execute(
        "INSERT INTO chapters (id, story_id, chapter_no, version_no, prev_version_id,"
        " title, content, status, branch_id, created_at, updated_at)"
        " VALUES ('ch1old', ?, 1, 1, NULL, '第1章', '旧版正文:沈砚发现古碑。',"
        " 'active', ?, '2026-01-01T00:00:00', '2026-01-01T00:00:00')",
        (sid, branch))
    conn.commit()
    # 伪造"已定稿 1 章"的 checkpoint(绕过共创;revamp 只面向有存稿的书)
    cfg = {"configurable": {"thread_id": sid}}
    graph.update_state(cfg, {
        "story_id": sid, "branch_id": branch, "target_chapters": 3,
        "chapters_done": 1, "chapter_no": 1,
        "stage_outline": "1| 旧章|沈砚|异象", "stage_end_chapter": 3,
    }, as_node="chapter_slice")
    launched = []
    runner = NodeRunner(deps, lambda: graph,
                        lambda s, u: launched.append((s, u)))
    yield deps, conn, graph, sid, branch, cfg, runner, launched
    conn.close()


def test_revamp_e2e_rewrite_review_finalize(env):
    deps, conn, graph, sid, branch, cfg, runner, launched = env
    obs = runner.revamp_chapter(sid, "u1", 1, "结尾加入古碑低语")
    assert "已开始重构第 1 章" in obs
    assert launched == [(sid, "u1")]          # launcher 触发一次(续跑信号)

    # checkpoint:输入经 as_node=chapter_slice 写回,revamp 标记就位
    st = graph.get_state(cfg).values
    assert st["revamp_pending"] is True and st["chapter_no"] == 1
    assert "古碑低语" in st["chapter_brief"]

    # 续跑:build_context → write_draft → 三评审 pass → 人审中断
    steps = [n for chunk in graph.stream(None, cfg, stream_mode="updates")
             for n in chunk]
    assert "build_context" in steps and "struct_merge" in steps \
        and "style_merge" in steps
    intr = graph.get_state(cfg).tasks[0].interrupts[0].value
    assert intr["type"] == "user_review_chapter"

    # 作者确认 → 定稿管道 → revamp 分支落库 → END(不进下一章)
    result = graph.invoke(Command(resume={"action": "confirm", "threads": []}), cfg)
    assert "__interrupt__" not in result
    st = graph.get_state(cfg).values
    assert st["chapters_done"] == 1           # 重构旧章:章数不变
    assert st.get("revamp_done") is True
    rows = conn.execute(
        "SELECT id, version_no, prev_version_id, status, content FROM chapters"
        " WHERE story_id=? AND chapter_no=1 ORDER BY version_no", (sid,)).fetchall()
    assert len(rows) == 2
    v1, v2 = rows
    assert v1["status"] == "archived" and v1["content"].startswith("旧版正文")
    assert v2["status"] == "active" and v2["version_no"] == 2
    assert v2["prev_version_id"] == v1["id"]
    assert "暴雨" in v2["content"]            # 新稿为回放脚本正文
    # 事实链照常更新(重写章的抽取产物正常进台账)
    assert conn.execute(
        "SELECT COUNT(*) c FROM facts WHERE story_id=?", (sid,)).fetchone()["c"] > 0


def test_revamp_guards(env):
    deps, conn, graph, sid, branch, cfg, runner, launched = env
    assert "还没有定稿章节" in runner.revamp_chapter("ghost", "u1", 1, "改")
    assert "不是已定稿章节" in runner.revamp_chapter(sid, "u1", 2, "改")
    assert "不是已定稿章节" in runner.revamp_chapter(sid, "u1", 0, "改")
    assert "缺少修订意见" in runner.revamp_chapter(sid, "u1", 1, "  ")
    assert launched == []                     # 守卫拦截:不触发续跑


def test_conflict_check_parses_and_degrades(env):
    deps, conn, graph, sid, branch, cfg, runner, launched = env
    # 后续章 ch2(active)供对照
    conn.execute(
        "INSERT INTO chapters (id, story_id, chapter_no, version_no, title, content,"
        " status, branch_id, created_at, updated_at)"
        " VALUES ('ch2', ?, 2, 1, '第2章', '古碑彻底碎裂,沈砚失去线索。',"
        " 'active', ?, '2026-01-02T00:00:00', '2026-01-02T00:00:00')",
        (sid, branch))
    conn.commit()
    state = {"story_id": sid, "chapter_no": 1, "draft": "新稿:古碑只是出现裂纹。"}
    orig = deps.llm._response_override
    deps.llm._response_override = lambda s: (
        R(content='[{"chapter_no": 2, "conflict": "新稿古碑未碎,后续章已碎",'
                  ' "suggest": "重写第2章"}]', model="fake")
        if s == "conflict_check" else None)
    try:
        report = run_conflict_check(deps, state)
    finally:
        deps.llm._response_override = orig
    assert report == [{"chapter_no": 2, "conflict": "新稿古碑未碎,后续章已碎",
                       "suggest": "重写第2章"}]

    # 坏 JSON → None(降级不阻断人审)
    deps.llm._response_override = lambda s: (
        R(content="不是JSON", model="fake") if s == "conflict_check" else None)
    try:
        assert run_conflict_check(deps, state) is None
    finally:
        deps.llm._response_override = orig


def test_conflict_check_no_later_chapters(env):
    deps, conn, graph, sid, branch, cfg, runner, launched = env
    assert run_conflict_check(
        deps, {"story_id": sid, "chapter_no": 1, "draft": "x"}) == []
