"""引擎组装:图 + checkpointer + repository + 检服务 的运行时依赖包。

图节点经 deps 访问持久层(不直接 import memory,便于测试注入);
CLI / API / 测试 复用同一引擎(ADR-0009:图与调用方解耦)。
"""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.core.llm.base import LLMResponse, UsageRecord
from app.core.llm.facade import LLMFacade
from app.db.database import init_db
from app.memory.repository import AgentContext, Repository
from app.memory.retrieval import RetrievalService
from app.memory.schemas import ChapterRow, Fact, VisibilityEntry
from app.observability.usage_log import make_usage_sink


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Deps:
    """节点可用的运行时依赖(经闭包注入图节点)。"""

    conn: sqlite3.Connection
    repo: Repository
    retrieval: RetrievalService
    llm: LLMFacade

    # ---- 节点辅助 ----
    def supervisor_ctx(self, story_id: str) -> AgentContext:
        return AgentContext("supervisor", story_id)

    def recent_carryover(self, state: dict) -> str:
        """短期记忆:最近 active 章的结尾原文 + 摘要(ADR-0003)。"""
        story_id = state.get("story_id", "")
        chapter_no = state.get("chapter_no", 1)
        rows = self.repo.conn.execute(
            "SELECT c.title, substr(c.content, -400) AS tail, s.content AS summary"
            " FROM chapters c LEFT JOIN chapter_summaries s"
            "   ON s.story_id = c.story_id AND s.chapter_no = c.chapter_no AND s.layer='chapter'"
            " WHERE c.story_id=? AND c.status='active' AND c.chapter_no < ?"
            " ORDER BY c.chapter_no DESC LIMIT 1",
            (story_id, chapter_no),
        ).fetchall()
        if not rows:
            return "(本书第一章)"
        r = rows[0]
        return f"上一章摘要:{r['summary'] or ''}\n上一章结尾:{r['tail'] or ''}"

    def extract_stage_line(self, stage_outline: str, chapter_no: int) -> str:
        """从阶段细纲提取本章行(确定性切片;兜底返回整份细纲)。"""
        for line in stage_outline.splitlines():
            if re.match(rf"\s*[-*]?\s*第?{chapter_no}[章|、|\s]", line):
                return line
        return stage_outline[:300]

    def character_name_map(self, state: dict) -> dict[str, str]:
        ctx = AgentContext("supervisor", state.get("story_id", ""))
        return {c.name: c.id for c in self.repo.get_characters(ctx)}

    def persist_characters(self, state: dict) -> list[str]:
        """共创:角色草案落库,返回 [name -> id 映射更新到 drafts]。"""
        ctx = AgentContext("character_manager", state["story_id"])
        ids = []
        for d in state.get("character_drafts", []):
            from app.memory.schemas import CharacterRow
            cid = self.repo.upsert_character(ctx, CharacterRow(
                id="", story_id="", name=d.get("name", "未命名"),
                profile=d.get("profile", "")))
            d["id"] = cid
            ids.append(cid)
        return ids

    def log_review(self, state: dict, *, reviewer: str, verdict: dict, round_no: int) -> None:
        self.conn.execute(
            "INSERT INTO review_results (id, story_id, chapter_no, round_no, reviewer,"
            " verdict, scores, feedback, forced_pass, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (uuid.uuid4().hex, state.get("story_id", ""), state.get("chapter_no"),
             round_no, reviewer, verdict.get("verdict", "revise"),
             json.dumps(verdict.get("scores", {}), ensure_ascii=False),
             verdict.get("feedback", ""),
             1 if state.get("forced_pass") else 0, _now()),
        )
        self.conn.commit()

    # ---- 定稿:编排原子性落库(单事务,ADR-0003 写链路)----
    def commit_finalize(self, state: dict) -> str:
        """定稿 DB 事务:章节 + 事实/认知/可见性 + 伏笔 + 摘要,一次提交。
        失败整体回滚,世界状态零污染。"""
        story_id = state["story_id"]
        branch = state["branch_id"]
        chapter_no = state["chapter_no"]
        changes = state.get("fact_changes", {})
        chapter_id = uuid.uuid4().hex
        try:
            self.conn.execute("BEGIN")
            # 1) 章节(active)
            self.conn.execute(
                "INSERT INTO chapters (id, story_id, chapter_no, version_no, title, content,"
                " status, branch_id, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (chapter_id, story_id, chapter_no, 1,
                 f"第{chapter_no}章", state.get("draft", ""), "active", branch, _now(), _now()),
            )
            # 2) facts + visibility
            fact_ids: list[str] = []
            for f in changes.get("facts", []):
                fid = uuid.uuid4().hex
                fact_ids.append(fid)
                self.conn.execute(
                    "INSERT INTO facts (id, story_id, type, content, chapter_established,"
                    " branch_id, prev_version_id, confidence, status, embedding, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (fid, story_id, f.get("type", "event"), f.get("content", ""),
                     chapter_no, branch, None, f.get("confidence", "high"),
                     "pending_review" if f.get("confidence") == "low" else "confirmed",
                     None, _now()),
                )
                for cid in f.get("visible_ids", []):
                    self.conn.execute(
                        "INSERT OR REPLACE INTO fact_visibility"
                        " (fact_id, character_id, knowledge_level, detail, learned_chapter, branch_id)"
                        " VALUES (?,?,?,?,?,?)",
                        (fid, cid, "known_full", None, chapter_no, branch),
                    )
            # 3) beliefs
            for b in changes.get("beliefs", []):
                if not b.get("character_id"):
                    continue
                self.conn.execute(
                    "INSERT INTO beliefs (id, story_id, character_id, content, source_fact_id,"
                    " status, established_chapter, dispelled_chapter, branch_id,"
                    " prev_version_id, embedding, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (uuid.uuid4().hex, story_id, b["character_id"], b.get("content", ""),
                     None, "believed", chapter_no, None, branch, None, None, _now()),
                )
            # 4) 角色卡更新
            for u in state.get("character_changes", []):
                if not u.get("character_id"):
                    continue
                self.conn.execute(
                    "UPDATE characters SET profile = COALESCE(profile,'') || char(10) || ?,"
                    " updated_at=? WHERE id=?",
                    (u.get("profile_append", ""), _now(), u["character_id"]),
                )
            # 5) 伏笔(人工已确认的 thread_changes)
            for t in state.get("thread_changes", []):
                action = t.get("action", "plant")
                if action == "plant":
                    self.conn.execute(
                        "INSERT INTO plot_threads (id, story_id, description, planted_chapter,"
                        " resolved_chapter, status, branch_id, created_at, updated_at)"
                        " VALUES (?,?,?,?,?,?,?,?,?)",
                        (uuid.uuid4().hex, story_id, t.get("description", ""), chapter_no,
                         None, "open", branch, _now(), _now()),
                    )
                else:
                    # advance/resolve/drop:按描述匹配最近 open 线
                    row = self.conn.execute(
                        "SELECT id FROM plot_threads WHERE story_id=? AND status='open'"
                        " AND description LIKE ? ORDER BY planted_chapter DESC LIMIT 1",
                        (story_id, f"%{t.get('description','')[:12]}%"),
                    ).fetchone()
                    if row:
                        status = {"advance": "open", "resolve": "resolved", "drop": "dropped"}[action]
                        self.conn.execute(
                            "UPDATE plot_threads SET status=?, resolved_chapter=?, updated_at=?"
                            " WHERE id=?",
                            (status, chapter_no if action != "advance" else None, _now(), row["id"]),
                        )
            # 6) 章摘要(检索索引)
            self.conn.execute(
                "INSERT INTO chapter_summaries (id, story_id, chapter_no, layer, content,"
                " branch_id, embedding, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, story_id, chapter_no, "chapter",
                 state.get("chapter_summary", ""), branch, None, _now()),
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return chapter_id


def build_engine(db_path: str | Path, llm: LLMFacade | None = None,
                 embed_fn=None) -> tuple[Deps, sqlite3.Connection]:
    """组装引擎(生产入口;测试注入 fake llm/embed)。"""
    conn = init_db(db_path)
    repo = Repository(conn)
    facade = llm or LLMFacade()
    facade.set_usage_sink(make_usage_sink(conn))
    retrieval = RetrievalService(repo, embed_fn=embed_fn)
    return Deps(conn=conn, repo=repo, retrieval=retrieval, llm=facade), conn
