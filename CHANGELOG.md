# Changelog

本项目的全部可观测变化记录于此。口径纪律:数字必须来自仓库内可复现的
测试/评测(pytest、evals);未经实测的能力不写。

## [0.2.0] 2026-09-14 —— 生产化(三方评审 15 项问题修复)

三方生产落地评审(`评审/production_readiness_review_20260913.md`,52/100)
驱动的整体改造:9 个批次、8 篇新 ADR(0021-0028)。目标部署形态:公网、
多用户、单机 SQLite(约 100 用户内,边界见 `docs/DEPLOY.md`)。

### 破坏性变更(升级必读)

- **API 全面启用认证(ADR-0022)**:业务端点一律要求 `Authorization: Bearer <JWT>`;
  无自助注册,账户由管理员经 `POST /admin/users` 开户。此前脚本/客户端若在
  裸奔状态,升级后全部 401。
- **SSE `error` 事件结构化(ADR-0028)**:从裸 `str(exc)` 改为
  `{message(截断 300), error_code, run_id, trace_id?}`;完整堆栈只进服务端
  日志(logger `novel.agent`)。前端如依赖旧文本格式需适配。
- **`target_chapters` 边界 1-50**:出界 422(此前无上限,一次请求可无限铺章)。
- **每日 token 预算闸门(ADR-0026)**:story 级与全局级,超限 429 +
  `X-Error-Code: budget_exceeded`;默认 0=不限,公网部署必须显式设置。
- **工作台需登录**:前端落地页 → 登录 → 工作台;默认管理员
  `admin/admin123` 仅限本机自用。

### 新特性

- **schema 版本化迁移(ADR-0021)**:`schema_migrations` 版本表 + 增量迁移
  (v2-v7);老库升级自动补列/补表,全新库 baseline 直建(现 27 表)。
  升级 = 替换代码重启,无手工 SQL。
- **JWT 认证与多租户(ADR-0022)**:argon2id 口令哈希、短期 token、
  `story_members` 租户隔离;story 列表/详情/抽检队列/合并提案全部按
  可见集合过滤,跨租户访问 404。禁用账户即时失效。
- **双 token 静默续期(ADR-0029)**:登录签发 access(2h)+ refresh(7 天)
  对,类型互斥;`POST /auth/refresh` 换新对(滑动续期,签发时再查
  users.status)。前端 401 先单飞 refresh 并重试原请求一次,仍失败才踢出,
  控制台记录请求 URL 与原因——长会话不再每 2h 被踢回登录,"莫名被踢"
  可凭 F12 一行日志定位。
- **并发互斥与事件归属(ADR-0023)**:同一 story 同时仅允许一个 active run,
  双击/并发第二个请求 409(此前会产生重复章节与事件串台);每 run 的
  `run_id` 经 ContextVar 贯通事件流、usage、traces、评审记录、失败台账。
- **run 状态持久化(ADR-0027)**:新表 `story_run_state` 记录
  running/waiting/idle + 完整中断卡 JSON。进程崩溃后重启,运行状态自动收敛、
  未裁决的中断卡原样还原,前端刷新/重启不再丢卡;`GET /stories/{id}/run-state`
  以 DB 为准合并进程内状态。
- **LLM 输出强契约与弹性(ADR-0026)**:12 处 JSON 调用点全部 Pydantic
  schema 硬校验(真枚举 Literal);解析/校验失败错误回喂自纠一次,仍失败
  抛 `LLMFormatError`(trace_id)进 `llm_failures` 台账;评审类节点失败
  安全降级(大纲/质量评审→revise,伏笔评审→零账本动作),绝不静默 pass。
  Provider client 缓存复用 + 显式超时(120s)/重试(3 次)。
- **可观测性补全(ADR-0028)**:`usage_log`/`agent_traces` 补 `user_id`
  (按触发用户归因成本);`llm_failures` 补 provider 状态码与应用内重试
  次数;新增 `GET /admin/stats`(admin):active runs、近 24h LLM 失败率、
  token 成本按日+story+owner 聚合。
- **部署文档**:`docs/DEPLOY.md` —— 必设环境变量、`NOVEL_*` 全清单、
  单 worker 约束、nginx SSE 反代 / Caddy HTTPS、SQLite 备份、systemd 示例。
- **日志落盘**:`novel.agent` 业务日志与 `uvicorn.error` 服务层错误双通道
  写 `logs/novel.log`(5MB×5 轮转,`NOVEL_LOG_DIR`/`NOVEL_LOG_LEVEL` 可调)
  ——控制台进程一关现场即失,崩溃/卡死从此有账可查。

### 修复

- **单事务定稿(ADR-0024)**:总大纲确认(角色种子落库 + 大纲归档)并入
  单事务,中途失败整体回滚——不再出现"大纲已确认但角色卡缺失"的半成品。
- **伏笔 thread_id 全链路(ADR-0025)**:评审契约强制回传伏笔 id,定稿按
  id 精确命中;LIKE 降级必留审计痕迹——不再出现描述相似即误匹配的定向
  变更错位。
- **POV 泄漏口径修正 + 分级硬过滤(ADR-0024)**:查询层实测 0/1345 泄漏
  (60 章世界逐条比对);vector_hits 结果进入 writer 上下文前按可见性
  分级硬过滤,补上"查询层干净但上下文拼装泄漏"的缺口。
- **run-state 误报**:run 完成后,事件快照里的旧 interrupt 不再令
  `run-state` 误报 waiting(以 DB 状态为准)。
- **通用节点异常留痕**:provider 429/超时等非坏-JSON 异常同样落
  `llm_failures` 台账(此前只有坏输出留痕)。
- **文档宣称与实现对齐(评审 6.14)**:落地页移除未实现的 IF 线宣传,换为
  已实现的断点续写;POV 泄漏改"0/1345 查询层逐条比对"实测口径;
  README 篇幅口径改 60 章实测;DESIGN_FINAL 4.3/4.4(章节版本化/IF 线)
  加未实现标记;AUDIT.md 补 2026-09-14 复审注记。

### 质量基线

- `pytest`:**140 passed**(新增 LLM 契约/数据完整性/运行状态/可观测性/
  并发集成五个测试面);`lint-imports` 分层契约 KEPT。
- 评测口径(零 token 可复现):远距离召回 100%(naive 0%);
  POV 查询层 0/1345(naive ~65% 条目泄密);10-60 章合成世界不衰减。

### 已知边界(诚实口径)

- 单 worker 进程:`_active` 互斥与引擎单例在进程内存中,多 worker/多副本
  未经设计,请勿上线(横向扩容需先迁移 PostgreSQL + Redis)。
- 事件流不持久化:重启后前端凭 run-state(持久化中断卡)+ Codex 重建视图,
  一般过程事件不回放。
- IF 线番外、定稿后章节版本化改写:仍未实现(设计见 DESIGN_FINAL 4.3/4.4,
  状态见 AUDIT.md),落地页已停止宣传。

### 升级步骤

1. 备份数据库:`sqlite3 data/novel_agent.db ".backup backup.db"`(勿直接 cp 活库)。
2. 设置环境变量:`NOVEL_JWT_SECRET`(必改)、`NOVEL_ADMIN_PASSWORD`(必改)、
   按需设 `NOVEL_STORY/GLOBAL_DAILY_TOKEN_BUDGET`;完整清单见 `docs/DEPLOY.md`。
3. 替换代码、重启进程;首次启动自动执行迁移 v2-v7 并 seed 管理员。
4. 为既有用户开户(`POST /admin/users`),客户端改携 Bearer token。

## [0.1.0] —— 初始版本

多 Agent 长篇小说生成系统首版:6+ Agent 协作、事实库记忆(facts/beliefs
版本链)、POV 查询层隔离、混合检索、双评审闭环、三类人工中断点、分级模型
路由。无认证、单用户假设、无迁移机制(schema 一次性直建 18 表)。
