"""LLMFacade:所有 Agent 的唯一调用入口 + usage 埋点(装饰器职责内联于入口)。

埋点经 UsageSink 回调上抛(core 不依赖 db,依赖倒置);无 sink 时静默跳过。
"""

from __future__ import annotations

import time
import uuid
from typing import Callable, Iterator, Sequence

from app.core.config import AgentRole, Settings, get_settings
from app.core.llm.base import (
    ChatMessage,
    EmbeddingResult,
    LLMResponse,
    UsageRecord,
)
from app.core.llm.factory import ProviderFactory, build_default_factory

UsageSink = Callable[[UsageRecord], None]
# TraceSink:全节点可观测——每次 LLM 调用完成后收到完整输入/输出快照
TraceSink = Callable[[dict], None]


class LLMFacade:
    def __init__(
        self,
        settings: Settings | None = None,
        factory: ProviderFactory | None = None,
        usage_sink: UsageSink | None = None,
        response_override: Callable[[str], LLMResponse | None] | None = None,
    ):
        """response_override(stage) -> LLMResponse | None:命中则跳过真实调用。

        用途:端到端测试回放、评测 dry-run(零 token 成本)。
        """
        self._settings = settings or get_settings()
        self._factory = factory
        self._usage_sink = usage_sink
        self._response_override = response_override
        self._trace_sink: TraceSink | None = None

    # ---- 内部 ----
    def _get_factory(self) -> ProviderFactory:
        if self._factory is None:
            self._factory = build_default_factory(
                self._settings.glm_api_key,
                getattr(self._settings, "dashscope_api_key", ""),
            )
        return self._factory

    def set_usage_sink(self, sink: UsageSink | None) -> None:
        self._usage_sink = sink

    def set_trace_sink(self, sink: TraceSink | None) -> None:
        self._trace_sink = sink

    def _emit_trace(
        self, *, agent: str, model: str, stage: str, story_id: str,
        messages: Sequence[ChatMessage], output: str,
        tokens_in: int, tokens_out: int, latency_ms: int, trace_id: str,
    ) -> None:
        if self._trace_sink is None:
            return
        try:
            self._trace_sink({
                "agent": agent, "model": model, "stage": stage, "story_id": story_id,
                "input": [{"role": m.role, "content": m.content} for m in messages],
                "output": output,
                "tokens_in": tokens_in, "tokens_out": tokens_out,
                "latency_ms": latency_ms, "trace_id": trace_id,
            })
        except Exception:   # 观测失败不影响主流程
            pass

    def _emit_usage(self, record: UsageRecord) -> None:
        if self._usage_sink is not None:
            try:
                self._usage_sink(record)
            except Exception:  # 埋点失败不影响主流程
                pass

    # ---- 对外入口 ----
    def chat(
        self,
        role: AgentRole | str,
        messages: Sequence[ChatMessage],
        *,
        stage: str = "",
        story_id: str = "",
        max_tokens: int | None = None,
        response_format: dict | None = None,
        tools: list[dict] | None = None,       # function calling 工具表(ADR-0031)
    ) -> LLMResponse:
        from app.core.llm.router import ModelRouter  # 延迟导入避免环

        role = AgentRole(role)
        route = ModelRouter(self._settings).route(role)
        trace_id = uuid.uuid4().hex[:16]
        started = time.perf_counter()
        if self._response_override is not None:
            resp = self._response_override(stage)
            if resp is not None:
                resp.model = route.model
                latency_ms = int((time.perf_counter() - started) * 1000)
                self._emit_usage(
                    UsageRecord(
                        agent=role.value, model=route.model,
                        latency_ms=latency_ms,
                        trace_id=trace_id, stage=stage, story_id=story_id,
                    )
                )
                self._emit_trace(
                    agent=role.value, model=route.model, stage=stage, story_id=story_id,
                    messages=messages, output=resp.content,
                    tokens_in=0, tokens_out=0,
                    latency_ms=latency_ms, trace_id=trace_id,
                )
                return resp
        client = self._get_factory().chat_client(route.provider)
        resp = client.chat(
            route.model,
            messages,
            temperature=route.temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            tools=tools,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        self._emit_usage(
            UsageRecord(
                agent=role.value,
                model=route.model,
                tokens_in=resp.tokens_in,
                tokens_out=resp.tokens_out,
                cached_tokens=resp.cached_tokens,
                latency_ms=latency_ms,
                trace_id=trace_id,
                stage=stage,
                story_id=story_id,
            )
        )
        self._emit_trace(
            agent=role.value, model=route.model, stage=stage, story_id=story_id,
            messages=messages,
            output=resp.content if not resp.tool_calls
            else f"[tool_calls] {[(c['name'], c['arguments']) for c in resp.tool_calls]}",
            tokens_in=resp.tokens_in, tokens_out=resp.tokens_out,
            latency_ms=latency_ms, trace_id=trace_id,
        )
        return resp

    def stream(
        self,
        role: AgentRole | str,
        messages: Sequence[ChatMessage],
        *,
        stage: str = "",
        story_id: str = "",
        max_tokens: int | None = None,
    ) -> Iterator[str]:
        """流式生成;埋点在流结束后发(token 统计不可得时记 0,延迟为准)。"""
        from app.core.llm.router import ModelRouter

        role = AgentRole(role)
        route = ModelRouter(self._settings).route(role)
        trace_id = uuid.uuid4().hex[:16]
        started = time.perf_counter()

        # 回放模式:override 命中则把整段内容切块 yield(零 token)
        if self._response_override is not None:
            resp = self._response_override(stage)
            if resp is not None:
                text = resp.content
                return self._wrap_replay(text, role, route.model, trace_id, stage,
                                         story_id, started)

        def _wrap(iterator: Iterator[str]) -> Iterator[str]:
            parts: list[str] = []
            try:
                for chunk in iterator:
                    parts.append(chunk)
                    yield chunk
            finally:
                latency_ms = int((time.perf_counter() - started) * 1000)
                # 优先取 provider 精确 usage(stream_options.include_usage);
                # 不可得时按字符数粗估(//2,保守值)
                usage = getattr(client, "last_usage", None)
                if usage:
                    tokens_in, tokens_out = usage[0], usage[1]
                    cached = usage[2] if len(usage) > 2 else 0
                else:
                    tokens_in = sum(len(m.content) for m in messages) // 2
                    tokens_out = sum(len(p) for p in parts) // 2
                    cached = 0
                self._emit_usage(
                    UsageRecord(
                        agent=role.value,
                        model=route.model,
                        tokens_in=tokens_in,
                        tokens_out=tokens_out,
                        cached_tokens=cached,
                        latency_ms=latency_ms,
                        trace_id=trace_id,
                        stage=stage,
                        story_id=story_id,
                    )
                )
                self._emit_trace(
                    agent=role.value, model=route.model, stage=stage, story_id=story_id,
                    messages=messages, output="".join(parts),
                    tokens_in=tokens_in, tokens_out=tokens_out,
                    latency_ms=latency_ms, trace_id=trace_id,
                )

        client = self._get_factory().chat_client(route.provider)
        return _wrap(
            client.stream(
                route.model, messages,
                temperature=route.temperature, max_tokens=max_tokens,
            )
        )

    def _wrap_replay(
        self, text: str, role: AgentRole, model: str, trace_id: str,
        stage: str, story_id: str, started: float,
    ) -> Iterator[str]:
        """回放模式的流式输出:按 8 字符切块,结束后发埋点。"""
        def _gen():
            try:
                for i in range(0, len(text), 8):
                    yield text[i:i + 8]
            finally:
                latency_ms = int((time.perf_counter() - started) * 1000)
                self._emit_usage(
                    UsageRecord(
                        agent=role.value, model=model, tokens_out=len(text) // 2,
                        latency_ms=latency_ms, trace_id=trace_id,
                        stage=stage, story_id=story_id,
                    )
                )
        return _gen()

    def embed(
        self,
        texts: Sequence[str],
        *,
        stage: str = "embed",
        story_id: str = "",
    ) -> EmbeddingResult:
        from app.core.llm.router import ModelRouter

        route = ModelRouter(self._settings).route(AgentRole.EMBEDDING)
        trace_id = uuid.uuid4().hex[:16]
        started = time.perf_counter()
        client = self._get_factory().embed_client(route.provider)
        result = client.embed(route.model, texts)
        latency_ms = int((time.perf_counter() - started) * 1000)
        self._emit_usage(
            UsageRecord(
                agent="EMBEDDING",
                model=route.model,
                tokens_in=result.tokens_in,
                latency_ms=latency_ms,
                trace_id=trace_id,
                stage=stage,
                story_id=story_id,
            )
        )
        return result


# 进程级默认实例(经 Settings 单例读取配置)
llm = LLMFacade()
