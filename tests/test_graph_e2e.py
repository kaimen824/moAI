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
    "init_entities": {
        "entities": [
            {"name": "青岩宗", "type": "faction", "content": "山村所属的修仙宗门",
             "aliases": ["宗门"]},
            {"name": "古碑", "type": "item", "content": "暴雨夜现世的神秘古碑",
             "aliases": []}],
        "character_aliases": [{"name": "沈砚", "aliases": ["砚小子"]}],
        "links": [
            {"from": "沈砚", "to": "古碑", "relation": "发现"},
            {"from": "青岩宗", "to": "古碑", "relation": "镇守"}]},
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
    "stage_summary": "阶段聚合:沈砚发现古碑并觉醒力量,踏上离村旅途。",
    "entity_resolve": {"decisions": [
        {"name": "剑尘真人", "decision": "same", "target": "沈砚",
         "evidence": "沈砚觉醒后的道号"}]},
    "entity_stage_update": {"updates": [
        {"name": "黑风寨", "content": "山村附近的马匪寨,已被剑尘真人荡平"}]},
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

    # 1) 启动 -> 共创/角色草案(暂存)/总大纲/评审 -> [中断点 0]
    result = graph.invoke({"story_id": story_id, "branch_id": branch,
                           "target_chapters": 2, "initial_input": "东方奇幻"}, cfg)
    assert result["__interrupt__"][0].value["type"] == "confirm_master_outline"
    # 角色卡确认前不落库(共创产物暂存 state,用户放弃/重来零残留)
    assert conn.execute("SELECT COUNT(*) c FROM characters").fetchone()["c"] == 0

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


def test_e2e_continuation_skips_coauthor(engine):
    """续写:再次 generate 应跳过共创,从 checkpoint 接续直接产出下一章。"""
    graph, deps, conn = engine
    story_id, branch = deps.repo.create_story("续写测试", "续")
    cfg = {"configurable": {"thread_id": "cont-1"}}
    base = {"story_id": story_id, "branch_id": branch, "initial_input": "x"}

    # 第一次:完整流程写到第 1 章定稿
    graph.invoke({**base, "target_chapters": 1}, cfg)
    graph.invoke(Command(resume={"action": "confirm"}), cfg)            # 中断 0
    graph.invoke(Command(resume={"action": "confirm"}), cfg)            # 中断 A
    graph.invoke(Command(resume={"action": "confirm", "threads": []}), cfg)  # 中断 B -> 1 章完成
    assert conn.execute("SELECT COUNT(*) c FROM chapters WHERE status='active'").fetchone()["c"] == 1

    # 续写:target 提到 2 —— 入口路由应跳过共创/总大纲/阶段细纲,直达第 2 章的中断 B
    result = graph.invoke({**base, "target_chapters": 2}, cfg)
    intr = result["__interrupt__"][0].value
    assert intr["type"] == "user_review_chapter"
    assert intr["chapter_no"] == 2
    graph.invoke(Command(resume={"action": "confirm", "threads": []}), cfg)

    n = conn.execute("SELECT COUNT(*) c FROM chapters WHERE status='active'").fetchone()["c"]
    assert n == 2


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


def test_e2e_stage_boundary_by_outline_range(engine, monkeypatch):
    """阶段边界以细纲覆盖章号为准:细纲只到第2章 -> 第3章重新走细纲+确认。"""
    monkeypatch.setitem(SCRIPTS, "stage_outline",
                        "1| 山村暴雨,沈砚发现古碑|沈砚,白芷|异象开启\n"
                        "2| 古碑力量觉醒|沈砚|力量觉醒")
    graph, deps, conn = engine
    story_id, branch = deps.repo.create_story("阶段边界测试", "测试")
    cfg = {"configurable": {"thread_id": "e2e-stage"}}

    graph.invoke({"story_id": story_id, "branch_id": branch,
                  "target_chapters": 3, "initial_input": "x"}, cfg)   # 中断 0
    graph.invoke(Command(resume={"action": "confirm"}), cfg)          # 中断 A(细纲1-2)
    graph.invoke(Command(resume={"action": "confirm"}), cfg)          # 中断 B(ch1)
    graph.invoke(Command(resume={"action": "confirm", "threads": []}), cfg)  # ch2 中断 B
    result = graph.invoke(Command(resume={"action": "confirm", "threads": []}), cfg)
    # ch3 超出细纲范围 -> 重新生成阶段细纲 -> 中断 A(而非直接切片写第3章)
    intr = result["__interrupt__"][0].value
    assert intr["type"] == "confirm_stage_outline"

    # 分层记忆:ch2 为阶段末章 -> 阶段聚合摘要已落库(layer='stage')
    stage_rows = conn.execute(
        "SELECT chapter_no, content FROM chapter_summaries WHERE layer='stage'").fetchall()
    assert stage_rows and stage_rows[0]["chapter_no"] == 2
    # recap 分层拼装:早期聚合段 + 当前阶段全量段(含 ch2)+ 细纲进度段
    recap = deps.story_recap({"story_id": story_id, "chapter_no": 3,
                              "stage_start_chapter": 2,
                              "stage_outline": "1| 发现古碑\n2| 力量觉醒\n3| 离村远行"})
    assert "当前阶段" in recap and "细纲进度" in recap
    assert "已写完" in recap and "待写" in recap


def test_e2e_stage_loop_auto_escalates(engine, monkeypatch):
    """自动中断语义(所有者裁决):细纲相似不打断(网文局部修订是正常语义),
    即使每版完全相同,也由轮次上限(3轮)兜底转人工,而非相似度检测。"""
    monkeypatch.setitem(SCRIPTS, "review_stage_outline",
                        {"verdict": "revise",
                         "scores": {"consistency": 5, "structure": 5}, "feedback": "不行"})
    # stage_outline 脚本固定 -> 每轮生成结果完全相同(相似度 1.0)
    graph, deps, conn = engine
    story_id, branch = deps.repo.create_story("相似不打断", "测试")
    cfg = {"configurable": {"thread_id": "e2e-stuck"}}

    graph.invoke({"story_id": story_id, "branch_id": branch,
                  "target_chapters": 1, "initial_input": "x"}, cfg)   # 中断 0
    result = graph.invoke(Command(resume={"action": "confirm"}), cfg)
    # 固定脚本跑满 3 轮生成,由轮次上限触发转人工(而非相似度提前拦)
    while result.get("__interrupt__", [{}])[0].value.get("type") != "confirm_stage_outline":
        result = graph.invoke(Command(resume={"action": "confirm"}), cfg)
    intr = result["__interrupt__"][0].value
    assert intr["type"] == "confirm_stage_outline"
    assert intr["escalation"] and "3 轮" in intr["escalation"]
    assert intr["regen_count"] == 3            # 相似不打断:跑满上限而非第2轮拦截


def test_e2e_stage_regen_limit_escalates(tmp_path, monkeypatch):
    """自动中断(轮次上限):细纲重生成 3 轮仍未通过 -> 转 confirm_stage。"""
    import json as _json
    from app.core.llm.base import LLMResponse
    from app.core.llm.facade import LLMFacade
    from app.graph.runtime import build_engine
    monkeypatch.setitem(SCRIPTS, "review_stage_outline",
                        {"verdict": "revise",
                         "scores": {"consistency": 5, "structure": 5}, "feedback": "不行"})
    calls = {"stage_outline": 0}

    def dynamic(stage: str):
        if stage in SCRIPTS and stage != "stage_outline":
            val = SCRIPTS[stage]
            return val if isinstance(val, LLMResponse) else LLMResponse(
                content=val if isinstance(val, str) else _json.dumps(val, ensure_ascii=False),
                model="fake")
        if stage == "stage_outline":
            calls["stage_outline"] += 1
            variants = [
                "1| 暴雨夜沈砚在后山发现刻满符文的古碑,白芷同行目击异象|沈砚,白芷|古碑初现\n2| 次日清晨村中长老召集议事,商讨古碑来历|沈砚,长老|身世线索",
                "1| 沈砚随商队穿越黑风峡,遭遇马匪截道,出手退敌显露锋芒|沈砚,商队|江湖初行\n2| 抵达青州城,卷入漕帮与官府的暗斗|沈砚,漕帮|势力纠葛",
                "1| 深夜破庙避雨,神秘黑衣人留下半块玉佩后离去|沈砚,黑衣人|悬念钩子\n2| 沈砚循玉佩线索查访城西当铺,掌柜认出故人信物|沈砚,掌柜|玉佩之谜",
            ]
            return LLMResponse(content=variants[(calls["stage_outline"] - 1) % 3], model="fake")
        return None

    facade = LLMFacade(response_override=dynamic)
    facade = LLMFacade(response_override=dynamic)
    deps, conn = build_engine(tmp_path / "limit.db", llm=facade)
    graph = build_graph(deps, checkpointer=deps.checkpointer)
    story_id, branch = deps.repo.create_story("上限检测", "测试")
    cfg = {"configurable": {"thread_id": "e2e-limit"}}

    graph.invoke({"story_id": story_id, "branch_id": branch,
                  "target_chapters": 1, "initial_input": "x"}, cfg)
    result = graph.invoke(Command(resume={"action": "confirm"}), cfg)
    while result.get("__interrupt__", [{}])[0].value.get("type") != "confirm_stage_outline":
        result = graph.invoke(Command(resume={"action": "confirm"}), cfg)
    intr = result["__interrupt__"][0].value
    assert intr["escalation"] and "3 轮" in intr["escalation"]
    assert calls["stage_outline"] == 3          # 恰好生成 3 次即停,不再空烧


def test_e2e_agent_traces_recorded(engine):
    """全节点可观测:LLM 调用的输入/输出快照落 agent_traces。"""
    graph, deps, conn = engine
    story_id, branch = deps.repo.create_story("观测测试", "测试")
    cfg = {"configurable": {"thread_id": "e2e-trace"}}
    graph.invoke({"story_id": story_id, "branch_id": branch,
                  "target_chapters": 1, "initial_input": "x"}, cfg)
    graph.invoke(Command(resume={"action": "confirm"}), cfg)
    graph.invoke(Command(resume={"action": "confirm"}), cfg)
    graph.invoke(Command(resume={"action": "confirm", "threads": []}), cfg)

    rows = conn.execute(
        "SELECT agent, stage, input_text, output_text FROM agent_traces"
        " WHERE story_id=? ORDER BY created_at", (story_id,)).fetchall()
    assert len(rows) >= 6                      # 共创/角色/大纲/评审/细纲/切片/草稿/评审/抽取/摘要
    stages = {r["stage"] for r in rows}
    assert {"coauthor", "master_outline", "draft", "extract_facts"} <= stages
    draft_row = next(r for r in rows if r["stage"] == "draft")
    assert draft_row["input_text"] and "system" in draft_row["input_text"]
    assert draft_row["output_text"]             # 输入输出快照齐全


def test_e2e_rewrite_exhausted_notifies_user(engine, monkeypatch):
    """重写达上限不自动强制通过:needs_user 中断携带 rewrite_exhausted,交用户裁决。"""
    monkeypatch.setitem(SCRIPTS, "review_draft_outline",
                        {"verdict": "revise",
                         "scores": {"consistency": 5, "fidelity": 5},
                         "feedback": "剧情与上一章重复"})
    monkeypatch.setitem(SCRIPTS, "review_quality",
                        {"verdict": "revise",
                         "scores": {"consistency": 5, "foreshadow": 5, "style": 5},
                         "feedback": "重复", "thread_changes": []})
    graph, deps, conn = engine
    story_id, branch = deps.repo.create_story("上限测试", "测试")
    cfg = {"configurable": {"thread_id": "e2e-exhaust"}}

    graph.invoke({"story_id": story_id, "branch_id": branch,
                  "target_chapters": 1, "initial_input": "x"}, cfg)   # 中断 0
    graph.invoke(Command(resume={"action": "confirm"}), cfg)          # 中断 A
    result = graph.invoke(Command(resume={"action": "confirm"}), cfg) # 3轮重写后 -> 中断 B
    intr = result["__interrupt__"][0].value
    assert intr["type"] == "user_review_chapter"
    assert intr["rewrite_exhausted"] is True           # 明确告知用户已达上限

    # 用户裁决 confirm -> 仍可定稿(带评审意见)
    result = graph.invoke(Command(resume={"action": "confirm", "threads": []}), cfg)
    assert result.get("chapters_done") == 1
    # 审计:转交用户时 merge 写入 needs_user 记录(重写耗尽痕迹)
    rows = conn.execute(
        "SELECT reviewer, verdict, forced_pass FROM review_results"
        " ORDER BY created_at").fetchall()
    assert rows[-1]["reviewer"] == "merge"
    assert rows[-1]["verdict"] == "needs_user"
    assert rows[-1]["forced_pass"] == 1


def test_e2e_entity_pipeline(engine, monkeypatch):
    """实体层全链路(ADR-0015):共创种子落库 -> 抽取候选 -> 消歧(same 道号合并/
    new 新建)-> 链接解析 -> 阶段末条目滚动 -> 检索一跳/别名识别生效。"""
    monkeypatch.setitem(SCRIPTS, "stage_outline",
                        "1| 山村暴雨,沈砚发现古碑|沈砚,白芷|异象开启\n"
                        "2| 古碑力量觉醒|沈砚|力量觉醒")   # 阶段只到 ch2 -> ch2 为阶段末
    monkeypatch.setitem(SCRIPTS, "extract_facts", {
        "facts": [{"content": "沈砚在暴雨夜发现古碑", "type": "event",
                   "confidence": "high", "visible_to": ["沈砚", "白芷"]}],
        "beliefs": [],
        "entities": [
            {"name": "古碑", "type": "item", "description": "暴雨夜现世"},   # 精确命中种子
            {"name": "剑尘真人", "type": "character", "description": "沈砚觉醒后的道号"},
            {"name": "黑风寨", "type": "faction", "description": "山村附近的马匪寨"},
        ],
        "links": [{"from": "剑尘真人", "to": "古碑", "relation": "觉醒于"}],
        "conflicts": []})
    graph, deps, conn = engine
    story_id, branch = deps.repo.create_story("实体测试", "测试")
    cfg = {"configurable": {"thread_id": "e2e-entity"}}

    graph.invoke({"story_id": story_id, "branch_id": branch,
                  "target_chapters": 2, "initial_input": "x"}, cfg)
    graph.invoke(Command(resume={"action": "confirm"}), cfg)   # 总大纲确认 -> 种子落库
    # 共创种子:角色实体(确定性)+ 势力/物品实体 + 别名 + 初始链接,确认时一并落库
    ents = {e["name"]: dict(e) for e in conn.execute(
        "SELECT * FROM entities WHERE status='active'").fetchall()}
    assert set(ents) >= {"沈砚", "白芷", "青岩宗", "古碑"}
    assert ents["沈砚"]["type"] == "character"
    aliases = {r["alias"]: r["entity_id"] for r in conn.execute(
        "SELECT alias, entity_id FROM entity_aliases").fetchall()}
    assert aliases["砚小子"] == ents["沈砚"]["id"]
    assert aliases["宗门"] == ents["青岩宗"]["id"]
    shen = conn.execute("SELECT entity_id FROM characters WHERE name='沈砚'").fetchone()
    assert shen["entity_id"] == ents["沈砚"]["id"]   # 回写:链接扩展从此生效

    graph.invoke(Command(resume={"action": "confirm"}), cfg)   # 细纲确认 -> ch1 写作
    graph.invoke(Command(resume={"action": "confirm", "threads": []}), cfg)  # ch1 定稿
    # ch1 消歧结果:剑尘真人 same->沈砚(别名吸收);黑风寨 new;链接经别名解析
    aliases = {r["alias"]: r["entity_id"] for r in conn.execute(
        "SELECT alias, entity_id FROM entity_aliases").fetchall()}
    assert aliases["剑尘真人"] == ents["沈砚"]["id"]
    links = [(r["from_entity"], r["to_entity"], r["relation"]) for r in conn.execute(
        "SELECT from_entity, to_entity, relation FROM entity_links").fetchall()]
    assert (ents["沈砚"]["id"], ents["古碑"]["id"], "觉醒于") in links   # 别名解析到本体
    heifeng = conn.execute(
        "SELECT id, content, chapter_no FROM entities WHERE name='黑风寨'").fetchone()
    assert heifeng["chapter_no"] == 1 and heifeng["content"]

    graph.invoke(Command(resume={"action": "confirm", "threads": []}), cfg)  # ch2 定稿=阶段末
    # 阶段末滚动:黑风寨条目并入新剧情
    heifeng2 = conn.execute(
        "SELECT content FROM entities WHERE id=?", (heifeng["id"],)).fetchone()
    assert "荡平" in heifeng2["content"]

    # 消费端:别名识别(剑尘真人 -> 沈砚角色 id)+ 一跳邻居进 bundle
    state = {"story_id": story_id, "chapter_no": 3}
    name_map = deps.character_name_map(state)
    shen_char = conn.execute(
        "SELECT id FROM characters WHERE name='沈砚'").fetchone()["id"]
    assert name_map["剑尘真人"] == shen_char and name_map["砚小子"] == shen_char


def test_auto_mode_skips_stage_and_chapter_gates(engine):
    """自动模式(ADR-0016):总大纲闸永远人工;细纲/章节评审绿则自动确认,
    从总大纲确认后一路跑到 END 无中断;伏笔变更随评审建议自动生效。"""
    graph, deps, conn = engine
    story_id, branch = deps.repo.create_story("自动模式", "测试")
    cfg = {"configurable": {"thread_id": "auto-1"}}

    result = graph.invoke({"story_id": story_id, "branch_id": branch,
                           "target_chapters": 2, "initial_input": "x",
                           "auto_mode": True}, cfg)
    # 总大纲确认不被自动模式代签(书之根基,所有者裁决)
    assert result["__interrupt__"][0].value["type"] == "confirm_master_outline"

    # 确认总大纲后:细纲确认、两章审阅全部自动通过,直达 END
    result = graph.invoke(Command(resume={"action": "confirm"}), cfg)
    assert "__interrupt__" not in result or not result.get("__interrupt__")
    assert result.get("chapters_done") == 2
    assert conn.execute(
        "SELECT COUNT(*) c FROM chapters WHERE status='active'").fetchone()["c"] == 2
    # 伏笔变更自动生效(评审 thread_changes 未经人工勾选直接落库,台账可查)
    assert conn.execute(
        "SELECT COUNT(*) c FROM plot_threads").fetchone()["c"] >= 1


def test_auto_mode_escalates_on_rewrite_exhausted(engine, monkeypatch):
    """自动模式下重写 3 次仍未过审:中断点 B 强制回人工(rewrite_exhausted)。"""
    monkeypatch.setitem(SCRIPTS, "review_draft_outline",
                        {"verdict": "revise",
                         "scores": {"consistency": 5, "fidelity": 5},
                         "feedback": "不行"})
    monkeypatch.setitem(SCRIPTS, "review_quality",
                        {"verdict": "revise",
                         "scores": {"consistency": 5, "foreshadow": 5, "style": 5},
                         "feedback": "不行", "thread_changes": []})
    graph, deps, conn = engine
    story_id, branch = deps.repo.create_story("自动耗尽", "测试")
    cfg = {"configurable": {"thread_id": "auto-2"}}

    graph.invoke({"story_id": story_id, "branch_id": branch,
                  "target_chapters": 1, "initial_input": "x",
                  "auto_mode": True}, cfg)                    # 总大纲闸(人工)
    result = graph.invoke(Command(resume={"action": "confirm"}), cfg)  # 细纲自动过 -> 3轮重写 -> 转人工
    intr = result["__interrupt__"][0].value
    assert intr["type"] == "user_review_chapter"
    assert intr["rewrite_exhausted"] is True           # 自动模式不吞掉升级信号


def test_master_outline_regen_limit_escalates(engine, monkeypatch):
    """总大纲回炉上限(ADR-0016 新增,与细纲/重写同口径):3 轮未过 -> 确认卡带
    escalation 转人工,不再无限回炉。"""
    monkeypatch.setitem(SCRIPTS, "review_master_outline",
                        {"verdict": "revise",
                         "scores": {"consistency": 5, "structure": 5},
                         "feedback": "不行"})
    graph, deps, conn = engine
    story_id, branch = deps.repo.create_story("总纲上限", "测试")
    cfg = {"configurable": {"thread_id": "auto-3"}}

    result = graph.invoke({"story_id": story_id, "branch_id": branch,
                           "target_chapters": 1, "initial_input": "x",
                           "auto_mode": True}, cfg)
    # 3 轮回炉在首次运行内完成,第一次中断即带 escalation 的总大纲确认(不再无限回炉)
    intr = result["__interrupt__"][0].value
    assert intr["type"] == "confirm_master_outline"
    assert intr["escalation"] and "3 轮" in intr["escalation"]


def test_commit_finalize_dedup_and_setting_chain(tmp_path):
    """定稿落库:精确去重 + setting 推翻链(任一时点只有最新场景环境有效)。"""
    from app.memory.world import replay_world

    facade = LLMFacade(response_override=lambda stage: None)
    deps, conn = build_engine(tmp_path / "facts.db", llm=facade)
    story_id, branch = deps.repo.create_story("事实测试", "测试")
    base = {"story_id": story_id, "branch_id": branch, "draft": "正文"}

    deps.commit_finalize({**base, "chapter_no": 1, "fact_changes": {"facts": [
        {"content": "夕阳如血,染红大楼", "type": "setting", "confidence": "high",
         "visible_ids": []},
        {"content": "沈砚击败了黑衣人", "type": "event", "confidence": "high",
         "visible_ids": []},
    ]}})
    deps.commit_finalize({**base, "chapter_no": 2, "fact_changes": {"facts": [
        {"content": "沈砚击败了黑衣人", "type": "event", "confidence": "high",
         "visible_ids": []},                       # 精确重复 -> 跳过
        {"content": "夜色如墨,压在城市上空", "type": "setting", "confidence": "high",
         "visible_ids": []},                       # 新场景 -> 推翻 ch1 setting
    ]}})

    facts = conn.execute("SELECT content FROM facts").fetchall()
    assert len(facts) == 3                          # 无重复入库

    snap1 = replay_world(conn, story_id, branch, upto_chapter=1)
    contents1 = {f["content"] for f in snap1.facts}
    assert "夕阳如血,染红大楼" in contents1         # ch1 时点:旧环境有效
    assert "夜色如墨,压在城市上空" not in contents1

    snap2 = replay_world(conn, story_id, branch, upto_chapter=2)
    contents2 = {f["content"] for f in snap2.facts}
    assert "夜色如墨,压在城市上空" in contents2      # ch2 时点:新环境有效
    assert "夕阳如血,染红大楼" not in contents2      # 旧环境已失效,时间线不回漂
