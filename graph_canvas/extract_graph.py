"""从真实 build_graph() 导出图结构 JSON(零手工维护,代码即真相)。

在子进程中运行(serve.py 每次变更调起),天然规避模块热重载的残留状态:
build.py 改坏时 graph.json 保留上一版,stderr 带回给前端显示。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
sys.path.insert(0, str(PROJECT))

OUT = ROOT / "public" / "graph.json"

# 节点着色规则:仅影响样式,按名字前缀启发式分类;改名/新增节点默认 agent。
KIND_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("interrupt", ("confirm_", "user_review")),
    ("review", ("review_", "merge_")),
    ("route", ("next_chapter", "persist_", "build_context", "chapter_slice")),
]

# 阶段分组(与 wiring.py 的三个接线区块对齐):驱动画布分区布局与组容器。
GROUPS: list[dict] = [
    {"key": "coauthor", "zh": "共创前置", "accent": "#8b7cf6"},
    {"key": "loop", "zh": "章节生产循环", "accent": "#38bdf8"},
    {"key": "finalize", "zh": "定稿管道", "accent": "#34d399"},
]

# 节点语义元数据(单一来源):中文名/分组/图标(Phosphor 名)/一句话职责。
# 改名/新增节点未登记时走 fallback:zh=原名、不进组、图标按 kind 兜底。
NODE_META: dict[str, dict] = {
    # ---- 共创前置(ADR-0011)----
    "coauthor": {"zh": "世界观共创", "group": "coauthor", "icon": "Chats",
                 "desc": "访谈汇总用户构想,产出世界观与能力契约"},
    "init_characters": {"zh": "角色草案", "group": "coauthor", "icon": "Users",
                        "desc": "生成角色卡草案(仅暂存 state)"},
    "persist_characters": {"zh": "角色暂存", "group": "coauthor", "icon": "Stack",
                           "desc": "草案透传,确认总大纲时才落库"},
    "gen_master_outline": {"zh": "总大纲生成", "group": "coauthor", "icon": "BookOpenText",
                           "desc": "含卷结构的整书大纲"},
    "review_master_outline": {"zh": "总大纲评审", "group": "coauthor", "icon": "Eye",
                              "desc": "大纲 Agent 裁决 pass/revise"},
    "confirm_master_outline": {"zh": "总大纲确认(人工)", "group": "coauthor", "icon": "HandTap",
                               "desc": "书之根基,永远人工拍板(ADR-0016)"},
    # ---- 章节生产循环 ----
    "next_chapter": {"zh": "章节推进", "group": "loop", "icon": "FastForward",
                     "desc": "阶段边界判定 + 跨章计数器重置"},
    "stage_outline": {"zh": "阶段细纲生成", "group": "loop", "icon": "ListBullets",
                      "desc": "当前阶段 N 章的剧情规划"},
    "review_stage_outline": {"zh": "细纲评审", "group": "loop", "icon": "Eye",
                             "desc": "细纲质量裁决"},
    "confirm_stage_outline": {"zh": "细纲确认(人工)", "group": "loop", "icon": "HandTap",
                              "desc": "自动模式绿则直通,循环耗尽转人工"},
    "chapter_slice": {"zh": "章节切片", "group": "loop", "icon": "Scissors",
                      "desc": "从细纲切出本章要点"},
    "build_context": {"zh": "上下文组装", "group": "loop", "icon": "Database",
                      "desc": "检索记忆/伏笔/禁用短语/用户指令"},
    "write_draft": {"zh": "正文写作", "group": "loop", "icon": "PenNib",
                    "desc": "生成章节草稿"},
    "polish_draft": {"zh": "风格精校", "group": "loop", "icon": "MagicWand",
                     "desc": "便宜模型局部修,不重掷全文(ADR-0038)"},
    "review_draft_outline": {"zh": "结构评审", "group": "loop", "icon": "Crosshair",
                             "desc": "大纲一致性裁决(结构闸输入)"},
    "review_threads": {"zh": "伏笔评审", "group": "loop", "icon": "Lightbulb",
                       "desc": "伏笔变更建议 + 超龄复核,不参与闸门表决"},
    "review_quality": {"zh": "风格评审", "group": "loop", "icon": "Article",
                       "desc": "文风/复读裁决(风格闸输入)"},
    "struct_merge": {"zh": "结构闸", "group": "loop", "icon": "TrafficSignal",
                     "desc": "大纲一致性+字数下限,revise 全文重写(上限3)"},
    "style_merge": {"zh": "风格闸", "group": "loop", "icon": "PaintBrush",
                    "desc": "结构过了才跑,revise 走精校(上限2)"},
    "user_review_chapter": {"zh": "章节人审(人工)", "group": "loop", "icon": "HandTap",
                            "desc": "确认定稿/打回重写 + 伏笔二次确认"},
    # ---- 定稿管道 ----
    "event_extract": {"zh": "事件抽取", "group": "finalize", "icon": "MagnifyingGlass",
                      "desc": "从定稿抽 facts/beliefs/entities"},
    "update_characters": {"zh": "角色更新", "group": "finalize", "icon": "UserFocus",
                          "desc": "角色卡增量变更(与消歧/摘要并行)"},
    "entity_resolve": {"zh": "实体消歧", "group": "finalize", "icon": "Fingerprint",
                       "desc": "新实体/别名/链接归并(ADR-0015)"},
    "summary": {"zh": "章节摘要", "group": "finalize", "icon": "TextAa",
                "desc": "章摘要 + 阶段末聚合摘要"},
    "finalize": {"zh": "定稿落库", "group": "finalize", "icon": "FloppyDisk",
                 "desc": "变更集单事务落库(编排原子性)"},
}


def classify(name: str) -> str:
    if name in ("__start__", "__end__"):
        return "terminal"
    for kind, prefixes in KIND_RULES:
        if name.startswith(prefixes):
            return kind
    return "agent"


def meta_of(name: str) -> dict:
    """节点语义元数据;未登记节点降级:中文名=原名,不进组,desc 空。"""
    m = NODE_META.get(name)
    if m:
        return {"zh": m["zh"], "group": m["group"], "icon": m["icon"],
                "desc": m["desc"]}
    return {"zh": None, "group": None, "icon": None, "desc": ""}


def _pretty(name: str) -> str:
    if name == "__start__":
        return "START"
    if name == "__end__":
        return "END"
    return name


def _edge_label(data) -> str:
    """本环境 langchain_core:条件边 data 直接是标签字符串(dict 形态也兼容)。"""
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        return data.get("label", "")
    return ""


def extract() -> dict:
    from app.graph.build import build_graph
    from app.graph.runtime import Deps

    deps = Deps(conn=None, repo=None, retrieval=None, llm=object())
    graph = build_graph(deps).get_graph()

    nodes = []
    for n in graph.nodes:               # 本版本 nodes 即 id 字符串
        nodes.append({"id": n, "label": _pretty(n), "kind": classify(n),
                      "meta": meta_of(n)})

    edges = []
    for e in graph.edges:
        edges.append({
            "source": e.source,
            "target": e.target,
            "label": _edge_label(e.data),
            "conditional": bool(e.conditional),
        })

    nodes.sort(key=lambda x: x["id"])
    edges.sort(key=lambda x: (x["source"], x["target"]))
    version = hashlib.sha1(
        json.dumps({"nodes": nodes, "edges": edges, "groups": GROUPS},
                   sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:12]
    return {
        "version": version,
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "groups": GROUPS,
        "nodes": nodes,
        "edges": edges,
        "error": None,
    }


def main() -> int:
    try:
        payload = extract()
    except Exception as exc:  # noqa: BLE001 —— 导出失败要带回给前端,不让 serve 崩
        import traceback
        print(traceback.format_exc(), file=sys.stderr)
        payload = {"version": None, "generated_at": None, "nodes": [], "edges": [],
                   "node_count": 0, "edge_count": 0,
                   "error": f"{type(exc).__name__}: {exc}"}
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, OUT)   # 原子替换:前端不会读到半截 JSON
    print(f"graph.json -> {len(payload['nodes'])} nodes / {len(payload['edges'])} edges"
          f" (version {payload['version']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
