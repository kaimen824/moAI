"""伏笔评审 Agent(ADR-0020):独立评审,专职伏笔账本。

从质量审校剥离(审校不再产 thread_changes,只评伏笔处理质量):
- 埋设准入:plant 只收"未解悬念钩子"(设定/阶段目标不入册),必填 tier+basis;
  活跃容量超限时契约要求先 drop 腾位(代码侧 commit 时硬校验拒绝)。
- 到期复核(双时点第二点):超龄伏笔裁决 collect/escalate(仅一次)/keep。
- 升格免死金牌防线:长线需绑定主线依据,且 long 也有账龄线。
产出:thread_changes(经中断点 B 人工确认,ADR-0006 语义不变)+
reviews(账本管理裁决;escalate 由定稿管道直接生效,不进剧情确认链)。
"""

from __future__ import annotations

from app.core.config import (
    AgentRole,
    THREAD_LONG_AGE,
    THREAD_LONG_CAP,
    THREAD_SHORT_AGE,
    THREAD_SHORT_CAP,
)
from app.graph.agents.base import BaseAgent, NodeDeps, register_agent

_SYSTEM = (
    "你是伏笔账本评审员(独立评审,不参与创作)。严格按 JSON 输出:\n"
    '{"thread_changes":[{"thread_id":"操作对象id(见下方规则4,plant 留空)",'
    '"description":"伏笔描述","action":"plant|advance|resolve|drop",'
    '"tier":"short|long","basis":"一句依据"}],\n'
    '"reviews":[{"thread_id":"超龄清单方括号内的id","description":"超龄伏笔描述",'
    '"verdict":"collect|escalate|keep","reason":"一句话"}]}\n'
    "无变更时对应数组留空。规则:\n"
    "1. plant 只收'未解悬念钩子'——正文留下的、读者会期待回答的问题。"
    "世界观设定、本章行动目标、已明确交代的背景不入伏笔账本(它们另有归宿);\n"
    "2. plant 必填 tier 与 basis:short=近程悬念(数章内可回收);long=绑定"
    "主线终局/跨卷布局(basis 写明绑定哪条主线)。模棱两可标 short——"
    "长线是承诺,不是免催收标签;\n"
    "3. 活跃容量超限时(见[容量现状]),优先 drop 低价值线或推进回收腾位,"
    "不新增 plant;\n"
    "4. advance/resolve/drop 必填 thread_id(从活跃清单每条开头的方括号内"
    "原样取),description 照抄原描述作留痕;落库优先按 thread_id 精确命中,"
    "id 缺省才按描述模糊匹配;\n"
    "5. reviews 只针对[超龄待复核]清单,thread_id 必填:collect=确认应尽快"
    "回收;escalate=确认应升格 long(每条仅允许一次);keep=维持现状并给理由;\n"
    "6. 宁缺毋滥:证据不足不强行动账本。"
)


@register_agent
class ThreadReviewNode(BaseAgent):
    name = "thread_reviewer"
    role = AgentRole.THREAD

    def __call__(self, state: dict, deps: NodeDeps) -> dict:
        bundle = state.get("context_bundle", {})
        threads = bundle.get("active_threads", [])
        chapter_no = state.get("chapter_no", 0)

        overdue: list[str] = []
        normal: list[str] = []
        n_short = n_long = 0
        for t in threads:
            tier = t.get("tier") or "short"
            if tier == "long":
                n_long += 1
            else:
                n_short += 1
            planted = t.get("planted_chapter") or 0
            age = chapter_no - planted if planted else 0
            limit = THREAD_LONG_AGE if tier == "long" else THREAD_SHORT_AGE
            # 每条开头带 id(ADR-0025:评审回传 thread_id,落库精确命中)
            line = (f"- [{t['id']}] {t['description']}(埋于ch{planted or '?'},"
                    f"tier={tier},已{age}章"
                    + (",已升格" if t.get("escalated_chapter") else "") + ")")
            # 复核清单:超龄且未升格(升格过=已用过一次机会,按 long 账龄自然滑出)
            if planted and age > limit and not t.get("escalated_chapter"):
                overdue.append(line)
            else:
                normal.append(line)

        over = (n_short > THREAD_SHORT_CAP or n_long > THREAD_LONG_CAP)
        user = (
            f"[本章正文草稿(第{chapter_no}章)]\n{state.get('draft', '')}\n\n"
            f"[主线大纲(判长线绑定用,截断)]\n{(state.get('master_outline') or '')[:1200]}\n\n"
            f"[容量现状] 活跃 short {n_short}/{THREAD_SHORT_CAP},"
            f"long {n_long}/{THREAD_LONG_CAP}"
            + ("(已超限:本章 plant 需先 drop 腾位)" if over else "") + "\n\n"
            f"[超龄待复核(short>{THREAD_SHORT_AGE}章/long>{THREAD_LONG_AGE}章,"
            f"escalate 每条仅一次)]\n" + ("\n".join(overdue) or "(无)") + "\n\n"
            f"[其余活跃伏笔(含悬置期,悬置只加深不回收)]\n" + ("\n".join(normal) or "(无)")
        )
        verdict = self.ask_json(
            _SYSTEM, user, stage="review_threads", story_id=state.get("story_id", ""))
        chg = verdict.get("thread_changes", [])
        rev = verdict.get("reviews", [])
        counts = ",".join(
            f"{a}:{sum(1 for c in chg if c.get('action') == a)}"
            for a in ("plant", "advance", "resolve", "drop"))
        review_fb = ";".join(
            f"{r.get('verdict')}:{(r.get('description') or '')[:20]}" for r in rev)
        deps.log_review(state, reviewer="thread", verdict={
            "verdict": "pass",   # 伏笔评审无 pass/revise 裁决,记 pass 占位
            "feedback": f"[{counts}] 复核:{review_fb or '(无)'}",
        }, round_no=state.get("rewrite_count", 0) + 1)
        return {"thread_review": verdict}
