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
            "canonical_names": [
                {"name": "审计部", "type": "faction", "aliases": ["审计部外勤组"]},
                {"name": "精神疗养院", "type": "location"},
            ],
        },
    }
    out = render_context(state)
    assert "[核心能力契约(主角能力的硬边界,不得越权)]" in out
    assert "能做:观察现象" in out
    assert "[在场角色意图" in out and "顾山海" in out and "夺回锁链" in out
    assert "[悬置伏笔(只许加深神秘感,严禁解释或回收)]" in out
    assert "古碑来历" in out.split("[悬置伏笔")[1]        # 悬置组含早埋伏笔
    assert "[活跃伏笔(可推进;每章至多推进一条;标[应回收]的优先安排)]" in out
    assert "旧神残渣" in out.split("[活跃伏笔")[1].split("[悬置伏笔")[0]
    assert "[禁用表达" in out and "大脑飞速运转" in out
    canon = out.split("[实体规范名")[1]                     # ADR-0019 词典段
    assert "审计部(势力)" in canon and "又称:审计部外勤组" in canon
    assert "精神疗养院(地点)" in canon


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


def test_recent_phrase_blacklist_drops_grammar_patterns(deps):
    """虚词边界修剪(ch12 死循环回归):'人名+没有/说''名词+的'是语法黏连,
    变体分散后任何单一整句都不该上榜;真口头禅仍要命中。"""
    d, conn = deps
    story_id, branch = d.repo.create_story("语法过滤", "测试")
    variants = ["林默没有犹豫。", "林默没有退。", "林默没有接话。", "林默没有点头。"]
    says = ["苏清歌说这事不能算了。", "苏清歌说那不一样。", "苏清歌说他想多了。", "苏清歌说不好。"]
    for no in range(1, 5):
        _ins_chapter(conn, story_id, branch, no,
                     variants[no - 1] + says[no - 1]
                     + "他的大脑飞速运转起来,想出了办法。")
    ban = d.recent_phrase_blacklist(story_id, 6)
    assert not any("林默没有" in p for p in ban)        # 人名+否定变体:语法黏连
    assert not any("苏清歌说" in p for p in ban)         # 人名+说:对话标签语法
    assert not any(p.endswith("的") for p in ban)        # 名词+的:所有格语法
    assert any("大脑飞速运转" in p for p in ban)         # 真口头禅(跨章一致)仍然命中


def test_recent_phrase_blacklist_empty_history(deps):
    d, conn = deps
    story_id, _ = d.repo.create_story("新书", "测试")
    assert d.recent_phrase_blacklist(story_id, 1) == []


def test_recent_phrase_blacklist_exempts_plot_phrases(deps):
    """剧情承载短语排除(ADR-0035,《古真神》ch4 死锁回归):跨章高频但出现在
    本章要点/伏笔描述中的短语(如剧情死线'月圆之约')不入清单;口头禅不受
    豁免影响。"""
    d, conn = deps
    story_id, branch = d.repo.create_story("剧情词豁免", "测试")
    deadline = "月圆之约"
    tic = "他的大脑飞速运转起来"
    for no in range(1, 5):
        _ins_chapter(conn, story_id, branch, no,
                     f"苏家使者重申{deadline},限期已定。{tic},他有了主意。")
    _ins_chapter(conn, story_id, branch, 5, "平静的一章。" * 10)

    # 不带豁免:剧情死线进清单(死锁形态)
    ban_plain = d.recent_phrase_blacklist(story_id, 6)
    assert any("月圆之约" in p for p in ban_plain)
    # 带豁免(要点/伏笔提及该短语):剧情词剔除,口头禅保留
    ban = d.recent_phrase_blacklist(story_id, 6,
                                    exclude_texts=["本章要点:直面苏家的月圆之约"])
    assert not any("月圆" in p for p in ban)
    assert any("大脑飞速运转" in p for p in ban)


def _ins_entity(conn, story_id, name, etype="faction"):
    import uuid
    from app.graph.runtime import _now
    conn.execute(
        "INSERT INTO entities (id, story_id, type, name, content, chapter_no,"
        " status, created_at, updated_at) VALUES (?,?,?,?,?,?, 'active', ?, ?)",
        (uuid.uuid4().hex, story_id, etype, name, f"{name}的条目", 1, _now(), _now()))
    conn.commit()
    return name


def test_recent_phrase_blacklist_excludes_entity_references(deps):
    """ADR-0019 实体指称排除:实体名/别名/角色名(双向子串)不入禁用清单——
    剧情连续章指称同一地点/机构是正常指称密度(ch10"精神病院"实证);
    真口头禅仍要命中。"""
    d, conn = deps
    story_id, branch = d.repo.create_story("实体排除", "测试")
    import uuid
    from app.graph.runtime import _now
    _ins_entity(conn, story_id, "中央后勤部")
    _ins_entity(conn, story_id, "精神疗养院", "location")
    conn.execute(
        "INSERT INTO entity_aliases (alias, story_id, entity_id, created_at)"
        " SELECT '精神病院', ?, id, ? FROM entities WHERE story_id=? AND name='精神疗养院'",
        (story_id, _now(), story_id))
    conn.execute(
        "INSERT INTO characters (id, story_id, name, profile, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?)",
        (uuid.uuid4().hex, story_id, "林默", "", _now(), _now()))
    conn.commit()

    entity_ref = "他潜入中央后勤部交材料,路过精神疗养院的大门,看见林默出手。"
    tic = "他的大脑飞速运转起来"
    for no in range(1, 5):
        _ins_chapter(conn, story_id, branch, no, f"{entity_ref}然后做了第{no}件事。{tic},想出了办法。")

    ban = d.recent_phrase_blacklist(story_id, 6)
    assert not any("后勤部" in p for p in ban)        # 实体名(含子串扩展)被保护
    assert not any("精神病院" in p or "疗养院" in p for p in ban)  # 别名同样保护
    assert not any("林默" in p for p in ban)          # 角色名保护
    assert any("大脑飞速运转" in p for p in ban)      # 真口头禅(跨章一致)仍然命中


def test_canonical_entity_registry(deps):
    """ADR-0019 规范名词典:active 非角色实体 + 别名挂载;角色类不入册。"""
    d, conn = deps
    story_id, _ = d.repo.create_story("词典", "测试")
    from app.graph.runtime import _now
    _ins_entity(conn, story_id, "审计部")
    _ins_entity(conn, story_id, "林默", "character")
    conn.execute(
        "INSERT INTO entity_aliases (alias, story_id, entity_id, created_at)"
        " SELECT '审计部外勤组', ?, id, ? FROM entities WHERE story_id=? AND name='审计部'",
        (story_id, _now(), story_id))
    conn.commit()

    reg = d.canonical_entity_registry(story_id)
    names = [e["name"] for e in reg]
    assert "审计部" in names and "林默" not in names   # 角色不入册
    entry = next(e for e in reg if e["name"] == "审计部")
    assert entry.get("aliases") == ["审计部外勤组"]


# ---------- 串行双闸路由(ADR-0036:结构先行,风格后置,各自计数)----------

def test_serial_gate_routing():
    from app.graph.routes import route_after_struct_review, route_after_style_review

    # 结构闸:pass -> 风格评审;revise -> 全文重写(无论 fix_scope,结构意见归结构);
    # needs_user -> 转人工
    assert route_after_struct_review({"struct_verdict": "pass"}) == "style_review"
    assert route_after_struct_review({"struct_verdict": "revise"}) == "rewrite"
    assert route_after_struct_review({"struct_verdict": "needs_user"}) == "user_review"
    assert route_after_struct_review({}) == "rewrite"          # 缺省安全侧重写

    # 风格闸:pass / needs_user -> 人审;revise -> 精校(局部修,不重掷全文)
    assert route_after_style_review({"merged_verdict": "pass"}) == "user_review"
    assert route_after_style_review({"merged_verdict": "needs_user"}) == "user_review"
    assert route_after_style_review({"merged_verdict": "revise"}) == "polish"
    assert route_after_style_review({}) == "polish"            # 缺省安全侧精校


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
