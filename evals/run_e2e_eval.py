"""P6 端到端量化评测:一致性 / 伏笔 / 重写分布 / 成本;分级路由对比。

两种运行模式:
  replay(默认,零 token):response_override 回放脚本化剧情,验证评测管线本身;
      python -m evals.run_e2e_eval --mode replay
  real(需 GLM_API_KEY):真实模型生成 N 章,产出简历量化报告;
      python -m evals.run_e2e_eval --mode real --chapters 3

报告落盘 evals/reports/e2e_eval_report.md。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import tempfile
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from app.core.llm.base import LLMResponse
from app.core.llm.facade import LLMFacade
from app.graph.build import build_graph
from app.graph.runtime import build_engine

REPORT_PATH = Path(__file__).parent / "reports" / "e2e_eval_report.md"


# ---------- 回放脚本(复用 e2e 的剧情;revise 一次以产生重写样本)----------

def replay_override(stage: str) -> LLMResponse | None:
    import tests.test_graph_e2e as t
    if stage in t.SCRIPTS:
        val = t.SCRIPTS[stage]
        return LLMResponse(
            content=val if isinstance(val, str) else json.dumps(val, ensure_ascii=False),
            model="replay",
        )
    return None


def run_scenario(db_path: Path, chapters: int, *, replay: bool,
                 model_overrides: dict | None = None) -> sqlite3.Connection:
    facade = (LLMFacade(response_override=replay_override) if replay else LLMFacade())
    deps, conn = build_engine(db_path, llm=facade)
    if model_overrides:
        from app.core.config import AgentRole
        for role, model in model_overrides.items():
            deps.llm._settings.set_model_override(AgentRole(role), model)
    graph = build_graph(deps, checkpointer=SqliteSaver(conn))

    story_id, branch = deps.repo.create_story(f"评测-{db_path.stem}", "端到端评测")
    cfg = {"configurable": {"thread_id": db_path.stem}}
    graph.invoke({"story_id": story_id, "branch_id": branch,
                  "target_chapters": chapters, "initial_input": "东方奇幻"}, cfg)
    # 中断链:确认大纲、确认阶段细纲,每章审阅时 revise 一次再 confirm(制造重写样本)
    pending = chapters
    while pending > 0:
        result = graph.invoke(Command(resume={"action": "confirm"}), cfg)
        if "__interrupt__" not in result:
            break
        itype = result["__interrupt__"][0].value["type"]
        if itype == "user_review_chapter":
            result = graph.invoke(Command(resume={
                "action": "revise", "feedback": "节奏加快", "threads": []}), cfg)
            result = graph.invoke(Command(resume={"action": "confirm", "threads": []}), cfg)
            pending -= 1
            if "__interrupt__" not in result or result["__interrupt__"][0].value["type"] != "user_review_chapter":
                pending -= 1 if result.get("chapters_done", 0) >= chapters else 0
        # confirm_stage_outline 等继续循环
    return conn


# ---------- 指标 ----------

def collect_metrics(conn: sqlite3.Connection) -> dict:
    m: dict = {}
    # 重写分布:每章审校轮次(user_review 前的 review_results 轮)
    rounds = [r["round_no"] for r in conn.execute(
        "SELECT round_no FROM review_results WHERE reviewer='reviewer'")]
    m["n_review_rounds"] = len(rounds)
    m["avg_rewrite_rounds"] = round(statistics.mean(rounds), 2) if rounds else 0
    # 维度评分分布
    scores = []
    for r in conn.execute("SELECT scores FROM review_results WHERE scores IS NOT NULL"):
        try:
            d = json.loads(r["scores"])
            scores.extend(v for v in d.values() if isinstance(v, (int, float)))
        except json.JSONDecodeError:
            continue
    m["n_scored"] = len(scores)
    m["avg_score"] = round(statistics.mean(scores), 2) if scores else None
    m["min_score"] = min(scores) if scores else None
    # 一致性:审校 consistency 维度
    cons = []
    for r in conn.execute(
            "SELECT scores FROM review_results WHERE reviewer='reviewer' AND scores IS NOT NULL"):
        try:
            v = json.loads(r["scores"]).get("consistency")
            if v is not None:
                cons.append(v)
        except json.JSONDecodeError:
            continue
    m["avg_consistency"] = round(statistics.mean(cons), 2) if cons else None
    # 伏笔
    threads = conn.execute("SELECT status, COUNT(*) n FROM plot_threads GROUP BY status").fetchall()
    m["threads"] = {r["status"]: r["n"] for r in threads}
    planted = sum(m["threads"].values())
    resolved = m["threads"].get("resolved", 0)
    m["thread_resolve_rate"] = round(resolved / planted, 3) if planted else None
    # 成本
    usage = conn.execute(
        "SELECT agent, COUNT(*) calls, SUM(tokens_in) tin, SUM(tokens_out) tout,"
        " SUM(latency_ms) lat FROM usage_log GROUP BY agent").fetchall()
    m["usage"] = [dict(r) for r in usage]
    m["total_calls"] = sum(u["calls"] for u in m["usage"])
    m["total_latency_s"] = round(sum(u["lat"] or 0 for u in m["usage"]) / 1000, 1)
    # 章节/事实
    m["chapters_active"] = conn.execute(
        "SELECT COUNT(*) c FROM chapters WHERE status='active'").fetchone()["c"]
    m["facts_confirmed"] = conn.execute(
        "SELECT COUNT(*) c FROM facts WHERE status='confirmed'").fetchone()["c"]
    m["facts_pending"] = conn.execute(
        "SELECT COUNT(*) c FROM facts WHERE status='pending_review'").fetchone()["c"]
    m["beliefs"] = conn.execute("SELECT COUNT(*) c FROM beliefs").fetchone()["c"]
    return m


def render(mode: str, tiered: dict, full_strong: dict | None) -> str:
    lines = [
        f"# P6 端到端评测报告({mode} 模式)",
        "",
        "> replay 模式验证评测管线与流程指标口径(响应为脚本回放,质量分数无语义意义);",
        "> real 模式(需 GLM_API_KEY)产出真实质量与成本数据。",
        "",
        "## 分级路由(默认)",
        "",
        "| 指标 | 值 |",
        "|---|---|",
    ]
    for k in ("chapters_active", "n_review_rounds", "avg_rewrite_rounds", "n_scored",
              "avg_score", "avg_consistency", "thread_resolve_rate",
              "facts_confirmed", "facts_pending", "beliefs",
              "total_calls", "total_latency_s"):
        v = tiered.get(k)
        lines.append(f"| {k} | {v} |")
    lines += ["", "### 分 Agent 用量", "",
              "| Agent | 调用 | tokens_in | tokens_out | 累计耗时(s) |", "|---|---|---|---|---|"]
    for u in tiered["usage"]:
        lines.append(f"| {u['agent']} | {u['calls']} | {u['tin'] or 0} | {u['tout'] or 0} | {round((u['lat'] or 0)/1000,1)} |")

    if full_strong:
        lines += ["", "## 对比:全强模型 vs 分级路由", "",
                  "| 指标 | 全强 | 分级 |", "|---|---|---|"]
        for k in ("total_calls", "total_latency_s"):
            lines.append(f"| {k} | {full_strong.get(k)} | {tiered.get(k)} |")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["replay", "real"], default="replay")
    ap.add_argument("--chapters", type=int, default=2)
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        conn = run_scenario(Path(tmp) / "tiered.db", args.chapters, replay=args.mode == "replay")
        tiered = collect_metrics(conn)
        conn.close()

        full_strong = None
        if args.mode == "replay":   # real 模式下对比实验单独跑(双倍成本),此处仅 replay 演示口径
            conn2 = run_scenario(
                Path(tmp) / "strong.db", args.chapters, replay=True,
                model_overrides={r: "glm-4.6" for r in
                                 ("EVENT", "CHARACTER", "SUMMARY")})
            full_strong = collect_metrics(conn2)
            conn2.close()

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(render(args.mode, tiered, full_strong), encoding="utf-8")
    print(render(args.mode, tiered, full_strong))
    print(f"[report saved] {REPORT_PATH}")


if __name__ == "__main__":
    main()
