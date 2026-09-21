"""兼容转发(ADR-0030 阶段4):build.py 已三分。

节点函数 -> app.graph.nodes;路由谓词 -> app.graph.routes;
图装配 -> app.graph.wiring。本模块再导出全部符号,保持既有
import 路径(api/deps、studio、tests、evals、graph_canvas)不变。
"""

from app.graph.nodes import (  # noqa: F401
    _node,
    build_context,
    confirm_master_outline,
    confirm_stage_outline,
    finalize,
    next_chapter,
    struct_merge,
    style_merge,
    user_review_chapter,
)
from app.graph.routes import (  # noqa: F401
    MASTER_REGEN_LIMIT,
    REWRITE_LIMIT,
    STAGE_REGEN_LIMIT,
    STYLE_POLISH_LIMIT,
    _master_escalation,
    _stage_escalation,
    route_after_master_review,
    route_after_review,
    route_after_stage_agent_review,
    route_after_stage_review,
    route_after_struct_review,
    route_after_style_review,
    route_chapter_entry,
    route_entry,
    route_next,
)
from app.graph.wiring import build_graph  # noqa: F401
