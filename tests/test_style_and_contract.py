"""ADR-0017 反AI味机制单测:能力契约/意图注入/伏笔悬置/动态句式黑名单/标题过滤。"""

from __future__ import annotations

import pytest

from app.core.llm.facade import LLMFacade
from app.graph.agents.writer import render_context, strip_markdown_title
from app.graph.runtime import build_engine


# ---------- 标题过滤 ----------

def test_strip_markdown_title():
    assert strip_markdown_title("# 第九章 旧日残渣\n\n正文第一句。") == "正文第一句。"
    assert strip_markdown_title("## 第3章\n正文。") == "正文。"
    # 正常正文开头(非标题)不受影响
    assert strip_markdown_title("蝉鸣声像是被烈日烤焦了。\n下一段。") == \
        "蝉鸣声像是被烈日烤焦了。\n下一段。"
    # 以#开头的正文行(罕见)宁可误剥首行也不保留标题——契约如此
    assert strip_markdown_title("# 不是标题的正文\n第二行") == "第二行"


# ---------- render_context 新块 ----------

def test_render_context_blocks():
    state = {
        "capability_contract": "能做:观察现象\n不能做:给出结论",
        "chapter_brief": "要点",
        "context_bundle": {
            "character_intents": [
                {"name": "顾山海", "goal": "脱困", "knows": "禁闭区有密道",
                 "doesnt_know": "林默与赵无极的交易", "self_interest": "夺回锁链"}],
            "active_threads": [
                {"description": "古碑来历", "planted_chapter": 1, "_suspend": True},
                {"description": "旧神残渣", "planted_chapter": 2, "_suspend": False}],
            "style_ban": ["大脑飞速运转", "瞳孔骤缩"],
        },
    }
    out = render_context(state)
    assert "[核心能力契约(主角能力的硬边界,不得越权)]" in out
    assert "能做:观察现象" in out
    assert "[在场角色意图" in out and "顾山海" in out and "夺回锁链" in out
    assert "[悬置伏笔(只许加深神秘感,严禁解释或回收)]" in out
    assert "古碑来历" in out.split("[悬置伏笔")[1]        # 悬置组含早埋伏笔
    assert "[活跃伏笔(可推进;每章至多推进一条)]" in out
    assert "旧神残渣" in out.split("[活跃伏笔")[1].split("[悬置伏笔")[0]
    assert "[禁用表达" in out and "大脑飞速运转" in out


# ---------- 动态句式黑名单 ----------

@pytest.fixture()
def deps(tmp_path):
    facade = LLMFacade(response_override=lambda stage: None)
    d, conn = build_engine(tmp_path / "style.db", llm=facade)
    yield d, conn
    conn.close()


def _ins_chapter(conn, story_id, branch, no, content):
    from app.graph.runtime import _now  # 复用同一时间源
    import uuid
    conn.execute(
        "INSERT INTO chapters (id, story_id, chapter_no, version_no, title, content,"
        " status, branch_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (uuid.uuid4().hex, story_id, no, 1, f"第{no}章", content,
         "active", branch, _now(), _now()))
    conn.commit()


def test_recent_phrase_blacklist_catches_tic(deps):
    """跨章复现的口头禅被抓进禁用清单;单章内重复(有意排比)不算。"""
    d, conn = deps
    story_id, branch = d.repo.create_story("句式测试", "测试")
    tic = "他的大脑飞速运转起来"
    for no in range(1, 5):
        _ins_chapter(conn, story_id, branch, no, f"{tic},然后做了第{no}件事。")
    _ins_chapter(conn, story_id, branch, 5, "平静的一章。" * 10)

    ban = d.recent_phrase_blacklist(story_id, 6)
    assert any("大脑飞速运转" in p for p in ban)      # 口头禅命中


def test_recent_phrase_blacklist_needs_cross_chapter(deps):
    d, conn = deps
    story_id, branch = d.repo.create_story("单章排比", "测试")
    _ins_chapter(conn, story_id, branch, 1, "重复的短语" * 5)   # 单章内高频
    _ins_chapter(conn, story_id, branch, 2, "完全无关的内容。" * 3)
    ban = d.recent_phrase_blacklist(story_id, 3)
    assert not any("重复的短语" in p for p in ban)     # 未跨章 -> 不算口头禅


def test_recent_phrase_blacklist_empty_history(deps):
    d, conn = deps
    story_id, _ = d.repo.create_story("新书", "测试")
    assert d.recent_phrase_blacklist(story_id, 1) == []


# ---------- 新细纲格式仍可解析 ----------

def test_parse_stage_range_with_tension_plan(deps):
    outline = (
        "[阶段目标]主角拿到进入内城的资格。\n"
        "[各方意图]\n- 主角方:查明哥哥下落。\n"
        "- 对手方:赵无极要赶在巡查前转移残渣(主角不知)。\n"
        "[强制受挫]第6章:主角误信线人,损失一半凭证。\n"
        "[悬念锁]哥哥三年前的行踪本阶段不解释。\n"
        "[分章计划]\n"
        "第4章|线人接触,信息半真半假|林默,线人\n"
        "第5章|凭证争夺小挫|林默\n"
        "第6章|误判发酵,凭证损失|林默,顾山海"
    )
    d, _ = deps
    assert d.parse_stage_range(outline, start=4) == 6
    assert d.extract_stage_line(outline, 5).startswith("第5章")
    # 受挫行不会混入切片(不以章号开头)
    assert d.extract_stage_line(outline, 6).startswith("第6章|误判")
