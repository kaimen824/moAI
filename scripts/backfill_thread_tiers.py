"""存量伏笔 tier 回填(ADR-0020 一次性数据操作)。

对 plot_threads 中 open 且 tier IS NULL 的行,按伏笔评审同款契约批量分类
short/long + basis。默认 dry-run 只打印;--apply 落库。
用法: python scripts/backfill_thread_tiers.py [story_id] [--apply]
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import AgentRole  # noqa: E402
from app.core.llm.base import ChatMessage  # noqa: E402
from app.core.llm.facade import LLMFacade  # noqa: E402
from app.db.database import init_db  # noqa: E402
from app.graph.agents.base import parse_json_loose  # noqa: E402
from app.graph.runtime import _now  # noqa: E402

_SYSTEM = (
    "你是伏笔账本评审员。对下列既有伏笔逐条分类,严格按 JSON 输出:\n"
    '{"classifications":[{"no":1,"tier":"short|long","basis":"一句依据"}]}\n'
    "short=近程悬念(数章内可回收/已接近回收条件);"
    "long=绑定主线终局/跨卷布局(basis 写明绑定哪条主线)。\n"
    "判据:世界观级/身世级/终局悬念 -> long;道具/位置/单事件悬念 -> short。"
    "模棱两可标 short(长线是承诺,不是免催收)。no 对应输入编号。"
)


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    apply = "--apply" in sys.argv
    story_id = args[0] if args else None

    conn = init_db(Path(__file__).resolve().parents[1] / "data" / "novel_agent.db")
    sql = ("SELECT p.id, p.story_id, p.planted_chapter, p.description FROM plot_threads p"
           " WHERE p.status='open' AND p.tier IS NULL")
    params: list = []
    if story_id:
        sql += " AND p.story_id=?"
        params.append(story_id)
    rows = conn.execute(sql, params).fetchall()
    if not rows:
        print("无可回填行(tier 均已填充)")
        return

    # 主线大纲(判 long 绑定):每故事取 confirmed 最新版截断
    outlines = {r["story_id"]: (r["content"] or "")[:1200] for r in conn.execute(
        "SELECT story_id, content FROM outlines WHERE status='confirmed'"
        " ORDER BY version_no DESC")}

    lines = []
    for i, r in enumerate(rows, 1):
        lines.append(f"#{i} (埋于ch{r['planted_chapter'] or '?'}) {r['description']}")
    outline_txt = "\n\n".join(f"[story {sid[:8]} 主线大纲]\n{txt}"
                              for sid, txt in outlines.items() if sid in {r["story_id"] for r in rows})

    facade = LLMFacade()
    resp = facade.chat(
        AgentRole.THREAD,
        [ChatMessage("system", _SYSTEM),
         ChatMessage("user", f"{outline_txt}\n\n[待分类伏笔]\n" + "\n".join(lines))],
        stage="backfill_thread_tiers", story_id=story_id or "",
        response_format={"type": "json_object"},
    )
    verdict = parse_json_loose(resp.content)
    by_no = {c.get("no"): c for c in verdict.get("classifications", [])}

    print(f"共 {len(rows)} 条;分类结果:")
    updates = []
    for i, r in enumerate(rows, 1):
        c = by_no.get(i) or {}
        tier = c.get("tier") if c.get("tier") in ("short", "long") else "short"
        basis = (c.get("basis") or "")[:120]
        print(f"#{i:<3} {tier:<5} ch{r['planted_chapter'] or '?':<3} {r['description'][:44]}"
              f"\n      依据: {basis}")
        updates.append((tier, basis, r["id"]))

    if not apply:
        print("\n(dry-run,未落库;加 --apply 生效)")
        return
    conn.executemany(
        "UPDATE plot_threads SET tier=?, basis=?, updated_at=? WHERE id=?",
        [(t, b, _now(), tid) for t, b, tid in updates])
    conn.commit()
    print(f"\n已落库 {len(updates)} 条")


if __name__ == "__main__":
    main()
