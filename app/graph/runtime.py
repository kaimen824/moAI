"""引擎组装:图 + checkpointer + repository + 检服务 的运行时依赖包。

图节点经 deps 访问持久层(不直接 import memory,便于测试注入);
CLI / API / 测试 复用同一引擎(ADR-0009:图与调用方解耦)。
"""

from __future__ import annotations

import json
import queue
import re
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.core.llm.base import LLMResponse, UsageRecord
from app.core.llm.facade import LLMFacade
from app.db.database import init_db
from app.memory.entity import EntityService
from app.memory.repository import AgentContext, Repository
from app.memory.retrieval import RetrievalService, encode_embedding
from app.memory.schemas import ChapterRow, EntityLink, EntityRow, Fact, VisibilityEntry
from app.observability.usage_log import make_usage_sink


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class StopRequested(Exception):
    """用户请求中断(协作式停止):节点入口检查抛出,worker 捕获后发 stopped 事件。

    checkpointer 保留最后完成的节点状态,重新 generate 从断点续跑。
    """


@dataclass
class Deps:
    """节点可用的运行时依赖(经闭包注入图节点)。"""

    conn: sqlite3.Connection
    repo: Repository
    retrieval: RetrievalService
    llm: LLMFacade
    entities: EntityService | None = None    # 实体层(ADR-0015:消歧漏斗/合并执行)
    embed_fn: object = None              # texts -> vectors(定稿时为 facts/摘要算 embedding)
    # SSE 事件总线:按 story(thread)隔离的广播 + 历史(刷新/断线后前端可恢复流程)
    _subscribers: list = field(default_factory=list)   # [(thread_id, queue)]
    _history: list = field(default_factory=list)       # [(kind, data, thread_id)] 全局环形,按 thread 过滤
    _current_thread: str = ""                          # 单任务串行约束下的当前运行 thread
    # 引擎级可重入互斥:全部 DB 访问(repo 方法 / usage sink / checkpointer / 端点)
    # 各自持锁做短临界区。不全程锁图执行——LangGraph fan-out 节点跑在独立线程。
    run_lock: threading.RLock = field(default_factory=threading.RLock)
    checkpointer: object = None
    # 协作式停止:用户中断按钮置位 -> 节点入口检查抛 StopRequested(行业惯例:
    # 不硬杀线程,在步骤边界安全退出,checkpointer 状态保留可续跑)
    _stop_requests: set = field(default_factory=set)

    def request_stop(self, story_id: str) -> None:
        self._stop_requests.add(story_id)

    def clear_stop(self, story_id: str) -> None:
        self._stop_requests.discard(story_id)

    def check_stop(self, story_id: str) -> None:
        """节点入口调用:置位即抛 StopRequested(由 _node 统一注入)。"""
        if story_id in self._stop_requests:
            raise StopRequested(story_id)

    def emit(self, kind: str, data: dict, thread_id: str | None = None) -> None:
        """广播事件(按 thread 订阅者)并留存历史。节点内调用走 _current_thread。"""
        tid = thread_id or self._current_thread
        self._history.append((kind, data, tid))
        if len(self._history) > 2000:
            del self._history[: len(self._history) - 2000]
        for sub_tid, q in list(self._subscribers):
            if sub_tid == tid:
                try:
                    q.put((kind, data))
                except Exception:
                    pass

    def subscribe(self, thread_id: str):
        """新增订阅者:先重放该 thread 历史,再接收后续事件。"""
        q: queue.Queue = queue.Queue()
        for k, d, tid in self._history:
            if tid == thread_id:
                q.put((k, d))
        self._subscribers.append((thread_id, q))
        return q

    def unsubscribe(self, q) -> None:
        self._subscribers = [(t, x) for t, x in self._subscribers if x is not q]

    def snapshot(self, thread_id: str) -> list:
        return [(k, d) for k, d, tid in self._history if tid == thread_id]

    def clear_events(self, thread_id: str) -> None:
        self._history = [(k, d, t) for k, d, t in self._history if t != thread_id]
        self._subscribers = [(t, q) for t, q in self._subscribers if t != thread_id]

    # ---- 用户指令通道(任意时刻输入,生成时消费)----
    def record_directive(self, story_id: str, content: str) -> str:
        """用户随时提交的指示;在下一次 build_context 时被主控消费。"""
        with self.run_lock:
            did = uuid.uuid4().hex
            self.conn.execute(
                "INSERT INTO user_directives (id, story_id, content, consumed_at, created_at)"
                " VALUES (?,?,?,?,?)",
                (did, story_id, content, None, _now()),
            )
            self.conn.commit()
            return did

    def take_pending_directives(self, story_id: str) -> list[str]:
        """取走未消费指令(消费即标记);build_context 调用。"""
        with self.run_lock:
            rows = self.conn.execute(
                "SELECT id, content FROM user_directives"
                " WHERE story_id=? AND consumed_at IS NULL ORDER BY created_at",
                (story_id,),
            ).fetchall()
            for r in rows:
                self.conn.execute(
                    "UPDATE user_directives SET consumed_at=? WHERE id=?",
                    (_now(), r["id"]),
                )
            self.conn.commit()
            return [r["content"] for r in rows]

    # ---- 节点辅助 ----
    def supervisor_ctx(self, story_id: str) -> AgentContext:
        return AgentContext("supervisor", story_id)

    def recent_carryover(self, state: dict) -> str:
        """短期记忆:最近 2 章摘要 + 上一章结尾原文(ADR-0003)。"""
        story_id = state.get("story_id", "")
        chapter_no = state.get("chapter_no", 1)
        rows = self.repo.conn.execute(
            "SELECT c.chapter_no, substr(c.content, -400) AS tail, s.content AS summary"
            " FROM chapters c LEFT JOIN chapter_summaries s"
            "   ON s.story_id = c.story_id AND s.chapter_no = c.chapter_no AND s.layer='chapter'"
            " WHERE c.story_id=? AND c.status='active' AND c.chapter_no < ?"
            " ORDER BY c.chapter_no DESC LIMIT 2",
            (story_id, chapter_no),
        ).fetchall()
        if not rows:
            return "(本书第一章)"
        parts = [f"上一章(ch{r['chapter_no']})摘要:{r['summary'] or ''}" for r in reversed(rows)]
        parts.append(f"上一章结尾原文:{rows[0]['tail'] or ''}")
        return "\n".join(parts)

    def stage_chapter_summaries(self, story_id: str, stage_start: int, *,
                                upto: int) -> list[tuple[int, str]]:
        """当前阶段内(>=stage_start, <=upto)的定稿章摘要序列。"""
        rows = self.repo.conn.execute(
            "SELECT s.chapter_no, s.content FROM chapter_summaries s"
            " JOIN chapters c ON c.story_id = s.story_id AND c.chapter_no = s.chapter_no"
            "   AND c.status='active' AND c.branch_id = s.branch_id"
            " WHERE s.story_id=? AND s.layer='chapter'"
            "   AND s.chapter_no >= ? AND s.chapter_no <= ?"
            " ORDER BY s.chapter_no",
            (story_id, stage_start, upto),
        ).fetchall()
        return [(r["chapter_no"], r["content"] or "") for r in rows]

    def story_recap(self, state: dict) -> str:
        """已完成剧情回顾(分层记忆,防长篇上下文膨胀):

        - 更早阶段:各阶段聚合摘要(layer='stage',每条截 400 字)
        - 当前阶段:全量章摘要(细纲单元粒度,3-6 章,保真)
        - 细纲进度:当前细纲行中已写完/待写的章号(防重排锚点)
        stage_start 缺失(旧 checkpoint)时兜底为最近 10 章。
        """
        story_id = state.get("story_id", "")
        chapter_no = state.get("chapter_no", 1)
        stage_start = state.get("stage_start_chapter", 0)
        if stage_start <= 0:   # 旧状态兜底:最近 10 章视为"当前阶段"
            stage_start = max(chapter_no - 10, 1)

        stage_rows = self.repo.conn.execute(
            "SELECT s.chapter_no, s.content FROM chapter_summaries s"
            " WHERE s.story_id=? AND s.layer='stage' AND s.chapter_no < ?"
            " ORDER BY s.chapter_no",
            (story_id, stage_start),
        ).fetchall()
        chapter_rows = self.stage_chapter_summaries(story_id, stage_start, upto=chapter_no - 1)

        if not stage_rows and not chapter_rows:
            return "(尚无已完成章节)"

        parts: list[str] = []
        if stage_rows:
            parts.append("[早期剧情(阶段聚合)]")
            parts.extend(f"- (至第{r['chapter_no']}章){(r['content'] or '')[:400]}"
                         for r in stage_rows)
        if chapter_rows:
            parts.append(f"[当前阶段(第{stage_start}章起,全量)]")
            parts.extend(f"- 第{no}章:{text[:200]}" for no, text in chapter_rows)

        # 细纲进度指针:已写完的细纲行 vs 待写(防重排的直接锚点)
        outline = state.get("stage_outline", "")
        if outline:
            done_lines, todo_lines = [], []
            for line in outline.splitlines():
                m = re.match(rf"\s*[-*]?\s*第?(\d+)[章|、|\s]", line)
                if not m:
                    continue
                (done_lines if int(m.group(1)) < chapter_no else todo_lines).append(line.strip())
            if done_lines or todo_lines:
                parts.append("[当前细纲进度(已完成行严禁重写)]")
                parts.extend(f"  已写完: {l}" for l in done_lines[:8])
                parts.extend(f"  待写:   {l}" for l in todo_lines[:8])
        return "\n".join(parts)

    def parse_stage_range(self, stage_outline: str, *, start: int) -> int:
        """解析细纲覆盖的末章章号(阶段边界);解析不出时保守取 start+2。"""
        nums = [int(m) for m in re.findall(r"第?(\d+)[章|、|\s]", stage_outline)]
        nums = [n for n in nums if n >= start]
        return max(nums) if nums else start + 2

    def extract_stage_line(self, stage_outline: str, chapter_no: int) -> str:
        """从阶段细纲提取本章行(确定性切片;兜底返回整份细纲)。"""
        for line in stage_outline.splitlines():
            if re.match(rf"\s*[-*]?\s*第?{chapter_no}[章|、|\s]", line):
                return line
        return stage_outline[:300]

    def character_name_map(self, state: dict) -> dict[str, str]:
        """名字 -> 角色 id(ADR-0015 升级:并入实体别名表,修别称盲区)。

        本名精确优先;别名(道号/俗称/尊称)经 characters.entity_id 关联补入。
        消歧正确性由别名注册时的实体层保证(同书别名唯一,先注册者得)。
        """
        story_id = state.get("story_id", "")
        ctx = AgentContext("supervisor", story_id)
        mapping = {c.name: c.id for c in self.repo.get_characters(ctx)}
        rows = self.repo.conn.execute(
            "SELECT a.alias, c.id FROM entity_aliases a"
            " JOIN characters c ON c.entity_id = a.entity_id AND c.story_id = a.story_id"
            " WHERE a.story_id=?", (story_id,)).fetchall()
        for r in rows:
            mapping.setdefault(r["alias"], r["id"])
        return mapping

    def persist_characters(self, state: dict) -> list[str]:
        """共创落库(确认总大纲时):角色卡 + 实体种子(ADR-0015)。

        角色实体由代码确定性生成(与角色卡同名同文,零对齐风险),entity_id 回写
        characters——检索链接扩展与别名识别从第一章即生效;势力/地点/物品等种子
        实体与链接来自共创 LLM 抽取(entity_drafts 暂存,确认前零残留)。
        实体写走 entity_manager 身份(单写者不变式);embedding 失败降级 None。
        """
        story_id = state["story_id"]
        ctx = AgentContext("character_manager", story_id)
        ectx = AgentContext("entity_manager", story_id)
        seeds = state.get("entity_drafts") or {}
        char_aliases = {a.get("name"): (a.get("aliases") or [])
                        for a in seeds.get("character_aliases", [])}

        def _upsert_alias(alias: str, entity_id: str) -> None:
            alias = (alias or "").strip()
            if alias:
                self.conn.execute(
                    "INSERT OR REPLACE INTO entity_aliases"
                    " (alias, story_id, entity_id, created_at) VALUES (?,?,?,?)",
                    (alias, story_id, entity_id, _now()))

        with self.run_lock:
            name_to_eid: dict[str, str] = {}
            ids: list[str] = []
            # 1) 角色卡 + 角色实体(确定性)
            for d in state.get("character_drafts", []):
                from app.memory.schemas import CharacterRow
                name = d.get("name", "未命名")
                cid = self.repo.upsert_character(ctx, CharacterRow(
                    id="", story_id="", name=name,
                    profile=d.get("profile", "")))
                d["id"] = cid
                ids.append(cid)
                eid = self.repo.upsert_entity(ectx, EntityRow(
                    id="", story_id="", type="character", name=name,
                    content=d.get("profile", "")))
                d["entity_id"] = eid
                name_to_eid[name] = eid
                self.conn.execute(
                    "UPDATE characters SET entity_id=?, updated_at=? WHERE id=?",
                    (eid, _now(), cid))
                for a in char_aliases.get(name, []):
                    _upsert_alias(a, eid)
            # 2) 种子实体(势力/地点/物品/功法/概念)
            for e in seeds.get("entities", []):
                name = (e.get("name") or "").strip()
                if not name or name in name_to_eid:
                    continue
                eid = self.repo.upsert_entity(ectx, EntityRow(
                    id="", story_id="", type=e.get("type", "concept"),
                    name=name, content=e.get("content", "")))
                name_to_eid[name] = eid
                for a in e.get("aliases", []) or []:
                    _upsert_alias(a, eid)
            # 3) 初始链接(from/to 名字对;解析不了的丢弃)
            links = [EntityLink(id="", story_id="",
                                from_entity=name_to_eid[(l.get("from") or "").strip()],
                                to_entity=name_to_eid[(l.get("to") or "").strip()],
                                relation=(l.get("relation") or "")[:120])
                     for l in seeds.get("links", [])
                     if (l.get("from") or "").strip() in name_to_eid
                     and (l.get("to") or "").strip() in name_to_eid]
            if links:
                self.repo.add_entity_links(ectx, links)
            # 4) 种子实体 embedding(消歧向量层;失败降级 None 不阻塞)
            if self.embed_fn is not None:
                ents = self.conn.execute(
                    "SELECT id, name, content FROM entities"
                    " WHERE story_id=? AND embedding IS NULL", (story_id,)).fetchall()
                if ents:
                    try:
                        vectors = self.embed_fn(
                            [f"{r['name']}:{(r['content'] or '')[:200]}" for r in ents])
                        for r, v in zip(ents, vectors):
                            if v:
                                self.conn.execute(
                                    "UPDATE entities SET embedding=? WHERE id=?",
                                    (encode_embedding(v), r["id"]))
                    except Exception:
                        pass
            self.conn.commit()
        return ids

    def log_review(self, state: dict, *, reviewer: str, verdict: dict, round_no: int,
                   forced: bool | None = None) -> None:
        # forced=None:读 state 中的 rewrite_exhausted(评审节点);
        # 显式传入:merge 等在标志写入前落审计的调用方。
        if forced is None:
            forced = bool(state.get("rewrite_exhausted"))
        with self.run_lock:
            self.conn.execute(
                "INSERT INTO review_results (id, story_id, chapter_no, round_no, reviewer,"
                " verdict, scores, feedback, forced_pass, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, state.get("story_id", ""), state.get("chapter_no"),
                 round_no, reviewer, verdict.get("verdict", "revise"),
                 json.dumps(verdict.get("scores", {}), ensure_ascii=False),
                 verdict.get("feedback", ""),
                 1 if forced else 0, _now()),
            )
            self.conn.commit()

    # ---- 实体合并提案:人工裁决入口(API 层)----

    def resolve_entity_proposal(self, proposal_id: str, action: str) -> dict:
        """裁决合并提案:merge=执行归一(链接重定向+别名吸收);new/ignore=登记裁决。

        裁决持久生效(ADR-0015):后续同名候选按此规则自动处理,不再重复入队。
        """
        with self.run_lock:
            row = self.conn.execute(
                "SELECT * FROM entity_merge_proposals WHERE id=? AND status='pending'",
                (proposal_id,)).fetchone()
            if not row:
                raise LookupError(f"pending proposal not found: {proposal_id}")
            p = dict(row)
            if action == "merge":
                self.entities.execute_merge(self.conn, p)
            else:
                self.conn.execute(
                    "UPDATE entity_merge_proposals SET status=?, decided_at=? WHERE id=?",
                    ("new" if action == "new" else "ignored", _now(), proposal_id))
            self.conn.commit()
            return p

    # ---- 定稿:编排原子性落库(单事务,ADR-0003 写链路)----
    def _fact_supersede_target(self, story_id: str, branch: str, f: dict,
                               chapter_no: int) -> str | None:
        """求新事实的 prev_version_id(推翻链):
        ① LLM 显式 supersedes(模糊匹配已知事实,取最近一条);
        ② setting 类兜底:接在当前仍有效的最新 setting 链尾之后,
           保证任一时点只有最新场景环境生效(时间线不回漂)。
        """
        sup = (f.get("supersedes") or "").strip()
        if sup:
            row = self.conn.execute(
                "SELECT id FROM facts WHERE story_id=? AND branch_id=?"
                " AND chapter_established<? AND content LIKE ?"
                " AND status!='rejected' ORDER BY chapter_established DESC LIMIT 1",
                (story_id, branch, chapter_no, f"%{sup[:24]}%"),
            ).fetchone()
            if row:
                return row["id"]
        if f.get("type") == "setting":
            row = self.conn.execute(
                # 仍有效 = 没有任何后续版本指向它(NOT EXISTS,与 world 回放口径一致)
                "SELECT f.id, f.chapter_established FROM facts f"
                " LEFT JOIN facts g ON g.prev_version_id = f.id"
                " WHERE f.story_id=? AND f.branch_id=? AND f.type='setting'"
                "   AND f.status!='rejected' AND g.id IS NULL"
                " ORDER BY f.chapter_established DESC LIMIT 1",
                (story_id, branch),
            ).fetchone()
            if row:
                return row["id"]
        return None

    def commit_finalize(self, state: dict) -> str:
        """定稿 DB 事务:章节 + 事实/认知/可见性 + 伏笔 + 摘要,一次提交。
        失败整体回滚,世界状态零污染。"""
        story_id = state["story_id"]
        branch = state["branch_id"]
        chapter_no = state["chapter_no"]
        changes = state.get("fact_changes", {})
        chapter_id = uuid.uuid4().hex

        # 事务外预计算 embedding(facts 去重后的内容 + 章摘要 + 新实体条目);
        # 失败降级为 None——不阻塞定稿,仅损失向量检索能力
        emb_contents: list[str] = [f.get("content", "").strip()
                                   for f in changes.get("facts", [])]
        emb_contents.append(state.get("chapter_summary", ""))
        entity_changes = state.get("entity_changes") or {}
        ent_emb_start = len(emb_contents)
        emb_contents.extend(
            f"{e['name']}:{e.get('content', '')}" for e in entity_changes.get("new_entities", []))
        emb_vecs: list[bytes | None] = [None] * len(emb_contents)
        if self.embed_fn is not None:
            try:
                texts = [t for t in emb_contents if t]
                if texts:
                    vectors = self.embed_fn(texts) or []
                    vi = 0
                    for i, t in enumerate(emb_contents):
                        if t and vi < len(vectors) and vectors[vi]:
                            emb_vecs[i] = encode_embedding(vectors[vi])
                            vi += 1
            except Exception:
                emb_vecs = [None] * len(emb_contents)

        try:
            self.run_lock.acquire()   # 显式事务:跨越整个 BEGIN..COMMIT 的临界区
            self.conn.execute("BEGIN")
            # 1) 章节(active)
            self.conn.execute(
                "INSERT INTO chapters (id, story_id, chapter_no, version_no, title, content,"
                " status, branch_id, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (chapter_id, story_id, chapter_no, 1,
                 f"第{chapter_no}章", state.get("draft", ""), "active", branch, _now(), _now()),
            )
            # 2) facts + visibility(精确去重:同分支同内容已存在则跳过)
            fact_ids: list[str] = []
            for i, f in enumerate(changes.get("facts", [])):
                content = (f.get("content") or "").strip()
                if not content:
                    continue
                dup = self.conn.execute(
                    "SELECT id FROM facts WHERE story_id=? AND branch_id=?"
                    " AND content=? AND status!='rejected' LIMIT 1",
                    (story_id, branch, content),
                ).fetchone()
                if dup:
                    continue     # 语义去重由抽取层负责;此处拦精确重复
                fid = uuid.uuid4().hex
                fact_ids.append(fid)
                self.conn.execute(
                    "INSERT INTO facts (id, story_id, type, content, chapter_established,"
                    " branch_id, prev_version_id, confidence, status, embedding, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (fid, story_id, f.get("type", "event"), content,
                     chapter_no, branch,
                     self._fact_supersede_target(story_id, branch, f, chapter_no),
                     f.get("confidence", "high"),
                     "pending_review" if f.get("confidence") == "low" else "confirmed",
                     emb_vecs[i] if i < len(emb_vecs) else None, _now()),
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
            # 6) 章摘要(检索索引)+ 阶段聚合摘要(分层记忆)
            summary_vec = emb_vecs[-1] if emb_vecs else None
            self.conn.execute(
                "INSERT INTO chapter_summaries (id, story_id, chapter_no, layer, content,"
                " branch_id, embedding, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, story_id, chapter_no, "chapter",
                 state.get("chapter_summary", ""), branch, summary_vec, _now()),
            )
            if state.get("stage_summary"):
                self.conn.execute(
                    "INSERT INTO chapter_summaries (id, story_id, chapter_no, layer, content,"
                    " branch_id, embedding, created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (uuid.uuid4().hex, story_id, chapter_no, "stage",
                     state["stage_summary"], branch, None, _now()),
                )
            # 7) 实体族(ADR-0015):新实体/别名/链接/合并提案 + 阶段末条目滚动
            for i, e in enumerate(entity_changes.get("new_entities", [])):
                self.conn.execute(
                    "INSERT INTO entities (id, story_id, type, name, content, embedding,"
                    " chapter_no, status, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (e["id"], story_id, e.get("type", "concept"), e["name"],
                     e.get("content", ""),
                     emb_vecs[ent_emb_start + i] if ent_emb_start + i < len(emb_vecs) else None,
                     chapter_no, "active", _now(), _now()),
                )
            for a in entity_changes.get("aliases", []):
                self.conn.execute(
                    "INSERT OR REPLACE INTO entity_aliases"
                    " (alias, story_id, entity_id, created_at) VALUES (?,?,?,?)",
                    (a["alias"], story_id, a["entity_id"], _now()),
                )
            for l in entity_changes.get("links", []):
                self.conn.execute(
                    "INSERT INTO entity_links (id, story_id, from_entity, to_entity,"
                    " relation, chapter_no) VALUES (?,?,?,?,?,?)",
                    (l["id"], story_id, l["from_entity"], l["to_entity"],
                     l.get("relation", ""), l.get("chapter_no")),
                )
            for p in entity_changes.get("proposals", []):
                self.conn.execute(
                    "INSERT INTO entity_merge_proposals (id, story_id, candidate_name,"
                    " candidate_entity_id, target_entity_id, similarity, evidence,"
                    " status, chapter_no, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (uuid.uuid4().hex, story_id, p["candidate_name"],
                     p.get("candidate_entity_id"), p["target_entity_id"],
                     p.get("similarity"), p.get("evidence", ""),
                     "pending", p.get("chapter_no"), _now()),
                )
            for u in state.get("entity_content_updates") or []:
                if u.get("entity_id") and u.get("content"):
                    self.conn.execute(
                        "UPDATE entities SET content=?, updated_at=? WHERE id=? AND story_id=?",
                        (u["content"], _now(), u["entity_id"], story_id),
                    )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        finally:
            self.run_lock.release()
        return chapter_id


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
    facade.set_usage_sink(make_usage_sink(conn, deps.run_lock))
    # 全节点可观测:每次 LLM 调用 -> SSE agent_call 事件(实时)+ agent_traces 落库(回溯)
    def _trace_sink(rec: dict) -> None:
        deps.emit("agent_call", rec)
        with deps.run_lock:
            conn.execute(
                "INSERT INTO agent_traces (id, story_id, agent, model, stage, input_text,"
                " output_text, tokens_in, tokens_out, latency_ms, trace_id, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, rec.get("story_id", ""), rec.get("agent", ""),
                 rec.get("model", ""), rec.get("stage", ""),
                 "\n\n".join(f"[{m['role']}]\n{m['content']}" for m in rec.get("input", []))[:4000],
                 (rec.get("output", "") or "")[:8000],
                 rec.get("tokens_in", 0), rec.get("tokens_out", 0),
                 rec.get("latency_ms", 0), rec.get("trace_id", ""), _now()),
            )
            conn.commit()
    facade.set_trace_sink(_trace_sink)
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
