"""生成层 POV 泄漏评测:用已定稿正文实测"不知情角色表现出知晓保密事实"。

与 run_memory_eval(查询层)互补:那边测"检索返回集合是否含泄密条目",
这边测"LLM 拿到过滤后上下文写出的正文,是否仍让不知情角色表现出知情"
(叙述者向读者透露不算泄漏;角色恰好行为吻合但无知情证据不算)。

判分依据:fact_visibility 为 ground truth——事实 F 建立于第 N 章之前,
角色 R 在 F 的可见性表中无条目,即 R 属于不知情方。
判定方式:LLM-judge(REVIEWER 强模型,单评无人工复核,证据引文落盘供抽检)。

运行:
  python -m evals.run_leak_eval --dry-run   # 只打印每章测试对规模,零 token
  python -m evals.run_leak_eval             # 实测(需 GLM_API_KEY)
  python -m evals.run_leak_eval --per-chapter 6 --per-fact 2   # 收紧采样

报告落盘 evals/reports/leak_eval_report.md。
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path

from app.core.llm.base import ChatMessage
from app.core.llm.facade import LLMFacade

DB_PATH = Path("data/novel_agent.db")
REPORT_PATH = Path(__file__).parent / "reports" / "leak_eval_report.md"

PER_CHAPTER_FACTS = 10   # 每章最多核查事实数(按不知情人数降序)
PER_FACT_CHARS = 3       # 每条事实最多核查的不知情角色数

JUDGE_SYSTEM = (
    "你是小说一致性审计员。给定一章正文与一组「事实 × 不知情角色」核查项,"
    "判定不知情角色是否在本章表现出已经知道该事实。"
    "严格区分三种情况:(a)角色未出场;(b)出场但无知情表现——包括叙述者/其他角色"
    "知道、以及角色行为恰好与事实吻合但正文未给出其知情的证据,均不算泄漏;"
    "(c)出场且表现出知情(说出、提及、据此行动、情绪/内心反应),才算泄漏。"
    "只输出 JSON 数组,不要输出任何其他文字。"
)


def pick_story(db: sqlite3.Connection, keyword: str) -> sqlite3.Row:
    row = db.execute(
        "SELECT id, title FROM stories WHERE title LIKE ?", (f"%{keyword}%",)
    ).fetchone()
    if row is None:
        raise SystemExit(f"未找到标题含「{keyword}」的故事")
    return row


def build_pairs(db: sqlite3.Connection, story_id: str,
                per_chapter: int, per_fact: int) -> dict[int, list[dict]]:
    """每章构造核查对:前情事实 × 不知情角色,预过滤"事实被本章触及"。

    预过滤规则(确定性):事实 content 中提及的任一角色名出现在本章正文。
    未触及的事实写进正文的可能性极低,判 no-leak 只会稀释分母、虚降泄漏率。
    """
    chars = {r["name"]: r["id"] for r in db.execute(
        "SELECT id, name FROM characters WHERE story_id=?", (story_id,))}
    id2name = {v: k for k, v in chars.items()}
    chapters = {r["chapter_no"]: r["content"] for r in db.execute(
        "SELECT chapter_no, content FROM chapters WHERE story_id=? AND status='active'",
        (story_id,))}
    out: dict[int, list[dict]] = {}
    for ch_no, text in sorted(chapters.items()):
        facts = db.execute(
            """SELECT f.id, f.content, f.chapter_established
               FROM facts f
               WHERE f.story_id=? AND f.chapter_established<? AND f.status='confirmed'
               ORDER BY f.chapter_established""",
            (story_id, ch_no)).fetchall()
        pairs = []
        for f in facts:
            knowers = {r[0] for r in db.execute(
                "SELECT character_id FROM fact_visibility WHERE fact_id=?", (f["id"],))}
            # 事实提及的角色至少一人出现在本章正文 -> 该事实被本章触及
            mentioned = [n for n in chars if n in f["content"]]
            if not mentioned or not any(n in text for n in mentioned):
                continue
            unaware = [id2name[cid] for cid in chars.values()
                       if cid not in knowers and id2name[cid] not in f["content"]]
            for name in sorted(unaware, key=lambda n: n not in text)[:per_fact]:
                pairs.append({"fact_id": f["id"][:8],
                              "fact": f["content"],
                              "character": name})
        # 截断采样:触及本章的事实按出现顺序取前 N(建立早的事实参与最多章的核查)
        pairs = pairs[: per_chapter * per_fact]
        if pairs:
            out[ch_no] = pairs
    return out


def parse_json_array(text: str) -> list:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        raise ValueError(f"judge 输出无 JSON 数组: {text[:120]}")
    return json.loads(m.group(0))


def judge_chapter(llm: LLMFacade, ch_no: int, content: str,
                  pairs: list[dict]) -> list[dict]:
    listing = json.dumps(
        [{"fact_id": p["fact_id"], "fact": p["fact"], "character": p["character"]}
         for p in pairs], ensure_ascii=False, indent=1)
    user = (
        f"## 第 {ch_no} 章正文\n\n{content}\n\n"
        f"## 核查项(事实均在本章之前成立;character 为不知情角色)\n\n{listing}\n\n"
        "对每一项输出:{\"fact_id\": 原样, \"character\": 原样, "
        "\"appears\": 角色是否出场, \"leak\": 是否表现出知晓, "
        "\"evidence\": leak=true 时摘录正文原句,否则留空}。"
    )
    resp = llm.chat("REVIEWER", [
        ChatMessage(role="system", content=JUDGE_SYSTEM),
        ChatMessage(role="user", content=user),
    ], stage=f"leak_eval_ch{ch_no}", response_format={"type": "json_object"})
    return parse_json_array(resp.content)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--story", default="从培训班开始")
    ap.add_argument("--per-chapter", type=int, default=PER_CHAPTER_FACTS)
    ap.add_argument("--per-fact", type=int, default=PER_FACT_CHARS)
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印每章测试对规模,不调 LLM")
    args = ap.parse_args()

    db = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    story = pick_story(db, args.story)
    pairs_by_ch = build_pairs(db, story["id"], args.per_chapter, args.per_fact)
    total = sum(len(v) for v in pairs_by_ch.values())
    print(f"故事「{story['title']}」: {len(pairs_by_ch)} 章, 共 {total} 个核查对")
    for ch, pairs in sorted(pairs_by_ch.items()):
        print(f"  ch{ch}: {len(pairs)} 对")
    if args.dry_run or not pairs_by_ch:
        return

    llm = LLMFacade()
    verdicts: list[dict] = []
    for ch, pairs in sorted(pairs_by_ch.items()):
        content = db.execute(
            "SELECT content FROM chapters WHERE story_id=? AND chapter_no=?",
            (story["id"], ch)).fetchone()["content"]
        fact_text = {p["fact_id"]: p["fact"] for p in pairs}
        try:
            rows = judge_chapter(llm, ch, content, pairs)
        except Exception as e:                      # 解析失败:该章标错,不静默丢
            verdicts.append({"chapter": ch, "error": str(e)[:200], "pairs": len(pairs)})
            print(f"  ch{ch}: judge 失败 {e}")
            continue
        for r in rows:
            r["chapter"] = ch
            r.setdefault("fact_text", fact_text.get(r.get("fact_id", ""), "?"))
        verdicts.append({"chapter": ch, "rows": rows})
        n_app = sum(1 for r in rows if r.get("appears"))
        n_leak = sum(1 for r in rows if r.get("leak"))
        print(f"  ch{ch}: 出场 {n_app}/{len(rows)}, 泄漏 {n_leak}")

    rows_all = [r for v in verdicts for r in v.get("rows", [])]
    appears = [r for r in rows_all if r.get("appears")]
    leaks = [r for r in rows_all if r.get("leak")]
    rate = len(leaks) / max(1, len(appears))

    lines = [
        "# 生成层 POV 泄漏评测:已定稿正文 × fact_visibility ground truth",
        "",
        f"> 故事「{story['title']}」;判定 LLM-judge(REVIEWER 档,单评无人工复核);",
        f"> 核查对 = (前情事实, 不知情角色), 预过滤「事实提及的角色至少一人出现在本章」;",
        f"> 计分: 泄漏率 = 出场且表现出知晓 / 出场。",
        "",
        "## 总体",
        "",
        f"- 核查对: {len(rows_all)}(出场 {len(appears)}, 未出场不计分)",
        f"- **泄漏: {len(leaks)} 条, 泄漏率 {rate:.1%}**",
        "",
        "## 每章明细",
        "",
        "| 章 | 核查对 | 出场 | 泄漏 |", "|---|---|---|---|",
    ]
    for v in verdicts:
        if "error" in v:
            lines.append(f"| ch{v['chapter']} | {v['pairs']} | - | 解析失败 |")
            continue
        rows = v["rows"]
        lines.append(f"| ch{v['chapter']} | {len(rows)} | "
                     f"{sum(1 for r in rows if r.get('appears'))} | "
                     f"{sum(1 for r in rows if r.get('leak'))} |")
    if leaks:
        lines += ["", "## 泄漏明细(供人工复核)", ""]
        for r in sorted(leaks, key=lambda r: r["chapter"]):
            lines.append(f"- ch{r['chapter']} · {r.get('character')} 不应知晓"
                         f"「{r.get('fact_text', '')}」;证据: {r.get('evidence', '')[:80]}")
    lines += [
        "",
        "## 口径与局限",
        "- 判定为 LLM-judge 单评,未人工复核;泄漏明细附证据引文,建议抽检后引用;",
        "- 叙述者向读者透露不计泄漏(伏笔/悬念的合法手段),仅角色层面知情计分;",
        "- beliefs 误信维度(角色按真相行动而违背其误信)不在本评测范围;",
        f"- 采样: 每章 ≤{args.per_chapter} 事实 × 每事实 ≤{args.per_fact} 不知情角色。",
    ]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[report saved] {REPORT_PATH}")


if __name__ == "__main__":
    main()
