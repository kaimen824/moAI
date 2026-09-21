"""引擎组装(ADR-0030 阶段2):Deps 兼容门面 + build_engine 唯一装配点。

Deps 的九组职责实现已拆至 app/infrastructure/runtime_components.py
(对应 application/ports.py 的端口);本模块保留原字段与方法名逐一转发——
节点代码与测试零改动,后续阶段节点再逐步改依赖端口注入。

运行上下文(run_ctx/user_ctx/StopRequested)实现已下沉 app/core/run_context,
此处再导出保持既有 import 路径(sse/build/tests/scripts)不变。

图节点经 deps 访问持久层(不直接 import memory,便于测试注入);
CLI / API / 测试 复用同一引擎(ADR-0009:图与调用方解耦)。
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from app.core.llm.facade import LLMFacade
from app.core.run_context import (  # noqa: F401 — 再导出保持 import 路径兼容
    StopRequested,
    current_run,
    current_user_id,
    run_ctx,
    user_ctx,
)
from app.db.database import init_db
from app.infrastructure.runtime_components import (  # noqa: F401 — _now 被 scripts/tests 复用
    InMemoryStopController,
    SqliteCharacterRegistry,
    SqliteDirectiveChannel,
    SqliteEventBus,
    SqliteFailureLedger,
    SqliteFinalizeStore,
    SqliteRecapBuilder,
    SqliteRunStateStore,
    SqliteStylePolicy,
    _now,
)
from app.memory.entity import EntityService
from app.memory.repository import AgentContext, Repository
from app.memory.retrieval import RetrievalService
from app.observability.usage_log import make_usage_sink


@dataclass
class Deps:
    """节点可用的运行时依赖(经闭包注入图节点)。

    字段为原始依赖面(测试注入点);协作者组件在 __post_init__ 组装,
    方法为端口转发。组件持 self 弱引用,故 repo/entities/embed_fn 的
    build_engine 延迟注入对组件透明。
    """

    conn: sqlite3.Connection
    repo: Repository
    retrieval: RetrievalService
    llm: LLMFacade
    entities: EntityService | None = None    # 实体层(ADR-0015:消歧漏斗/合并执行)
    embed_fn: object = None              # texts -> vectors(定稿时为 facts/摘要算 embedding)
    # 引擎级可重入互斥:全部 DB 访问(repo 方法 / usage sink / checkpointer / 端点)
    # 各自持锁做短临界区。不全程锁图执行——LangGraph fan-out 节点跑在独立线程。
    run_lock: threading.RLock = field(default_factory=threading.RLock)
    checkpointer: object = None
    # 协作者组件(端口实现,见 infrastructure.runtime_components)
    events: SqliteEventBus = field(default_factory=SqliteEventBus)
    stops: InMemoryStopController = field(default_factory=InMemoryStopController)

    def __post_init__(self) -> None:
        self._states = SqliteRunStateStore(self.conn, self.run_lock)
        self._directives = SqliteDirectiveChannel(self.conn, self.run_lock)
        self._recap = SqliteRecapBuilder(self.conn)
        self._style = SqliteStylePolicy(self.conn, self.run_lock)
        self._registry = SqliteCharacterRegistry(self)
        self._ledger = SqliteFailureLedger(self.conn, self.run_lock)
        self._finalize = SqliteFinalizeStore(self, self._directives)

    # ---- 协作式停止(端口 StopController)----
    def request_stop(self, story_id: str) -> None:
        self.stops.request_stop(story_id)

    def clear_stop(self, story_id: str) -> None:
        self.stops.clear_stop(story_id)

    def check_stop(self, story_id: str) -> None:
        self.stops.check_stop(story_id)

    # ---- SSE 事件总线(端口 EventBus)----
    def emit(self, kind: str, data: dict, thread_id: str | None = None) -> None:
        self.events.emit(kind, data, thread_id)

    def subscribe(self, thread_id: str):
        return self.events.subscribe(thread_id)

    def unsubscribe(self, q) -> None:
        self.events.unsubscribe(q)

    def has_subscribers(self, thread_id: str) -> bool:
        return self.events.has_subscribers(thread_id)

    def snapshot(self, thread_id: str) -> list:
        return self.events.snapshot(thread_id)

    def clear_events(self, thread_id: str) -> None:
        self.events.clear_events(thread_id)

    # ---- 用户指令通道(端口 DirectiveChannel)----
    def record_directive(self, story_id: str, content: str) -> str:
        return self._directives.record_directive(story_id, content)

    def peek_pending_directives(self, story_id: str) -> list[dict]:
        return self._directives.peek_pending_directives(story_id)

    def mark_directives_consumed(self, directive_ids: list[str]) -> None:
        self._directives.mark_directives_consumed(directive_ids)

    # ---- 运行状态持久化(端口 RunStateStore)----
    def set_run_state(self, story_id: str, *, status: str, run_id: str | None = None,
                      interrupt_type: str | None = None,
                      interrupt_payload: str | None = None,
                      error_code: str | None = None,
                      error_stage: str | None = None,
                      target_chapters: int | None = None) -> None:
        self._states.set_run_state(
            story_id, status=status, run_id=run_id, interrupt_type=interrupt_type,
            interrupt_payload=interrupt_payload, error_code=error_code,
            error_stage=error_stage, target_chapters=target_chapters)

    def get_run_state(self, story_id: str) -> dict | None:
        return self._states.get_run_state(story_id)

    # ---- 节点辅助 ----
    def supervisor_ctx(self, story_id: str) -> AgentContext:
        return AgentContext("supervisor", story_id)

    # ---- 记忆拼装(端口 RecapBuilder)----
    def recent_carryover(self, state: dict) -> str:
        return self._recap.recent_carryover(state)

    def stage_chapter_summaries(self, story_id: str, stage_start: int, *,
                                upto: int) -> list[tuple[int, str]]:
        return self._recap.stage_chapter_summaries(story_id, stage_start, upto=upto)

    def story_recap(self, state: dict) -> str:
        return self._recap.story_recap(state)

    def parse_stage_range(self, stage_outline: str, *, start: int) -> int:
        return self._recap.parse_stage_range(stage_outline, start=start)

    def extract_stage_line(self, stage_outline: str, chapter_no: int) -> str:
        return self._recap.extract_stage_line(stage_outline, chapter_no)

    # ---- 叙述风格策略(端口 StylePolicy)----
    def recent_phrase_blacklist(self, story_id: str, chapter_no: int, *,
                                window: int = 5, min_freq: int = 3,
                                min_chapters: int = 2, limit: int = 12,
                                exclude_texts: list[str] | None = None) -> list[str]:
        return self._style.recent_phrase_blacklist(
            story_id, chapter_no, window=window, min_freq=min_freq,
            min_chapters=min_chapters, limit=limit, exclude_texts=exclude_texts)

    def canonical_entity_registry(self, story_id: str, *, cap: int = 60) -> list[dict]:
        return self._style.canonical_entity_registry(story_id, cap=cap)

    # ---- 角色/实体簿记(端口 CharacterRegistry)----
    def character_name_map(self, state: dict) -> dict[str, str]:
        return self._registry.character_name_map(state)

    def prepare_character_seeds(self, state: dict) -> dict:
        return self._registry.prepare_character_seeds(state)

    def commit_character_seeds(self, plan: dict, *, commit: bool = True) -> list[str]:
        return self._registry.commit_character_seeds(plan, commit=commit)

    def resolve_entity_proposal(self, proposal_id: str, action: str) -> dict:
        return self._registry.resolve_entity_proposal(proposal_id, action)

    # ---- 审计台账(端口 FailureLedger)----
    def log_review(self, state: dict, *, reviewer: str, verdict: dict, round_no: int,
                   forced: bool | None = None) -> None:
        self._ledger.log_review(state, reviewer=reviewer, verdict=verdict,
                                round_no=round_no, forced=forced)

    def log_llm_failure(self, *, story_id: str, stage: str, node: str,
                        exc: Exception) -> None:
        self._ledger.log_llm_failure(story_id=story_id, stage=stage, node=node, exc=exc)

    # ---- 定稿(端口 FinalizeStore)----
    def commit_finalize(self, state: dict) -> str:
        return self._finalize.commit_finalize(state)


def build_engine(db_path: str | Path, llm: LLMFacade | None = None,
                 embed_fn=None) -> tuple[Deps, sqlite3.Connection]:
    """组装引擎(生产入口;测试注入 fake llm/embed)。

    返回 (deps, conn);deps.checkpointer 已构建并与引擎锁绑定——
    全部 DB 访问(repo 方法 / usage sink / checkpointer / API 端点)
    共用 deps.run_lock 做短临界区互斥。
    embed_fn 未注入且 facade 可用时,默认包装 facade.embed——
    向量兜底与定稿 embedding 不再静默降级。
    """
    conn = init_db(db_path)
    facade = llm or LLMFacade()
    deps = Deps(conn=conn, repo=None, retrieval=None, llm=facade)   # type: ignore[arg-type]
    repo = Repository(conn, lock=deps.run_lock)
    deps.repo = repo
    facade.set_usage_sink(make_usage_sink(
        conn, deps.run_lock,
        run_id_provider=lambda: current_run()[1] or None,
        user_id_provider=lambda: current_user_id() or None))
    # 全节点可观测:每次 LLM 调用 -> SSE agent_call 事件(实时)+ agent_traces 落库(回溯)
    def _trace_sink(rec: dict) -> None:
        deps.emit("agent_call", rec)
        with deps.run_lock:
            conn.execute(
                "INSERT INTO agent_traces (id, story_id, user_id, agent, model, stage,"
                " input_text, output_text, tokens_in, tokens_out, latency_ms,"
                " trace_id, run_id, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, rec.get("story_id", ""),
                 current_user_id() or None, rec.get("agent", ""),
                 rec.get("model", ""), rec.get("stage", ""),
                 "\n\n".join(f"[{m['role']}]\n{m['content']}" for m in rec.get("input", []))[:4000],
                 (rec.get("output", "") or "")[:8000],
                 rec.get("tokens_in", 0), rec.get("tokens_out", 0),
                 rec.get("latency_ms", 0), rec.get("trace_id", ""),
                 current_run()[1] or None, _now()),
            )
            conn.commit()
    facade.set_trace_sink(_trace_sink)
    # 启动收敛(ADR-0027,评审 6.4):进程崩溃残留的 running 收敛为 idle——
    # 可恢复性由 LangGraph checkpoint(SqliteSaver)保证,重新 generate 即续跑;
    # waiting 行保留(持久化中断卡仍可还原/resume)
    stale = conn.execute(
        "UPDATE story_run_state SET status='idle', interrupt_type=NULL,"
        " interrupt_payload=NULL, updated_at=? WHERE status='running'",
        (_now(),)).rowcount
    if stale:
        conn.commit()
    from langgraph.checkpoint.sqlite import SqliteSaver
    saver = SqliteSaver(conn)
    saver.lock = deps.run_lock          # saver 内部锁替换为引擎锁:与 repo/sink 互斥
    deps.checkpointer = saver
    if embed_fn is None:
        def _embed(texts):
            return facade.embed(list(texts)).vectors
        embed_fn = _embed
    deps.embed_fn = embed_fn
    deps.retrieval = RetrievalService(repo, embed_fn=embed_fn)
    deps.entities = EntityService(repo, embed_fn=embed_fn)
    return deps, conn
