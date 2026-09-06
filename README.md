# 墨澜 MoLan · 多 Agent 长篇小说生成系统

基于 LangGraph 的多 Agent 小说合写系统:六个 LLM Agent 分工协作,在**篇幅无上限**的约束下维持设定一致、伏笔可追踪、角色视角严格隔离的长篇叙事。

> 完整设计(12 个 ADR + trade-off 论证)见 [DESIGN_FINAL.md](DESIGN_FINAL.md) · 决策过程见 [PROJECT_DESIGN.md](PROJECT_DESIGN.md)

## 核心特性

| 能力 | 实现 |
|---|---|
| **事实库记忆** | facts(客观,版本链+置信度分层)/ beliefs(角色认知,误信建模)分表;世界状态任意章节时点可回放 |
| **POV 视角隔离** | 角色记忆 = 查询时投影(可见性矩阵过滤),实测泄漏率 0%(naive 基线 ~65%) |
| **混合检索** | 结构化查表主路 + 实体链接扩展 + 向量兜底;远距离召回 100%(naive 为 0%),篇幅增长不衰减 |
| **双评审闭环** | 大纲一致性 + 质量审校并行评审,自动重写循环(上限可配),forced_pass 显式降级 |
| **human-in-the-loop** | 三类中断点:总大纲确认 / 阶段细纲确认 / 章节审阅+伏笔人工复核 |
| **成本工程** | 按 Agent 分级路由(强/中/便宜),全链路 usage 埋点 |
| **可插拔架构** | 单向依赖规则(import-linter 进 CI)+ Agent 注册表 + 插件契约 |

## 架构

```
React(SSE) → FastAPI → LangGraph(6 Agent + 生产循环)
                              ↓
                    记忆系统(仓储层 ACL fail-closed + 检索服务)→ SQLite(18 表)
                              ↘
                    LLM 接入层(抽象工厂 + 策略路由 + 埋点装饰器)
```

## 快速开始

```bash
# 环境:Python 3.12+(conda 环境 novel-agent)
conda activate novel-agent
pip install -e ".[dev]"

# 配置
cp .env.example .env       # 填入 GLM_API_KEY

# 后端(端口 8000)
uvicorn app.main:app --reload

# 前端(端口 5173,另一终端)
cd frontend && npm install && npm run dev
# 浏览器打开 http://localhost:5173 :落地页 → 进入工作台
```

## 测试与评测

```bash
pytest                          # 51 项测试(记忆/权限/版本链/图端到端/API)
lint-imports                    # 分层架构契约(机械化守护)

python -m evals.run_memory_eval         # P2.5:记忆层基线对比(零 token)
python -m evals.run_e2e_eval --mode replay   # P6:端到端指标管线(零 token)
python -m evals.run_e2e_eval --mode real --chapters 3   # 真实模型评测(需 GLM_API_KEY)
```

## 项目结构

```
app/
├── core/       config(三级优先级)· llm/(工厂+路由+埋点)
├── db/         SQLite DDL(18 表)+ ACL 种子
├── memory/     repository(fail-closed)· retrieval · world(版本链回放)
├── graph/      state · 6 agents · build(生产循环)· runtime(编排原子性)
├── api/        FastAPI · SSE · 中断点端点
evals/          记忆层评测 · 端到端评测(报告可复现)
frontend/       React:落地页 + 工作台
```
