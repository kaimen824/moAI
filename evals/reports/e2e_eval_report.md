# P6 端到端评测报告(replay 模式)

> replay 模式验证评测管线与流程指标口径(响应为脚本回放,质量分数无语义意义);
> real 模式(需 GLM_API_KEY)产出真实质量与成本数据。

## 分级路由(默认)

| 指标 | 值 |
|---|---|
| chapters_active | 2 |
| n_review_rounds | 3 |
| avg_rewrite_rounds | 1 |
| n_scored | 19 |
| avg_score | 8.79 |
| avg_consistency | 9 |
| thread_resolve_rate | None |
| facts_confirmed | 2 |
| facts_pending | 2 |
| beliefs | 2 |
| total_calls | 20 |
| total_latency_s | 0.1 |

### 分 Agent 用量

| Agent | 调用 | tokens_in | tokens_out | 累计耗时(s) |
|---|---|---|---|---|
| CHARACTER | 3 | 0 | 0 | 0.0 |
| EVENT | 2 | 0 | 0 | 0.0 |
| OUTLINE | 3 | 0 | 0 | 0.0 |
| REVIEWER | 3 | 0 | 0 | 0.0 |
| SUMMARY | 2 | 0 | 0 | 0.0 |
| SUPERVISOR | 4 | 0 | 0 | 0.1 |
| WRITER | 3 | 0 | 0 | 0.0 |

## 对比:全强模型 vs 分级路由

| 指标 | 全强 | 分级 |
|---|---|---|
| total_calls | 20 | 20 |
| total_latency_s | 0.0 | 0.1 |
