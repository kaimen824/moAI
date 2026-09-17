"""UI 读侧查询与用户域存取(ADR-0030 阶段3):api 层内联 SQL 的收敛归位。

与 memory.repository 的分工:Repository 是 Agent 侧 ACL 仓储(AgentContext
身份,agent_acl 表级权限);本模块是用户侧读模型(owner/member 鉴权在
api 层 require_story 完成后才到这里),方法即 UI 视图,无身份对象。

方法体自 routes 逐字迁移,行为零变化;不加锁(与原 routes 裸调用一致,
互斥由引擎锁在写路径保障)。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone


class StoryQueries:
    """小说域 UI 读模型:列表/详情/章节/Codex/指令计数。"""

    def __init__(self, conn) -> None:
        self.conn = conn

    def list_for_user(self, user_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT s.*, (SELECT COUNT(*) FROM chapters c"
            "  WHERE c.story_id = s.id AND c.status='active') AS chapter_count"
            " FROM stories s"
            " WHERE s.owner_id=? OR s.id IN"
            "   (SELECT story_id FROM story_members WHERE user_id=?)"
            " ORDER BY s.created_at DESC", (user_id, user_id)).fetchall()
        return [dict(r) for r in rows]

    def story_row(self, story_id: str):
        """story 行(generate 组装共创输入/取主分支)。"""
        return self.conn.execute("SELECT * FROM stories WHERE id=?", (story_id,)).fetchone()

    def detail(self, story_id: str) -> dict:
        story = self.story_row(story_id)
        if not story:
            raise LookupError(story_id)
        chapters = self.conn.execute(
            "SELECT id, chapter_no, version_no, title, status, updated_at,"
            " length(content) AS clen FROM chapters"
            " WHERE story_id=? AND status='active' ORDER BY chapter_no", (story_id,)).fetchall()
        outline = self.conn.execute(
            "SELECT content FROM outlines WHERE story_id=? AND status='confirmed'"
            " ORDER BY version_no DESC LIMIT 1", (story_id,)).fetchone()
        characters = self.conn.execute(
            "SELECT id, name, profile FROM characters WHERE story_id=?", (story_id,)).fetchall()
        threads = self.conn.execute(
            "SELECT * FROM plot_threads WHERE story_id=?", (story_id,)).fetchall()
        return {
            "story": dict(story),
            "outline": outline["content"] if outline else None,
            "chapters": [dict(c) for c in chapters],
            "characters": [dict(c) for c in characters],
            "plot_threads": [dict(t) for t in threads],
        }

    def active_chapter(self, story_id: str, chapter_no: int):
        return self.conn.execute(
            "SELECT * FROM chapters WHERE story_id=? AND chapter_no=? AND status='active'",
            (story_id, chapter_no)).fetchone()

    def codex(self, story_id: str, branch: str) -> dict:
        """设定集(Codex):角色卡 + 伏笔台账 + 当前有效世界记忆(排除被推翻/拒绝)。

        facts 推翻链与 world 回放同口径:有后续版本指向即失效,任一时点只呈现有效记忆。
        """
        characters = self.conn.execute(
            "SELECT id, name, profile FROM characters WHERE story_id=? ORDER BY created_at",
            (story_id,)).fetchall()
        threads = self.conn.execute(
            "SELECT id, description, planted_chapter, resolved_chapter, status"
            " FROM plot_threads WHERE story_id=? ORDER BY planted_chapter",
            (story_id,)).fetchall()
        facts = self.conn.execute(
            "SELECT f.type, f.content, f.chapter_established, f.confidence"
            " FROM facts f"
            " WHERE f.story_id=? AND f.branch_id=? AND f.status!='rejected'"
            "   AND NOT EXISTS ("
            "     SELECT 1 FROM facts g"
            "     WHERE g.prev_version_id = f.id AND g.branch_id = f.branch_id)"
            " ORDER BY f.chapter_established DESC, f.rowid DESC LIMIT 300",
            (story_id, branch)).fetchall()
        outline = self.conn.execute(
            "SELECT content FROM outlines WHERE story_id=? AND status='confirmed'"
            " ORDER BY version_no DESC LIMIT 1", (story_id,)).fetchone()
        # 实体图(ADR-0015):active 条目 + 链接(带双方名字,前端直接渲染)
        entities = self.conn.execute(
            "SELECT id, type, name, content, chapter_no FROM entities"
            " WHERE story_id=? AND status='active' ORDER BY COALESCE(chapter_no, 0), created_at",
            (story_id,)).fetchall()
        links = self.conn.execute(
            "SELECT l.relation, l.chapter_no, ef.name AS from_name, et.name AS to_name"
            " FROM entity_links l"
            " JOIN entities ef ON ef.id = l.from_entity"
            " JOIN entities et ON et.id = l.to_entity"
            " WHERE l.story_id=? ORDER BY COALESCE(l.chapter_no, 0)", (story_id,)).fetchall()
        return {
            "characters": [dict(r) for r in characters],
            "plot_threads": [dict(r) for r in threads],
            "facts": [dict(r) for r in facts],
            "entities": [dict(r) for r in entities],
            "entity_links": [dict(r) for r in links],
            "outline": outline["content"] if outline else None,
        }

    def main_branch(self, story_id: str) -> str:
        row = self.conn.execute("SELECT main_branch_id FROM stories WHERE id=?",
                                (story_id,)).fetchone()
        return row["main_branch_id"] if row else ""

    def pending_directive_count(self, story_id: str) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) c FROM user_directives WHERE story_id=? AND consumed_at IS NULL",
            (story_id,),
        ).fetchone()["c"]


class ReviewQueues:
    """审核队列读模型:事实抽检(E3)与实体合并提案(ADR-0015)。"""

    def __init__(self, conn) -> None:
        self.conn = conn

    def pending_facts(self, story_ids: list[str]) -> list[dict]:
        marks = ",".join("?" * len(story_ids))
        rows = self.conn.execute(
            "SELECT f.*, s.title AS story_title FROM facts f JOIN stories s ON s.id=f.story_id"
            f" WHERE f.status='pending_review' AND f.story_id IN ({marks})"
            " ORDER BY f.created_at", story_ids).fetchall()
        return [dict(r) for r in rows]

    def fact_story_id(self, fact_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT story_id FROM facts WHERE id=?", (fact_id,)).fetchone()
        return row["story_id"] if row else None

    def decide_fact(self, fact_id: str, new_status: str) -> int:
        cur = self.conn.execute(
            "UPDATE facts SET status=? WHERE id=? AND status='pending_review'",
            (new_status, fact_id))
        self.conn.commit()
        return cur.rowcount

    def pending_proposals(self, story_ids: list[str]) -> list[dict]:
        marks = ",".join("?" * len(story_ids))
        rows = self.conn.execute(
            "SELECT p.*, s.title AS story_title,"
            "       cf.name AS candidate_label, tf.name AS target_label"
            " FROM entity_merge_proposals p"
            " JOIN stories s ON s.id = p.story_id"
            " LEFT JOIN entities cf ON cf.id = p.candidate_entity_id"
            " LEFT JOIN entities tf ON tf.id = p.target_entity_id"
            f" WHERE p.status='pending' AND p.story_id IN ({marks})"
            " ORDER BY p.created_at", story_ids).fetchall()
        return [dict(r) for r in rows]

    def proposal_story_id(self, proposal_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT story_id FROM entity_merge_proposals WHERE id=?",
            (proposal_id,)).fetchone()
        return row["story_id"] if row else None


class UserStore:
    """用户域存取(ADR-0022/0029):login/refresh/admin 的 users 表读写。"""

    def __init__(self, conn) -> None:
        self.conn = conn

    def find_by_username(self, username: str):
        return self.conn.execute(
            "SELECT * FROM users WHERE username=?", (username,)).fetchone()

    def find_by_id(self, user_id: str):
        return self.conn.execute(
            "SELECT * FROM users WHERE id=?", (user_id,)).fetchone()

    def password_hash(self, user_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT password_hash FROM users WHERE id=?", (user_id,)).fetchone()
        return row["password_hash"] if row else None

    def set_password(self, user_id: str, password_hash: str) -> None:
        self.conn.execute(
            "UPDATE users SET password_hash=? WHERE id=?",
            (password_hash, user_id))
        self.conn.commit()

    def create(self, username: str, password_hash: str, role: str) -> None:
        self.conn.execute(
            "INSERT INTO users (id, username, password_hash, role, status, created_at)"
            " VALUES (?, ?, ?, ?, 'active', ?)",
            (uuid.uuid4().hex, username, password_hash, role,
             datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")))
        self.conn.commit()

    def list_all(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, username, role, status, created_at FROM users ORDER BY created_at"
        ).fetchall()
        return [dict(r) for r in rows]

    def exists(self, user_id: str) -> bool:
        return self.conn.execute(
            "SELECT id FROM users WHERE id=?", (user_id,)).fetchone() is not None

    def set_status(self, user_id: str, status: str) -> int:
        return self.conn.execute(
            "UPDATE users SET status=? WHERE id=?", (status, user_id)).rowcount

    def update_password(self, user_id: str, password_hash: str) -> int:
        return self.conn.execute(
            "UPDATE users SET password_hash=? WHERE id=?",
            (password_hash, user_id)).rowcount

    def commit(self) -> None:
        self.conn.commit()


class ObservabilityQueries:
    """观测域读模型:用量聚合 / 回溯 / 评审记录 / 运营统计(ADR-0028)。"""

    def __init__(self, conn) -> None:
        self.conn = conn

    def usage_by_agent(self, story_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT agent, model, COUNT(*) calls, SUM(tokens_in) tin, SUM(tokens_out) tout,"
            " SUM(cached_tokens) cached, SUM(latency_ms) latency,"
            " CAST(ROUND(100.0 * SUM(cached_tokens) / NULLIF(SUM(tokens_in), 0)) AS INTEGER)"
            "   AS cache_hit_pct"
            " FROM usage_log WHERE story_id=? GROUP BY agent, model",
            (story_id,)).fetchall()
        return [dict(r) for r in rows]

    def recent_traces(self, story_id: str, limit: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT agent, model, stage, input_text, output_text,"
            " tokens_in, tokens_out, latency_ms, created_at"
            " FROM agent_traces WHERE story_id=? ORDER BY created_at DESC LIMIT ?",
            (story_id, limit)).fetchall()
        return [dict(r) for r in rows]

    def reviews(self, story_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM review_results WHERE story_id=? ORDER BY created_at",
            (story_id,)).fetchall()
        return [dict(r) for r in rows]

    def ops_stats(self, since: str) -> dict:
        """近 24h 运营口径:调用数/失败数/等待中断/错误 run。"""
        return {
            "llm_calls": self.conn.execute(
                "SELECT COUNT(*) n FROM usage_log WHERE created_at>=?",
                (since,)).fetchone()["n"],
            "llm_failures": self.conn.execute(
                "SELECT COUNT(*) n FROM llm_failures WHERE created_at>=?",
                (since,)).fetchone()["n"],
            "waiting_interruptions": self.conn.execute(
                "SELECT COUNT(*) n FROM story_run_state WHERE status='waiting'"
            ).fetchone()["n"],
            "error_runs": self.conn.execute(
                "SELECT COUNT(*) n FROM story_run_state WHERE error_code IS NOT NULL"
            ).fetchone()["n"],
        }

    def token_cost(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT substr(u.created_at,1,10) AS day, u.story_id, s.title, s.owner_id,"
            " COUNT(*) AS calls, SUM(COALESCE(u.tokens_in,0)) AS tokens_in,"
            " SUM(COALESCE(u.tokens_out,0)) AS tokens_out"
            " FROM usage_log u JOIN stories s ON s.id = u.story_id"
            " GROUP BY day, u.story_id ORDER BY day DESC, tokens_in + tokens_out DESC"
            " LIMIT 200").fetchall()
        return [dict(r) for r in rows]

    def spent_today(self, day: str, story_id: str | None = None) -> int:
        """当日 token 消耗(预算闸门口径):story 级或全站级。"""
        if story_id is None:
            row = self.conn.execute(
                "SELECT COALESCE(SUM(COALESCE(tokens_in,0)+COALESCE(tokens_out,0)),0) s"
                " FROM usage_log WHERE substr(created_at,1,10)=?", (day,)).fetchone()
        else:
            row = self.conn.execute(
                "SELECT COALESCE(SUM(COALESCE(tokens_in,0)+COALESCE(tokens_out,0)),0) s"
                " FROM usage_log WHERE story_id=? AND substr(created_at,1,10)=?",
                (story_id, day)).fetchone()
        return row["s"] or 0
