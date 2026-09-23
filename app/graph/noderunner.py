"""ReAct 工具的图通道(ADR-0031 P1):状态通道的唯一合法入口。

revamp_chapter(重构历史章,所有者拍板):校验 → 组装输入 →
update_state(as_node="chapter_slice") 写回 → 交还 sse worker 续跑。
节点执行仍在图内(写作/三评审/人审/定稿零改动);一键模式没有
"重构旧章"入口(state 的 revamp 字段只在工具写回时出现),工具表
只挂 chat 端点——拓扑 + 白名单双重门禁。

graph_provider / launcher 由 api 层装配注入(本模块不可 import api);
冲突检查(拍板 b)是人审卡前的专属步骤,由 sse worker 调用,同样
不进图拓扑、不触碰一键模式路由。
"""

from __future__ import annotations

import json

from app.core.config import AgentRole
from app.infrastructure.queries import StoryQueries


class NodeRunner:
    """组装 revamp 输入并触发续跑;后续在此扩展生成型/裁决型节点工具。"""

    def __init__(self, deps, graph_provider, launcher) -> None:
        self._deps = deps
        self._graph_provider = graph_provider   # () -> compiled graph
        self._launcher = launcher               # (story_id, user_id) -> 触发后台续跑

    def revamp_chapter(self, story_id: str, user_id: str,
                       chapter_no, feedback: str) -> str:
        """重构已定稿章节:返回给 agent 的观察文本(异常在工具层已被转换)。"""
        graph = self._graph_provider()
        cfg = {"configurable": {"thread_id": story_id}}
        st = graph.get_state(cfg).values or {}
        done = st.get("chapters_done", 0)
        if not done:
            return "本书还没有定稿章节,无法重构"
        try:
            no = int(chapter_no)
        except (TypeError, ValueError):
            return f"章节号不合法:{chapter_no!r}"
        if not 1 <= no <= done:
            return f"第 {no} 章不是已定稿章节(当前已定稿 {done} 章)"
        if not (feedback or "").strip():
            return "缺少修订意见:请说明这一章要怎么改"
        old = StoryQueries(self._deps.conn).active_chapter(story_id, no)
        if old is None:
            return f"第 {no} 章不存在或未定稿"

        # chapter_brief 携带作者意见 + 原文,write_draft 的既有提示词原样消费;
        # as_node=chapter_slice:出边固定接 build_context,续跑走完整生产管道
        values = {
            "chapter_no": no,
            "chapter_brief": (f"【重构第{no}章】作者要求:{feedback.strip()}\n\n"
                              f"【原章内容(保持与前后的连贯,按作者要求重写)】\n"
                              f"{(old['content'] or '')[:3000]}"),
            "revamp_pending": True,
            "revamp_done": False,
            "rewrite_count": 0,
            "rewrite_exhausted": False,
            "polish_count": 0,           # 串行双闸计数不跨次残留(ADR-0036)
            "polish_exhausted": False,
            "last_polish_edits": [],     # 编辑回执不跨次残留(ADR-0040)
            "quality_review": {},        # 上一轮风格意见不得串入本次结构闸
        }
        graph.update_state(cfg, values, as_node="chapter_slice")
        self._launcher(story_id, user_id)
        return (f"已开始重构第 {no} 章:流程为重写 → 三评审 → 等你确认"
                f"(工作台会出现章节确认卡);若后续章节与新稿冲突,确认卡会"
                f"附冲突标注。生成期间无法同时启动新一轮生成。")


def run_conflict_check(deps, state: dict) -> list[dict] | None:
    """拍板 b(冲突标注):重写稿对照 N+1 起已定稿章,LLM 找矛盾。

    返回 [{chapter_no, conflict, suggest}];无后续章返回 [];任何失败
    返回 None(降级不阻断人审——冲突检查是辅助标注,不是闸门)。
    """
    story_id = state.get("story_id", "")
    no = state.get("chapter_no", 0)
    rows = deps.conn.execute(
        "SELECT chapter_no, content FROM chapters"
        " WHERE story_id=? AND chapter_no>? AND status='active'"
        " ORDER BY chapter_no", (story_id, no)).fetchall()
    if not rows:
        return []
    later = "\n\n".join(
        f"第{r['chapter_no']}章:{(r['content'] or '')[:400]}" for r in rows)
    prompt = (
        "你是网文连续性审校。第 "
        f"{no} 章刚被作者要求重写,新稿如下:\n\n"
        f"{(state.get('draft') or '')[:2000]}\n\n"
        f"其后已定稿章节开头如下:\n\n{later}\n\n"
        "请找出新稿与后续章节的矛盾(人物状态/事实/时间线/伏笔)。"
        '只输出 JSON 数组,元素形如 {"chapter_no": 章号, "conflict": "矛盾描述",'
        ' "suggest": "处理建议(重写该章/可忽略)"};无矛盾输出 []。')
    from app.core.llm.base import ChatMessage

    try:
        resp = deps.llm.chat(
            AgentRole.REVIEWER,
            [ChatMessage(role="user", content=prompt)],
            stage="conflict_check", story_id=story_id,
            response_format={"type": "json_object"},
            max_tokens=1500)
        text = resp.content.strip()
        data = json.loads(text[text.index("["):text.rindex("]") + 1])
        out = [{"chapter_no": int(c.get("chapter_no", 0)),
                "conflict": str(c.get("conflict", "")),
                "suggest": str(c.get("suggest", ""))}
               for c in data if isinstance(c, dict)]
        return out
    except Exception:  # noqa: BLE001 — 标注失败不阻断人审
        return None
