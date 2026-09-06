"""仓储层(ADR-0003/0006):ACL + story_id 强制注入,fail-closed。

- 所有读写必须携带 AgentContext(agent_name + story_id),无身份直接拒绝
- 表级 ACL 在此强制:写作 Agent 写 facts -> PermissionError
- 所有查询自动 WHERE story_id = ctx.story_id(多租户隔离)
- Agent 不直接接触 SQL:Repository 是唯一入口(import-linter 守护分层)
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Iterable, Sequence

from app.memory.schemas import (
    Belief,
    ChapterRow,
    CharacterRow,
    EntityLink,
    EntityRow,
    Fact,
    PlotThread,
    VisibilityEntry,
)


class PermissionError_(PermissionError):
    """fail-closed 拒绝(含无身份/无 ACL/跨域写)。"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex


class AgentContext:
    """调用者身份(接口契约的一部分,ADR-0006)。"""

    __slots__ = ("agent_name", "story_id")

    def __init__(self, agent_name: str, story_id: str):
        if not agent_name or not story_id:
            raise PermissionError_(
                "fail-closed: agent_name 与 story_id 均为必传(AgentContext)"
            )
        self.agent_name = agent_name
        self.story_id = story_id


class Repository:
    _LOCKED_METHODS = (
        "check_access", "insert_facts", "insert_beliefs", "insert_visibility",
        "upsert_character", "get_characters", "upsert_entity", "add_entity_links",
        "get_linked_entities", "insert_chapter", "get_active_chapter",
        "upsert_plot_thread", "get_plot_threads", "insert_summary",
        "get_summaries", "create_story", "main_branch",
    )

    def __init__(self, conn: sqlite3.Connection, lock: "threading.RLock | None" = None):
        self.conn = conn
        self._db_lock = lock or threading.RLock()
        # 方法级加锁:每个 DB 访问为短临界区(图内 fan-out 节点线程安全)
        for name in self._LOCKED_METHODS:
            fn = getattr(self, name)

            def locked(*a, _fn=fn, **kw):
                with self._db_lock:
                    return _fn(*a, **kw)

            setattr(self, name, locked)

    # ================= ACL =================
    def check_access(self, ctx: AgentContext, domain: str, op: str) -> None:
        """fail-closed:无 ACL 行或无该操作权限 -> 拒绝。"""
        row = self.conn.execute(
            "SELECT can_read, can_write FROM agent_acl WHERE agent_name=? AND data_domain=?",
            (ctx.agent_name, domain),
        ).fetchone()
        col = f"can_{op}"
        if row is None or row[col] != 1:
            raise PermissionError_(
                f"deny: agent={ctx.agent_name!r} {op} {domain!r} "
                f"(acl={'missing' if row is None else 'insufficient'})"
            )

    # ================= facts / beliefs(写者:事件管理)=================
    def insert_facts(
        self,
        ctx: AgentContext,
        facts: Iterable[Fact],
        visibility: Iterable[VisibilityEntry] = (),
    ) -> list[str]:
        self.check_access(ctx, "facts", "write")
        ids: list[str] = []
        rows = []
        for f in facts:
            if f.id:
                f_id = f.id
            else:
                f_id = new_id()
                f.id = f_id
            f.story_id = ctx.story_id  # 强制对齐,防跨租户
            ids.append(f_id)
            rows.append((
                f_id, ctx.story_id, f.type, f.content, f.chapter_established,
                f.branch_id, f.prev_version_id, f.confidence, f.status,
                f.embedding, f.created_at or now_iso(),
            ))
        self.conn.executemany(
            "INSERT INTO facts (id, story_id, type, content, chapter_established, branch_id,"
            " prev_version_id, confidence, status, embedding, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        if visibility:
            self.insert_visibility(ctx, visibility)
        self.conn.commit()
        return ids

    def insert_beliefs(self, ctx: AgentContext, beliefs: Iterable[Belief]) -> list[str]:
        self.check_access(ctx, "beliefs", "write")
        ids = []
        rows = []
        for b in beliefs:
            b_id = b.id or new_id()
            b.id = b_id
            b.story_id = ctx.story_id
            ids.append(b_id)
            rows.append((
                b_id, ctx.story_id, b.character_id, b.content, b.source_fact_id,
                b.status, b.established_chapter, b.dispelled_chapter, b.branch_id,
                b.prev_version_id, b.embedding, b.created_at or now_iso(),
            ))
        self.conn.executemany(
            "INSERT INTO beliefs (id, story_id, character_id, content, source_fact_id,"
            " status, established_chapter, dispelled_chapter, branch_id, prev_version_id,"
            " embedding, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        self.conn.commit()
        return ids

    def insert_visibility(self, ctx: AgentContext, entries: Iterable[VisibilityEntry]) -> int:
        self.check_access(ctx, "fact_visibility", "write")
        rows = [
            (e.fact_id, e.character_id, e.knowledge_level, e.detail,
             e.learned_chapter, e.branch_id or self.main_branch(ctx))
            for e in entries
        ]
        self.conn.executemany(
            "INSERT OR REPLACE INTO fact_visibility"
            " (fact_id, character_id, knowledge_level, detail, learned_chapter, branch_id)"
            " VALUES (?,?,?,?,?,?)",
            rows,
        )
        self.conn.commit()
        return len(rows)

    # ================= 角色卡(写者:角色管理)=================
    def upsert_character(self, ctx: AgentContext, c: CharacterRow) -> str:
        self.check_access(ctx, "characters", "write")
        c_id = c.id or new_id()
        ts = now_iso()
        self.conn.execute(
            "INSERT INTO characters (id, story_id, name, profile, entity_id, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET name=excluded.name, profile=excluded.profile,"
            " entity_id=excluded.entity_id, updated_at=excluded.updated_at",
            (c_id, ctx.story_id, c.name, c.profile, c.entity_id, ts, ts),
        )
        self.conn.commit()
        return c_id

    def get_characters(self, ctx: AgentContext) -> list[CharacterRow]:
        self.check_access(ctx, "characters", "read")
        return [
            CharacterRow(**dict(r))
            for r in self.conn.execute(
                "SELECT * FROM characters WHERE story_id=?", (ctx.story_id,)
            )
        ]

    # ================= 实体与链接(写者:角色管理)=================
    def upsert_entity(self, ctx: AgentContext, e: EntityRow) -> str:
        self.check_access(ctx, "entities", "write")
        e_id = e.id or new_id()
        ts = now_iso()
        self.conn.execute(
            "INSERT INTO entities (id, story_id, type, name, content, embedding, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET name=excluded.name, content=excluded.content,"
            " embedding=excluded.embedding, updated_at=excluded.updated_at",
            (e_id, ctx.story_id, e.type, e.name, e.content, e.embedding, ts, ts),
        )
        self.conn.commit()
        return e_id

    def add_entity_links(self, ctx: AgentContext, links: Sequence[EntityLink]) -> None:
        self.check_access(ctx, "entity_links", "write")
        self.conn.executemany(
            "INSERT INTO entity_links (id, story_id, from_entity, to_entity, relation)"
            " VALUES (?,?,?,?,?)",
            [(l.id or new_id(), ctx.story_id, l.from_entity, l.to_entity, l.relation) for l in links],
        )
        self.conn.commit()

    def get_linked_entities(self, ctx: AgentContext, entity_ids: Sequence[str]) -> list[EntityRow]:
        """链接扩展一跳:给定实体,返回其直接邻居(去重)。"""
        self.check_access(ctx, "entities", "read")
        if not entity_ids:
            return []
        ph = ",".join("?" * len(entity_ids))
        rows = self.conn.execute(
            f"SELECT DISTINCT e.* FROM entities e JOIN entity_links l"
            f" ON (e.id = l.to_entity AND l.from_entity IN ({ph}))"
            f" OR (e.id = l.from_entity AND l.to_entity IN ({ph}))"
            " WHERE e.story_id=?",
            (*entity_ids, *entity_ids, ctx.story_id),
        ).fetchall()
        seen, result = set(entity_ids), []
        for r in rows:
            if r["id"] not in seen:
                seen.add(r["id"])
                result.append(EntityRow(**dict(r)))
        return result

    # ================= 章节(写者:主控)=================
    def insert_chapter(self, ctx: AgentContext, c: ChapterRow) -> str:
        self.check_access(ctx, "chapters", "write")
        c_id = c.id or new_id()
        ts = now_iso()
        self.conn.execute(
            "INSERT INTO chapters (id, story_id, chapter_no, version_no, prev_version_id,"
            " title, content, status, branch_id, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (c_id, ctx.story_id, c.chapter_no, c.version_no, c.prev_version_id,
             c.title, c.content, c.status, c.branch_id, ts, ts),
        )
        self.conn.commit()
        return c_id

    def get_active_chapter(self, ctx: AgentContext, chapter_no: int) -> ChapterRow | None:
        self.check_access(ctx, "chapters", "read")
        r = self.conn.execute(
            "SELECT * FROM chapters WHERE story_id=? AND chapter_no=? AND status='active'",
            (ctx.story_id, chapter_no),
        ).fetchone()
        return ChapterRow(**dict(r)) if r else None

    # ================= 伏笔(写者:审校)=================
    def upsert_plot_thread(self, ctx: AgentContext, t: PlotThread) -> str:
        self.check_access(ctx, "plot_threads", "write")
        t_id = t.id or new_id()
        ts = now_iso()
        self.conn.execute(
            "INSERT INTO plot_threads (id, story_id, description, planted_chapter,"
            " resolved_chapter, status, branch_id, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET description=excluded.description,"
            " planted_chapter=excluded.planted_chapter, resolved_chapter=excluded.resolved_chapter,"
            " status=excluded.status, updated_at=excluded.updated_at",
            (t_id, ctx.story_id, t.description, t.planted_chapter, t.resolved_chapter,
             t.status, t.branch_id, ts, ts),
        )
        self.conn.commit()
        return t_id

    def get_plot_threads(self, ctx: AgentContext, status: str | None = None) -> list[PlotThread]:
        self.check_access(ctx, "plot_threads", "read")
        sql = "SELECT * FROM plot_threads WHERE story_id=?"
        args: list = [ctx.story_id]
        if status:
            sql += " AND status=?"
            args.append(status)
        return [PlotThread(**dict(r)) for r in self.conn.execute(sql, args)]

    # ================= 分层摘要(写者:主控,定稿管道步骤)=================
    def insert_summary(
        self, ctx: AgentContext, layer: str, content: str,
        *, chapter_no: int | None = None, embedding: bytes | None = None,
    ) -> str:
        """layer: chapter|volume|book。分层检索索引(ADR-0002)。"""
        self.check_access(ctx, "chapter_summaries", "write")
        s_id = new_id()
        self.conn.execute(
            "INSERT INTO chapter_summaries (id, story_id, chapter_no, layer, content,"
            " branch_id, embedding, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (s_id, ctx.story_id, chapter_no, layer, content,
             self.main_branch(ctx), embedding, now_iso()),
        )
        self.conn.commit()
        return s_id

    def get_summaries(self, ctx: AgentContext, layer: str) -> list[dict]:
        self.check_access(ctx, "chapter_summaries", "read")
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM chapter_summaries WHERE story_id=? AND layer=? ORDER BY chapter_no",
            (ctx.story_id, layer),
        )]

    # ================= 基础:story / branch =================
    def create_story(self, title: str, premise: str = "") -> tuple[str, str]:
        """建 story + 主线分支。返回 (story_id, main_branch_id)。"""
        story_id, branch_id = new_id(), new_id()
        ts = now_iso()
        self.conn.execute(
            "INSERT INTO stories (id, title, premise, status, main_branch_id, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (story_id, title, premise, "draft", branch_id, ts, ts),
        )
        self.conn.execute(
            "INSERT INTO branches (id, story_id, kind, status, created_at) VALUES (?,?,?,?,?)",
            (branch_id, story_id, "main", "active", ts),
        )
        self.conn.commit()
        return story_id, branch_id

    def main_branch(self, ctx: AgentContext) -> str:
        r = self.conn.execute(
            "SELECT main_branch_id FROM stories WHERE id=?", (ctx.story_id,)
        ).fetchone()
        if r is None:
            raise LookupError(f"story not found: {ctx.story_id}")
        return r["main_branch_id"]
