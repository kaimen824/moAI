"""P3 验收:端到端跑通(响应覆盖回放模式,零 token),覆盖三类中断点、恢复、定稿落库。"""

from __future__ import annotations

import json

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from app.core.llm.base import LLMResponse
from app.core.llm.facade import LLMFacade
from app.graph.build import build_graph
from app.graph.runtime import build_engine

R = LLMResponse

SCRIPTS: dict[str, str | dict] = {
    "coauthor": "世界观:架空东方奇幻,核心冲突:旧神复苏",
    "init_characters": {"characters": [
        {"name": "沈砚", "profile": "主角;驽钝但坚韧"},
        {"name": "白芷", "profile": "师妹;知晓秘密"}]},
    "master_outline": "主线:阻止旧神复苏\n卷一(1-3章):山村异变\n卷二(4-6章):入宗门",
    "review_master_outline": {"verdict": "pass",
                              "scores": {"consistency": 9, "structure": 8}, "feedback": "ok"},
    "stage_outline": "1| 山村暴雨,沈砚发现古碑|沈砚,白芷|异象开启\n2| 古碑力量觉醒|沈砚|力量觉醒\n3| 离村远行|沈砚,白芷|踏上旅途",
    "review_stage_outline": {"verdict": "pass",
                             "scores": {"consistency": 9, "structure": 9}, "feedback": "ok"},
    "chapter_slice": "本章要点:暴雨夜的异象与古碑初现,沈砚与白芷同行。",
    "draft": "沈砚在暴雨中前行,身旁的白芷提着一盏昏黄的灯……(正文约一千五百字)",
    "review_draft_outline": {"verdict": "pass",
                             "scores": {"consistency": 9, "fidelity": 9}, "feedback": "ok"},
    "review_quality": {"verdict": "pass",
                       "scores": {"consistency": 9, "foreshadow": 8, "style": 9},
                       "feedback": "ok",
                       "thread_changes": [{"description": "古碑的来历", "action": "plant"}]},
    "extract_facts": {
        "facts": [
            {"content": "沈砚在暴雨夜发现古碑", "type": "event", "confidence": "high",
             "visible_to": ["沈砚", "白芷"]},
            {"content": "古碑下埋着旧神残识", "type": "setting", "confidence": "low",
             "visible_to": []},
        ],
        "beliefs": [{"character": "沈砚", "content": "沈砚以为古碑只是凡物"}],
        "conflicts": []},
    "update_characters": {"updates": [
        {"name": "沈砚", "profile_append": "觉醒了感应古碑的能力"}]},
    "chapter_summary": "暴雨夜沈砚发现古碑,力量初醒。",
}


def override(stage: str) -> LLMResponse | None:
    if stage in SCRIPTS:
        val = SCRIPTS[stage]
        content = val if isinstance(val, str) else json.dumps(val, ensure_ascii=False)
        return R(content=content, model="fake")
    return None


@pytest.fixture()
def engine(tmp_path):
    facade = LLMFacade(response_override=override)
    deps, conn = build_engine(tmp_path / "e2e.db", llm=facade)
    return build_graph(deps, checkpointer=deps.checkpointer), deps, conn


def test_e2e_two_chapters_with_interrupts(engine):
    graph, deps, conn = engine
    story_id, branch = deps.repo.create_story("端到端测试", "测试小说")
    cfg = {"configurable": {"thread_id": "e2e-run-1"}}

    # 1) 启动 -> 共创/角色/总大纲/评审 -> [中断点 0]
    result = graph.invoke({"story_id": story_id, "branch_id": branch,
                           "target_chapters": 2, "initial_input": "东方奇幻"}, cfg)
    assert result["__interrupt__"][0].value["type"] == "confirm_master_outline"

    # 2) 确认总大纲 -> 阶段细纲 + 评审 -> [中断点 A]
    result = graph.invoke(Command(resume={"action": "confirm"}), cfg)
    assert result["__interrupt__"][0].value["type"] == "confirm_stage_outline"

    # 3) 确认阶段细纲 -> 检索/写作/双评审 -> [中断点 B]
    result = graph.invoke(Command(resume={"action": "confirm"}), cfg)
    intr = result["__interrupt__"][0].value
    assert intr["type"] == "user_review_chapter"
    assert intr["chapter_no"] == 1 and intr["draft"]
    assert intr["thread_changes"]                     # 伏笔建议已带出待人工确认

    # 4) 确认定稿(伏笔人工确认传回)-> 第 2 章(非首章,切片,直达中断点 B)
    result = graph.invoke(Command(resume={
        "action": "confirm",
        "threads": [{"description": "古碑的来历", "action": "plant"}],
    }), cfg)
    intr2 = result["__interrupt__"][0].value
    assert intr2["type"] == "user_review_chapter" and intr2["chapter_no"] == 2

    # 5) 确认第 2 章 -> chapters_done == target -> END
    result = graph.invoke(Command(resume={"action": "confirm", "threads": []}), cfg)
    assert result.get("chapters_done") == 2

    # ---- 落库断言(定稿管道)----
    chapters = conn.execute(
        "SELECT * FROM chapters WHERE status='active' ORDER BY chapter_no").fetchall()
    assert len(chapters) == 2
    facts = conn.execute("SELECT * FROM facts").fetchall()
    assert len(facts) >= 2
    low_row = [f for f in facts if f["confidence"] == "low"]
    assert low_row and low_row[0]["status"] == "pending_review"   # E3 抽检队列
    beliefs = conn.execute("SELECT * FROM beliefs").fetchall()
    assert beliefs and beliefs[0]["status"] == "believed"         # 认知层落库
    threads = conn.execute("SELECT * FROM plot_threads").fetchall()
    assert any(t["status"] == "open" for t in threads)            # 伏笔落库
    profile = conn.execute(
        "SELECT profile FROM characters WHERE name='沈砚'").fetchone()["profile"]
    assert "感应古碑" in profile                                  # 角色卡消费事实更新
    assert conn.execute(
        "SELECT COUNT(*) c FROM chapter_summaries").fetchone()["c"] >= 2
    assert conn.execute("SELECT COUNT(*) c FROM usage_log").fetchone()["c"] >= 10
    assert conn.execute("SELECT COUNT(*) c FROM review_results").fetchone()["c"] >= 4


def test_e2e_revision_loop_and_user_rewrite(engine):
    """中断点 B 用户提改写意见 -> 回流写作;再次确认定稿。"""
    graph, deps, conn = engine
    story_id, branch = deps.repo.create_story("改写测试", "测试")
    cfg = {"configurable": {"thread_id": "e2e-revise"}}

    graph.invoke({"story_id": story_id, "branch_id": branch,
                  "target_chapters": 1, "initial_input": "x"}, cfg)   # -> 中断 0
    graph.invoke(Command(resume={"action": "confirm"}), cfg)          # -> 中断 A
    result = graph.invoke(Command(resume={"action": "confirm"}), cfg) # -> 中断 B
    assert result["__interrupt__"][0].value["type"] == "user_review_chapter"

    # 用户提改写意见 -> 回流 write_draft -> 再次双评审 -> 中断 B
    result = graph.invoke(Command(resume={
        "action": "revise", "feedback": "开头节奏太慢", "threads": []}), cfg)
    assert result["__interrupt__"][0].value["type"] == "user_review_chapter"

    result = graph.invoke(Command(resume={"action": "confirm", "threads": []}), cfg)
    assert result.get("chapters_done") == 1
    usage = conn.execute("SELECT COUNT(*) c FROM usage_log").fetchone()["c"]
    assert usage >= 12     # 多了一轮 draft + 双评审
