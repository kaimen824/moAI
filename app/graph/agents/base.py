"""BaseAgent 契约与注册表(ADR-0012 轻量实现模式)。

新增 Agent = 继承 BaseAgent(声明 role/写域/输出 schema 约定)+ 注册 + ACL 加行;
图装配读注册表,不改主控代码。
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any, Callable

from app.core.config import AgentRole
from app.core.llm.base import ChatMessage, LLMResponse
from app.core.llm.facade import LLMFacade

# 注入的依赖包(引擎组装时提供;测试可注入 fake)
NodeDeps = Any   # runtime.Deps 的前向声明,避免环依赖


class BaseAgent(ABC):
    name: str = "base"            # 与 agent_acl 对齐的身份名
    role: AgentRole = AgentRole.SUPERVISOR
    write_domains: tuple[str, ...] = ()   # 声明所需写域(文档化;ACL 仍强制)

    def __init__(self, llm: LLMFacade):
        self.llm = llm

    @abstractmethod
    def __call__(self, state: dict, deps: NodeDeps) -> dict:
        """节点函数:返回状态增量。"""

    # ---- 结构化输出辅助(json 容错解析)----
    def ask_json(self, system: str, user: str, stage: str, story_id: str = "") -> dict:
        resp: LLMResponse = self.llm.chat(
            self.role,
            [ChatMessage("system", system), ChatMessage("user", user)],
            stage=stage,
            story_id=story_id,
            response_format={"type": "json_object"},
        )
        return parse_json_loose(resp.content)

    def ask_text(self, system: str, user: str, stage: str, story_id: str = "") -> str:
        resp: LLMResponse = self.llm.chat(
            self.role,
            [ChatMessage("system", system), ChatMessage("user", user)],
            stage=stage,
            story_id=story_id,
        )
        return resp.content


def parse_json_loose(text: str) -> dict:
    """容错 JSON 解析:剥 markdown 代码栅栏/前后杂文。"""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start > 0 or end < len(text) - 1:
        text = text[start:end + 1]
    return json.loads(text)


AGENT_REGISTRY: dict[str, type[BaseAgent]] = {}


def register_agent(cls: type[BaseAgent]) -> type[BaseAgent]:
    AGENT_REGISTRY[cls.name] = cls
    return cls
