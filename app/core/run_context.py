"""运行上下文(ADR-0023/0028,ADR-0030 阶段2 下沉至 core):

- run_ctx:每 run 归属 (thread_id=story_id, run_id),worker 线程入口 set,
  节点/fan-out 线程(LangGraph 并行节点实测继承 ContextVar)与 LLM 回调经
  emit() 兜底读取——多 story 并发时事件不再串台。
- user_ctx:触发用户 id,usage_log/agent_traces 按用户归因成本。

放 core 层的原因:graph 层的 runtime 与 infrastructure 层的观测/台账组件
都需读取,而 infrastructure 不得反向 import graph(分层契约)。
graph.runtime 经再导出保持既有 import 路径兼容。
"""

from __future__ import annotations

import contextvars

run_ctx: contextvars.ContextVar = contextvars.ContextVar("novel_run_ctx", default=None)
user_ctx: contextvars.ContextVar = contextvars.ContextVar("novel_user_ctx", default="")


def current_run() -> tuple[str, str]:
    """(thread_id, run_id);不在运行上下文中返回空串。"""
    ctx = run_ctx.get()
    return ctx if ctx else ("", "")


def current_user_id() -> str:
    """触发用户 id;不在运行上下文(图级直调/定时任务)返回空串。"""
    return user_ctx.get() or ""


class StopRequested(Exception):
    """用户请求中断(协作式停止):节点入口检查抛出,worker 捕获后发 stopped 事件。

    checkpointer 保留最后完成的节点状态,重新 generate 从断点续跑。
    """
