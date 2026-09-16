"""细纲打回意见注入验收:confirm_stage_outline 上用户 revise+feedback 必须
进入 stage_outline 重生成 prompt——此前仅总大纲注入(supervisor.py GenMasterOutline),
细纲打回实际是"盲重摇"(2026-09-16 链路排查发现的缺陷)。"""

from __future__ import annotations

from types import SimpleNamespace

from app.core.llm.base import LLMResponse
from app.graph.agents.supervisor import StageOutlineNode


class _CaptureLLM:
    """记录 messages 的假 LLM(ask_text 只需 chat 一个方法)。"""

    def __init__(self):
        self.calls = []

    def chat(self, role, messages, **kw):
        self.calls.append(messages)
        return LLMResponse(content="1| 新细纲行|沈砚|推进", model="fake")


def _deps():
    return SimpleNamespace(
        story_recap=lambda s: "回顾文本",
        recent_carryover=lambda s: "衔接文本",
        parse_stage_range=lambda outline, start: start + 1,
    )


def test_revise_feedback_reaches_stage_prompt():
    llm = _CaptureLLM()
    node = StageOutlineNode(llm=llm)
    state = {"story_id": "s1", "master_outline": "卷一:山村异变",
             "chapters_done": 2,
             "user_input": {"action": "revise",
                            "feedback": "第三章不要离村,改成夜探古碑"}}
    node(state, _deps())
    user_msg = llm.calls[0][1].content
    assert "夜探古碑" in user_msg
    assert "[用户对上一版细纲的修改意见(优先落实)]" in user_msg


def test_no_feedback_no_injection():
    """正常路径(confirm/首轮)不得出现意见段——防提示词污染。"""
    llm = _CaptureLLM()
    node = StageOutlineNode(llm=llm)
    state = {"story_id": "s1", "master_outline": "卷一",
             "chapters_done": 0}     # 无 user_input
    node(state, _deps())
    assert "修改意见" not in llm.calls[0][1].content

    llm2 = _CaptureLLM()
    node2 = StageOutlineNode(llm=llm2)
    state2 = {"story_id": "s1", "master_outline": "卷一", "chapters_done": 0,
              "user_input": {"action": "confirm"}}   # confirm 也不注入
    node2(state2, _deps())
    assert "修改意见" not in llm2.calls[0][1].content
