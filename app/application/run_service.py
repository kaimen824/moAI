"""运行用例服务(ADR-0030 阶段1):预算闸门等业务规则。

application 层不依赖表现层:超限抛 BudgetExceeded,api 层负责转换为
429 + X-Error-Code 响应(错误契约不变)。用量聚合 SQL 在
infrastructure.queries.ObservabilityQueries(ADR-0030 阶段3 收敛)。
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.config import get_settings
from app.infrastructure.queries import ObservabilityQueries


class BudgetExceeded(Exception):
    """每日 token 预算超限(story 级或全站级)。"""

    def __init__(self, scope: str, spent: int, limit: int, message: str):
        super().__init__(message)
        self.scope = scope            # story | global
        self.spent = spent
        self.limit = limit
        self.message = message


def check_daily_budget(deps, story_id: str) -> None:
    """每日 token 预算闸门(评审 6.10 / ADR-0026):story 级与全局级,
    按 usage_log 的 UTC 日聚合比对配额。配额 0 = 不限(默认,单机自用);
    公网部署经环境变量设定。"""
    settings = get_settings()
    limits = {
        "story": (settings.story_daily_token_budget, "本 story 今日 token 预算已用尽"),
        "global": (settings.global_daily_token_budget, "全站今日 token 预算已用尽"),
    }
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    usage = ObservabilityQueries(deps.conn)

    for scope, (limit, message) in limits.items():
        if limit <= 0:
            continue
        spent = usage.spent_today(
            today, None if scope == "global" else story_id)
        if spent >= limit:
            raise BudgetExceeded(scope, spent, limit, message)
