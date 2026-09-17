"""ChatDock P0(ADR-0031):ReAct 循环 / 工具观察回填 / JSONL 决策链日志。

LLM 经 response_override 按 stage="chat" 依序回放(可调用对象按调用
次序弹出一个响应);工具执行走真实 conn(查询工具读测试库,record_directive
/ stop_run 走引擎组件)。断言三面:SSE 事件序列、chat_messages 落库、
logs/chat/{story_id}.jsonl 事件全文。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.core.config import get_settings
from app.core.llm.base import LLMResponse
from app.core.llm.facade import LLMFacade
from app.graph.build import build_graph
from app.graph.runtime import build_engine
from app.infrastructure.chat_store import ChatStore

R = LLMResponse


class ChatScript:
    """stage="chat" 时按调用次序回放;序列耗尽后兜底一句普通回复。

    responses 暴露为可变属性:fixture 共享同一实例,测试用例自行装载序列。
    """

    def __init__(self, responses=()):
        self.responses = list(responses)

    def __call__(self, stage: str):
        if stage != "chat":
            return None
        if self.responses:
            return self.responses.pop(0)
        return R(content="好的", model="fake")


def _tool_call(name: str, arguments: str = "{}", tid: str = "t1") -> dict:
    return {"id": tid, "name": name, "arguments": arguments}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "chat_max_steps", 4)
    script = ChatScript()
    deps, conn = build_engine(tmp_path / "chat.db", llm=LLMFacade(response_override=script))
    main.install_engine(deps, conn, build_graph(deps, checkpointer=deps.checkpointer))
    with TestClient(main.app) as c:
        r = c.post("/auth/login", json={"username": "admin", "password": "admin123"})
        c.headers.update({"Authorization": f"Bearer {r.json()['access_token']}"})
        sid = c.post("/stories", json={"title": "对话测试书", "premise": "测试"}).json()["story_id"]
        yield c, deps, sid, script
    main.reset_engine()


def parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        kind = data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                kind = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        events.append((kind, data))
    return events


def test_chat_react_loop(client, tmp_path):
    """一次工具调用 + 最终回复:SSE 事件齐全,落库与 JSONL 对得上。"""
    c, deps, sid, script = client
    script.responses = [
        R(content="我先查一下状态", model="fake",
          tool_calls=[_tool_call("query_run_status")]),
        R(content="当前空闲,随时可以生成", model="fake"),
    ]

    r = c.post(f"/stories/{sid}/chat", json={"message": "现在什么状态?"})
    assert r.status_code == 200
    events = parse_sse(r.text)
    kinds = [k for k, _ in events]

    assert "tool_call" in kinds and "tool_result" in kinds
    assert ("reply", {"content": "当前空闲,随时可以生成"}) in events
    # 观察 query_run_status 回填的是 JSON(生成状态 + 待生效指令)
    tr = next(d for k, d in events if k == "tool_result")
    assert "run_state" in tr["observation"]

    # 落库:user / assistant(带 tool_calls)/ tool / assistant
    msgs = ChatStore(deps.conn).history(sid)
    assert [m["role"] for m in msgs] == ["user", "assistant", "tool", "assistant"]
    assert msgs[1]["meta"]["tool_calls"][0]["name"] == "query_run_status"
    assert msgs[2]["meta"]["name"] == "query_run_status"

    # JSONL 决策链:全文观察(不被前端截断口径影响)
    log_path = get_settings().log_dir / "chat" / f"{sid}.jsonl"
    lines = [json.loads(x) for x in log_path.read_text(encoding="utf-8").splitlines()]
    evts = [x["event"] for x in lines]
    assert evts == ["user_message", "step", "tool_call", "tool_result",
                    "step", "final_reply"]
    assert lines[3]["observation"].startswith("{")
    assert all("turn_id" in x and "ts" in x for x in lines)


def test_chat_history_restores_tool_chain(client):
    """GET history 还原完整工具链(下一轮 ReAct 可见上一轮调用)。"""
    c, deps, sid, script = client
    script.responses = [
        R(content="", model="fake", tool_calls=[_tool_call("record_directive", '{"content": "节奏放慢"}')]),
        R(content="已记录指令:节奏放慢", model="fake"),
    ]
    c.post(f"/stories/{sid}/chat", json={"message": "节奏放慢一点"})

    msgs = ChatStore(deps.conn).history_messages(sid)
    assert len(msgs) == 4
    assert msgs[1].tool_calls[0]["name"] == "record_directive"
    assert msgs[2].role == "tool" and msgs[2].tool_call_id == "t1"
    assert "已记录" in msgs[2].content

    r = c.get(f"/stories/{sid}/chat/history")
    assert r.status_code == 200
    assert [m["role"] for m in r.json()["messages"]] == [
        "user", "assistant", "tool", "assistant"]
    # 指令真的进了通道
    assert deps.peek_pending_directives(sid)[0]["content"] == "节奏放慢"


def test_chat_tool_bad_arguments_fed_back(client):
    """参数非法:错误作为观察回填,循环继续到最终回复。"""
    c, deps, sid, script = client
    script.responses = [
        R(content="", model="fake",
          tool_calls=[_tool_call("query_chapter", "not json")]),
        R(content="这本书还没有章节", model="fake"),
    ]

    r = c.post(f"/stories/{sid}/chat", json={"message": "看下第1章"})
    events = parse_sse(r.text)
    tr = next(d for k, d in events if k == "tool_result")
    assert "不是合法 JSON" in tr["observation"]
    assert ("reply", {"content": "这本书还没有章节"}) in events


def test_chat_max_steps_forces_summary(client, monkeypatch):
    """步数上限:连续工具调用耗尽后强制无工具总结,回复仍产生。"""
    c, deps, sid, script = client
    monkeypatch.setattr(get_settings(), "chat_max_steps", 2)
    script.responses = [
        R(content="", model="fake", tool_calls=[_tool_call("query_usage", tid="t1")]),
        R(content="", model="fake", tool_calls=[_tool_call("query_usage", tid="t2")]),
        R(content="用量已汇总:暂无调用", model="fake"),   # 上限后的总结调用
    ]

    r = c.post(f"/stories/{sid}/chat", json={"message": "花了多少 token"})
    events = parse_sse(r.text)
    assert ("reply", {"content": "用量已汇总:暂无调用"}) in events

    log_path = get_settings().log_dir / "chat" / f"{sid}.jsonl"
    final = [json.loads(x) for x in log_path.read_text(encoding="utf-8").splitlines()][-1]
    assert final["event"] == "final_reply" and final["max_steps_reached"] is True


def test_chat_unauthorized_401(tmp_path):
    """未认证:401(与一键生成同口径)。"""
    deps, conn = build_engine(tmp_path / "chat401.db",
                              llm=LLMFacade(response_override=lambda s: None))
    main.install_engine(deps, conn, build_graph(deps, checkpointer=deps.checkpointer))
    with TestClient(main.app) as c:
        r = c.post("/stories/whatever/chat", json={"message": "hi"})
        assert r.status_code == 401
    main.reset_engine()
