# 墨澜 MoLan · 多 Agent 长篇小说生成系统

基于 LangGraph 的多 Agent 小说合写系统:Agent 注册表 16 项(规划/写作/评审/抽取/摘要/伏笔治理)、11 级模型路由角色分工协作,在 60 章量级合成世界的实测中维持设定一致、伏笔可追踪、角色视角严格隔离的长篇叙事。

> 完整设计(ADR 决策记录编号至 ADR-0042 + trade-off 论证)见 [DESIGN_FINAL.md](DESIGN_FINAL.md) · 书籍导入与状态重建设计见 [docs/BOOK_IMPORT_DESIGN.md](docs/BOOK_IMPORT_DESIGN.md)(设计态) · 公网部署见 [docs/DEPLOY.md](docs/DEPLOY.md) · 版本更新见 [CHANGELOG.md](CHANGELOG.md)

## 核心特性

| 能力 | 实现 |
|---|---|
| **事实库记忆** | facts(客观,版本链+置信度分层)/ beliefs(角色认知,误信建模)分表;世界状态任意章节时点可回放 |
| **POV 视角隔离** | 角色记忆 = 查询时投影(可见性矩阵过滤);查询层 0/1345 泄漏(60 章世界逐条比对)+ 生成上下文分级硬过滤(ADR-0024)双口径;naive 滑窗 ~65% 条目泄密 |
| **混合检索** | 结构化查表主路 + 实体链接扩展 + 向量兜底;远距离召回 100%(naive 为 0%),10-60 章实测不衰减 |
| **双评审闭环** | 大纲一致性 + 质量审校并行评审,自动重写循环(上限可配),LLM 输出 schema 硬校验+失败安全降级(绝不静默 pass) |
| **human-in-the-loop** | 三类中断点:总大纲确认 / 阶段细纲确认 / 章节审阅+伏笔人工复核;中断卡持久化,刷新/重启不丢 |
| **对话工作台** | ChatDock(ADR-0031):ReAct 独占节点,查询/指令/停止工具 + JSONL 决策链;revamp_chapter 对话式重构历史章节 |
| **认证与多租户** | JWT 双 token(access 2h + refresh 7d 滑动续期,ADR-0029)+ 管理员开户 + story_members 租户隔离(ADR-0022);同一 story 单 active run 互斥(ADR-0023) |
| **成本工程** | 按 Agent 分级路由(强/中/便宜),全链路 usage 埋点按用户/run 归因,每日 token 预算闸门(story/全局) |
| **可插拔架构** | 单向依赖规则(import-linter 分层契约机械化保障)+ Agent 注册表 + 插件契约 |

## 架构

```
React(SSE) → FastAPI(JWT 认证)→ LangGraph(16 Agent 节点 + 生产循环)
                              ↓
                    记忆系统(仓储层 ACL fail-closed + 检索服务)→ SQLite(27 表,版本化迁移)
                              ↘
                    LLM 接入层(抽象工厂 + 策略路由 + 埋点 + 失败台账)
```

## 快速开始

```bash
# 环境:Python 3.12+(conda 环境 novel-agent)
conda activate novel-agent
pip install -e ".[dev]"

# 配置
cp .env.example .env       # 填入 GLM_API_KEY;公网部署必设 NOVEL_JWT_SECRET / NOVEL_ADMIN_PASSWORD

# 后端(端口 8000;首次启动自动 seed 管理员并打印默认口令警告)
uvicorn app.main:app --reload

# 前端(端口 5173,另一终端)
cd frontend && npm install && npm run dev
# 浏览器打开 http://localhost:5173 :落地页 → 登录 → 进入工作台
```

默认管理员 `admin/admin123`(仅限本机自用;公网部署必须设置 `NOVEL_ADMIN_PASSWORD`,详见 [docs/DEPLOY.md](docs/DEPLOY.md))。

## 测试与评测

```bash
pytest                          # 191 项测试(记忆/权限/版本链/图端到端/API/认证/LLM 契约/数据完整性/运行状态/可观测性/并发集成/导出)
lint-imports                    # 分层架构契约(机械化守护)

python -m evals.run_memory_eval         # P2.5:记忆层基线对比(零 token)
python -m evals.run_e2e_eval --mode replay   # P6:端到端指标管线(零 token)
python -m evals.run_e2e_eval --mode real --chapters 3   # 真实模型评测(需 GLM_API_KEY)
```

## 项目结构

```
app/
├── core/       config(三级优先级)· llm/(工厂+路由+埋点)· security(argon2)
├── db/         SQLite DDL(27 表,版本化迁移)+ ACL 种子
├── memory/     repository(fail-closed)· retrieval · world(版本链回放)
├── graph/      state · agents(注册表)· build(生产循环)· runtime(编排/运行状态)
├── observability/  usage_log 落库 sink
├── auth.py     JWT 认证 + 租户过滤;main.py FastAPI · SSE · 中断点端点
evals/          记忆层评测 · 端到端评测(报告可复现)
frontend/       React:落地页 + 工作台
docs/           部署文档(DEPLOY.md)
```
