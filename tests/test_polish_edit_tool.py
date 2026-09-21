"""精校局部替换编辑工具(ADR-0038):套用器矩阵 + 节点级行为。

治两个病:全文重掷的 token 成本(整章 out → 改动量)与未改动文本被
重掷引入新瑕疵(打地鼠实证)。构造性保证:不动的部分物理上不变。
"""

from __future__ import annotations

import json

import pytest
from app.core.llm.base import LLMResponse
from app.core.llm.facade import LLMFacade
from app.graph.agents.polisher import PolishDraftNode, apply_edits
from app.graph.runtime import build_engine

R = LLMResponse

TEXT = "沈砚在暴雨中前行,身旁的白芷提着一盏昏黄的灯。沈砚收了伞。"


def test_apply_edits_matrix():
    # 精确唯一命中:套用
    out, applied, failed = apply_edits(TEXT, [{"find": "昏黄的灯", "replace": "暖黄的灯"}])
    assert "暖黄的灯" in out and "昏黄的灯" not in out
    assert len(applied) == 1 and not failed
    # 未找到(复述/改写了原文)→ 失配
    out, applied, failed = apply_edits(TEXT, [{"find": "昏黄的灯光", "replace": "x"}])
    assert out == TEXT and failed[0]["reason"].startswith("草稿中未找到")
    # 多处命中 → 失配并要求加长
    out, applied, failed = apply_edits(TEXT, [{"find": "沈砚", "replace": "他"}])
    assert failed[0]["reason"].startswith("命中 2 处")
    # 过短 → 失配;同值空编辑 → 静默跳过(不进 failed,不烧回填重试)
    out, applied, failed = apply_edits(TEXT, [{"find": "灯", "replace": "x"},
                                              {"find": "沈砚", "replace": "沈砚"}])
    assert out == TEXT and len(failed) == 1 and failed[0]["reason"] == "片段过短"
    # 顺序套用:前一条改变后一条的唯一性
    out, _, _ = apply_edits(
        TEXT, [{"find": "沈砚在暴雨中前行", "replace": "他在雨中前行"},
                {"find": "沈砚收了伞", "replace": "他收了伞"}])
    assert "他在雨中前行" in out and "他收了伞" in out


@pytest.fixture()
def env(tmp_path):
    script = {"responses": []}

    def override(stage: str):
        if stage == "polish" and script["responses"]:
            item = script["responses"].pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return None

    deps, conn = build_engine(tmp_path / "edit.db",
                              llm=LLMFacade(response_override=override))
    yield deps, script
    conn.close()


def _edits(*pairs):
    return R(content=json.dumps(
        {"edits": [{"find": f, "replace": r} for f, r in pairs]},
        ensure_ascii=False), model="fake")


def _node_run(deps, draft=TEXT, **extra):
    node = PolishDraftNode(deps.llm)
    state = {"story_id": "s1", "chapter_no": 1, "rewrite_count": 0,
             "draft": draft, "context_bundle": {}, **extra}
    return node(state, deps)


def test_edit_tool_normal_path(env):
    """命中编辑:原稿局部替换,未触及部分逐字保留;单次调用。"""
    deps, script = env
    script["responses"] = [_edits(("昏黄的灯", "暖黄的灯"))]
    out = _node_run(deps)
    assert out["draft"] == TEXT.replace("昏黄的灯", "暖黄的灯")


def test_edit_tool_empty_edits_returns_original(env):
    """模型判定无需修改:原稿直返(不回退全文,不烧第二次调用)。"""
    deps, script = env
    script["responses"] = [_edits()]
    out = _node_run(deps)
    assert out["draft"] == TEXT


def test_edit_tool_retry_on_mismatch(env):
    """失配编辑回填重试一次;重试命中即成立,两次调用。重试以套用后
    文本为底——find 针对尚未被修改的目标。"""
    deps, script = env
    script["responses"] = [
        _edits(("白芷提着灯笼", "x"),            # 失配:原文是"提着一盏"
                ("沈砚收了伞", "他收了伞")),      # 命中
        _edits(("白芷提着一盏昏黄的灯", "白芷提着一盏烛火")),   # 重试修正片段
    ]
    out = _node_run(deps)
    assert "白芷提着一盏烛火" in out["draft"] and "昏黄的灯" not in out["draft"]
    assert "他收了伞" in out["draft"]            # 第一次命中保留,不被重试翻掉


def test_edit_tool_full_fallback_when_zero_applied(env):
    """全部失配且重试仍零命中 → 回退全文重写(旧模式,轮次推进保证)。"""
    deps, script = env
    script["responses"] = [
        _edits(("不存在甲", "x")),
        _edits(("不存在乙", "y")),
        R(content="全文重写后的正文。", model="fake"),      # 模式 B
    ]
    out = _node_run(deps)
    assert out["draft"] == "全文重写后的正文。"


def test_edit_tool_full_fallback_on_bad_json(env):
    """编辑 JSON 解析失败(自纠后仍坏)→ 模式 B 兜底。"""
    deps, script = env
    script["responses"] = [
        R(content="不是JSON", model="fake"),
        R(content="还不是JSON", model="fake"),             # ask_json 自纠一次
        R(content="模式B全文。", model="fake"),
    ]
    out = _node_run(deps)
    assert out["draft"] == "模式B全文。"
