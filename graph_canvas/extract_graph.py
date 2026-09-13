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


def classify(name: str) -> str:
    if name in ("__start__", "__end__"):
        return "terminal"
    for kind, prefixes in KIND_RULES:
        if name.startswith(prefixes):
            return kind
    return "agent"


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
        nodes.append({"id": n, "label": _pretty(n), "kind": classify(n)})

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
        json.dumps({"nodes": nodes, "edges": edges},
                   sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:12]
    return {
        "version": version,
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "node_count": len(nodes),
        "edge_count": len(edges),
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
