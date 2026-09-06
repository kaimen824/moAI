"""世界状态回放(ADR-0004):沿版本链取 (branch, chapter) 时点的有效事实。

有效 = 已确立(chapter_established <= upto)且未被更早或同时点的后续版本推翻。
主线回放含各章活跃版本语义(章节版本化由 chapters.status 承载,事实层面即版本链)。
"""

from __future__ import annotations

import sqlite3

from app.memory.repository import AgentContext
from app.memory.schemas import WorldSnapshot

# 事实在该时点仍有效:无"推翻它的更早版本"存在
_FACT_VALID = """
SELECT f.* FROM facts f
WHERE f.story_id = :story_id AND f.branch_id = :branch_id
  AND f.chapter_established <= :upto
  AND NOT EXISTS (
    SELECT 1 FROM facts g
    WHERE g.prev_version_id = f.id AND g.branch_id = f.branch_id
      AND g.chapter_established <= :upto
  )
"""

_BELIEF_ACTIVE = """
SELECT b.* FROM beliefs b
WHERE b.story_id = :story_id AND b.branch_id = :branch_id
  AND b.character_id = :character_id
  AND b.established_chapter <= :upto
  AND (b.dispelled_chapter IS NULL OR b.dispelled_chapter > :upto)
  AND NOT EXISTS (
    SELECT 1 FROM beliefs b2
    WHERE b2.prev_version_id = b.id AND b2.branch_id = b.branch_id
      AND b2.established_chapter <= :upto
  )
"""

# 注:belief 的时点有效性由 dispelled_chapter 决定(历史时点回放);
# status 列仅作"当前状态"冗余标记,不参与时点查询。


def replay_world(
    conn: sqlite3.Connection, story_id: str, branch_id: str, upto_chapter: int
) -> WorldSnapshot:
    """上帝视角世界快照(审校一致性校验的基准)。"""
    params = {"story_id": story_id, "branch_id": branch_id, "upto": upto_chapter}
    snap = WorldSnapshot()
    snap.facts = [dict(r) for r in conn.execute(_FACT_VALID, params).fetchall()]
    snap.beliefs = _all_active_beliefs(conn, params)
    snap.plot_threads = [dict(r) for r in conn.execute(
        "SELECT * FROM plot_threads WHERE story_id=:story_id AND branch_id=:branch_id",
        params,
    ).fetchall()]
    snap.characters = [dict(r) for r in conn.execute(
        "SELECT * FROM characters WHERE story_id=:story_id", params
    ).fetchall()]
    return snap


def _all_active_beliefs(conn: sqlite3.Connection, params: dict) -> list[dict]:
    sql = """
    SELECT b.* FROM beliefs b
    WHERE b.story_id = :story_id AND b.branch_id = :branch_id
      AND b.established_chapter <= :upto
      AND (b.dispelled_chapter IS NULL OR b.dispelled_chapter > :upto)
      AND NOT EXISTS (
        SELECT 1 FROM beliefs b2
        WHERE b2.prev_version_id = b.id AND b2.branch_id = b.branch_id
          AND b2.established_chapter <= :upto
      )
    """
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_pov_memory(
    conn: sqlite3.Connection,
    story_id: str,
    branch_id: str,
    character_id: str,
    upto_chapter: int,
) -> dict:
    """角色 POV 长期记忆(ADR-0003):客观(经可见性过滤)∪ 主观(believed 误信)。"""
    params = {
        "story_id": story_id, "branch_id": branch_id,
        "character_id": character_id, "upto": upto_chapter,
    }
    objective = conn.execute("""
        SELECT f.*, v.knowledge_level, v.detail FROM facts f
        JOIN fact_visibility v
          ON v.fact_id = f.id AND v.character_id = :character_id
        WHERE f.story_id = :story_id AND f.branch_id = :branch_id
          AND f.chapter_established <= :upto
          AND (v.learned_chapter IS NULL OR v.learned_chapter <= :upto)
          AND NOT EXISTS (
            SELECT 1 FROM facts g
            WHERE g.prev_version_id = f.id AND g.branch_id = f.branch_id
              AND g.chapter_established <= :upto
          )
    """, params).fetchall()
    subjective = conn.execute(_BELIEF_ACTIVE, params).fetchall()
    return {
        "facts": [dict(r) for r in objective],
        "beliefs": [dict(r) for r in subjective],
    }
