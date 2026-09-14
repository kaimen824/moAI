"""BaseAgent 契约与注册表(ADR-0012 轻量实现模式)。

新增 Agent = 继承 BaseAgent(声明 role/写域/输出 schema 约定)+ 注册 + ACL 加行;
图装配读注册表,不改主控代码。
"""

from __future__ import annotations

import json
import re
import uuid
from abc import ABC, abstractmethod
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from app.core.config import AgentRole
from app.core.llm.base import ChatMessage, LLMResponse
from app.core.llm.facade import LLMFacade

# 注入的依赖包(引擎组装时提供;测试可注入 fake)
NodeDeps = Any   # runtime.Deps 的前向声明,避免环依赖


class LLMFormatError(RuntimeError):
    """LLM 输出解析/schema 校验失败(自纠重试一次后仍失败,ADR-0026)。

    携带 trace_id 与 error_code:SSE error 事件透出,便于前端提示与
    llm_failures 台账(原始输出截断留存)关联归因。
    """

    error_code = "llm_format"

    def __init__(self, *, stage: str, raw: str, error: str):
        self.stage = stage
        self.raw_output = raw
        self.error = error
        self.trace_id = uuid.uuid4().hex
        super().__init__(
            f"LLM 输出未通过 schema 校验(stage={stage},trace={self.trace_id}):{error}")


class BaseAgent(ABC):
    name: str = "base"            # 与 agent_acl 对齐的身份名
    role: AgentRole = AgentRole.SUPERVISOR
    write_domains: tuple[str, ...] = ()   # 声明所需写域(文档化;ACL 仍强制)

    def __init__(self, llm: LLMFacade):
        self.llm = llm

    @abstractmethod
    def __call__(self, state: dict, deps: NodeDeps) -> dict:
        """节点函数:返回状态增量。"""

    # ---- 结构化输出辅助(json 容错解析 + schema 自纠,ADR-0026)----
    def ask_json(self, system: str, user: str, stage: str, story_id: str = "",
                 schema: type[BaseModel] | None = None) -> dict:
        """JSON 调用:解析 -> schema 校验 -> 失败把错误回喂模型自纠一次 ->
        仍失败抛 LLMFormatError(由节点/管道决定安全降级或终止)。

        传 schema 时返回 model_dump() 的 dict(节点侧保持 dict 协议);
        不传 schema 保持旧行为(宽松解析,仅保证是 JSON 对象)。
        """
        messages = [ChatMessage("system", system), ChatMessage("user", user)]
        resp = self._chat_json(messages, stage, story_id)
        try:
            return self._validated(resp.content, schema)
        except (json.JSONDecodeError, ValidationError) as first_err:
            messages = [*messages,
                        ChatMessage("assistant", resp.content),
                        ChatMessage(
                            "user",
                            f"你的上一条输出未通过校验:{first_err}"
                            "。请严格按系统提示的 JSON 契约重新输出,"
                            "只输出 JSON 本体,不要解释、不要代码栅栏外的内容。")]
            retry = self._chat_json(messages, stage, story_id)
            try:
                return self._validated(retry.content, schema)
            except (json.JSONDecodeError, ValidationError) as err:
                raise LLMFormatError(
                    stage=stage, raw=retry.content or resp.content,
                    error=str(err)) from err

    def _chat_json(self, messages: list[ChatMessage], stage: str, story_id: str) -> LLMResponse:
        return self.llm.chat(
            self.role, messages,
            stage=stage,
            story_id=story_id,
            response_format={"type": "json_object"},
        )

    @staticmethod
    def _validated(text: str, schema: type[BaseModel] | None) -> dict:
        data = parse_json_loose(text)
        if schema is None:
            return data
        return schema.model_validate(data).model_dump(by_alias=True)

    def ask_text(self, system: str, user: str, stage: str, story_id: str = "") -> str:
        resp: LLMResponse = self.llm.chat(
            self.role,
            [ChatMessage("system", system), ChatMessage("user", user)],
            stage=stage,
            story_id=story_id,
        )
        return resp.content


def parse_json_loose(text: str) -> dict:
    """容错 JSON 解析(评审 6.9 加固):剥代码栅栏后用 raw_decode 扫描
    第一个完整 JSON 对象(容忍前后杂文);截断输出找不到完整对象时抛
    JSONDecodeError(原始输出由调用方留痕 llm_failures 便于归因)。"""
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    decoder = json.JSONDecoder()
    for start, ch in enumerate(text):
        if ch not in "{[":
            continue
        try:
            obj, _end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    raise json.JSONDecodeError("no complete JSON object found", text, 0)


AGENT_REGISTRY: dict[str, type[BaseAgent]] = {}


def register_agent(cls: type[BaseAgent]) -> type[BaseAgent]:
    AGENT_REGISTRY[cls.name] = cls
    return cls
