# 生产化修复方案(对照三方评审 production_readiness_review_20260913)

## 1. 输入与所有者决策记录

| 项 | 值 |
|---|---|
| 依据报告 | `评审/production_readiness_review_20260913.md`(总分 52/100,1 P0 + 9 P1 + 5 P2) |
| 我方核实 | 15 项逐条核对代码,14 项属实、1 项部分属实(LLM timeout/retry:OpenAI SDK 有默认值但未显式配置、client 每次 new、无业务级限流) |
| 部署目标 | **公网多用户**(所有者裁决 2026-09-14) |
| 修复范围 | **全部 15 项 + 建议项精华** |
| 认证形态 | **登录校验发 token(JWT Bearer),统一鉴权**——前端 SSE 已是 fetch+getReader(`frontend/src/api.js:13-40`),Authorization header 直接可加,无需改造 SSE 通道 |
| 注册策略 | **管理员开户**(无开放注册,不依赖邮箱;LLM 成本不可刷) |
| pending 事实 | **confirmed 全量 + pending 以低置信标注注入** |
| vector_hits POV | **分级硬过滤**(ADR-0014 修订,见批次 3) |

## 2. 核实结论摘要(逐项)

| # | 问题 | 核实 | 关键证据 |
|---|---|---|---|
| 6.1 | API 无认证/授权/租户 | 属实 | `app/main.py` 无任何中间件/Depends 校验;22 张表无 users、无 owner_id |
| 6.2 | 同 story 可重复运行 | 属实 | `main.py:121` `_active.add` 无检查;二次 generate 还会 `clear_events` 踢掉首轮订阅者 |
| 6.3 | 事件归属不可靠 | 属实 | `runtime.py:55` 全局 `_current_thread`,`main.py:116` 每次覆盖;`_trace_sink` 等节点内 emit 用默认归属 |
| 6.4 | 重启后恢复不可靠 | 属实 | `main.py:173` 状态只由内存 `_active` + `_history` 推导;DDL 无 run 状态表 |
| 6.5 | pending/rejected 污染 POV | 属实 | `world.py:88-100` 无 status 过滤(`_FACT_VALID` 同病);对照 `retrieval.py:140`、`main.py:310` 有过滤 |
| 6.6 | vector_hits 削弱 POV | 属实(ADR-0014 有意为之,本次修订) | `retrieval.py:119-129` 兜底不过滤可见性;`build.py:195` 注释与实现矛盾 |
| 6.7 | 伏笔 description 模糊匹配 | 属实 | `runtime.py:660-672/678-683` 前 12 字符 LIKE;LLM 输出的 id 被忽略 |
| 6.8 | outline 与种子落库不原子 | 属实 | `build.py:110-126` 两段事务;embedding 静默吞错后仍 commit |
| 6.9 | LLM JSON 无 schema/重试 | 属实 | `base.py:35-43` loose 解析直通;无 Pydantic 校验、无失败记录 |
| 6.10 | LLM 超时/重试/预算缺失 | 部分属实 | SDK 默认 timeout=600s/max_retries=2 未显式配置;client 每次 new(`openai_compat.py:116`);`GenerateRequest.target_chapters` 无上限(`main.py:65-70`) |
| 6.11 | 阶段摘要漏末章 | 属实 | SummaryNode 在 finalize 前执行,聚合源是 DB,当前章摘要尚未入库 |
| 6.12 | 指令过早标记消费 | 属实 | `runtime.py:120-134` 取走即 UPDATE consumed |
| 6.13 | 可观测性不足 | 属实 | 四张观测表无 run_id/user_id/错误栈/status code/retry |
| 6.14 | 文档超前宣称 | 属实 | Landing IF 线零实现;README "47 项测试"实为 82;"篇幅无上限""零泄漏"绝对化 |
| 6.15 | 迁移无版本管理 | 属实 | `ddl.py:299-328` PRAGMA 探测式 ALTER,无版本表 |

## 3. 修复总原则

1. **遵守报告第 9 节"不建议现在修改"**:不引 Kafka/MQ、不换 PostgreSQL、不拆 `commit_finalize()` 单事务、不做 IF 线/章节版本化、不引 MCP/CQRS、不加深 provider 抽象。
2. **每批交付即完整**:测试全绿 + ADR 补记 + 当场 commit,不攒批。
3. **schema 变更一律走版本化迁移**(批次 0 立起后)。
4. 每批验收以"新增回归测试 + 原有测试全绿"为准,最终补端到端并发/越权/重启/容错测试(批次 8)。

---

## 4. 批次计划(依赖排序)

### 批次 0:schema migrations 版本化(6.15)【先行基建】

后续每批都要改 DDL(users、run 状态表、观测表加列、唯一索引),先立版本机制,避免每批往 `_MIGRATIONS` 列表打补丁。

改动:
- 新表 `schema_migrations(version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT)`。
- 现有 `SCHEMA_SQL` 全量基线化为 **version 1**;`ddl.py:302-316` 现存 `_MIGRATIONS` 压缩为 version 2(存量库补列)。
- `migrate()` 重写:按版本升序逐个事务执行;存量库通过版本表判位,全新库直接跑基线;失败即回滚该版本并停机报错(不静默继续)。
- 旧库升级路径:无版本表但表已存在 → 探测标记为 baseline 1,再追版本 2+。

验收:全新库 / 旧式存量库 / 半迁移中断库 三路径测试;重复执行幂等。
ADR:**ADR-0021 schema migrations 版本化**。

### 批次 1:认证与多租户(6.1,P0)

改动:
- **users 表**:`id / username UNIQUE / password_hash / role(admin|user) / status(active|disabled) / created_at`。密码哈希 argon2(`argon2-cffi`;行业当前首选,bcrypt 可接受)。
- **stories 加 `owner_id`**;存量 story 回填给首个 admin。新表 **`story_members(story_id, user_id, role(owner|editor|viewer), PRIMARY KEY(story_id, user_id))`**——单人多书 owner 即够,协作预留。
- **鉴权链路**:`POST /auth/login` 校验后签发短期 JWT(access 2h,HS256,SECRET 走环境变量);FastAPI 依赖 `get_current_user` 解析 Bearer;`get_admin_user` 限 admin。
- **管理员端点**:`POST /admin/users` 开户、`PATCH /admin/users/{id}` 禁用/重置密码。无自助注册。
- **全端点收口**(报告 6.1 列的都改):
  - `/stories*` → 列表只返回 owner/member 可见;详情类校验成员资格,非法访问 404(不泄露存在性,行业惯例)。
  - `GET /facts/pending` / `POST /facts/{id}/review` / `GET /entities/pending` / `POST /entities/{id}/review` → 按 story 归属过滤到当前用户可见集合。
  - `/config/models` → **admin 专用**(报告指出它是全局配置)。
  - `/admin/*` → admin。
- **测试基建**:conftest 提供 `auth_client`(admin/user 两个 fixture,自动登录带 token)——现有 82 个测试全部改造走带token客户端,这是一次性成本。
- SSE 端点鉴权:`j()` / `sse()` 统一注入 `Authorization` header(`api.js` 两处)。

验收:未带 token 全端点 401;user A 访问 user B 的 story 404;非 admin 改配置 403;密码哈希不可逆;伪造/过期 token 401。
ADR:**ADR-0022 认证与多租户边界(JWT + 管理员开户 + story_members)**。

### 批次 2:并发与运行完整性(6.2 + 6.3 + 唯一约束)

改动:
- **active run 互斥**:`_sse_run()` 入口 `with _active_lock: if thread_id in _active: raise HTTPException(409); _active.add(thread_id)`(main.py:109-121;`_sse_run` 是同步函数跑在线程池,check-then-add 有真实竞态窗口,必须持锁)。generate/resume 共用此检查;worker finally 里 `discard` 保持现状。
- **chapters 部分唯一索引**:`CREATE UNIQUE INDEX idx_chapters_active ON chapters(story_id, chapter_no) WHERE status='active'`(SQLite 3.8+ partial index,走批次 0 迁移)。`commit_finalize` 捕获 IntegrityError 转结构化错误。
- **事件归属**:`Deps._current_thread` 删除,改为 `contextvars.ContextVar("thread_id")`,`_sse_run` worker 线程启动时 set;`emit()` 无显式 thread_id 时读 contextvar(节点内 emit 如 `_trace_sink`、fan-out 线程各自继承正确的 run 上下文)。`clear_events` 不再因二次启动破坏在跑 run(互斥已挡,双保险)。
- **run_id 引入**(为批次 5 贯通做准备):`_sse_run` 生成 uuid,`emit` 事件体带 `run_id` 字段。

验收:并发双击 generate 第二个 409;两个 story 并发生成,事件/usage 各归各 thread(多线程集成测试);同章号重复 active 被约束拒绝。
ADR:**ADR-0023 active run 互斥与并发事件归属(contextvars)**。

### 批次 3:数据正确性(6.5 + 6.6 + 6.7 + 6.8 + 6.11 + 6.12)

改动:
- **6.5 POV status 过滤**:`world.py` `get_pov_memory()` 与 `_FACT_VALID` 增加 `f.status != 'rejected'`;confirmed 正常注入,pending 返回时携带 `confidence='low'` 标记,writer 渲染层显式分节「低置信线索(未经审核,可作暗线素材,不得作为确定事实陈述)」。`replay_world` 同口径。与 `retrieval.py:140` 现有过滤对齐。
- **6.6 vector_hits 分级硬过滤(ADR-0014 修订)**:`_vector_fallback()` 查询改为——
  - 排除:存在 visibility 行、且当前 POV 角色不在 known 集的事实(信息差事实,**硬滤除**)。
  - 保留:无 visibility 行的背景/环境事实(叙事素材,ADR-0014 原语义)。
  - SQL:`LEFT JOIN fact_visibility fv ON ... AND fv.character_id = :pov` 然后 `WHERE NOT (fv.id IS NOT NULL AND ...pov 不在 known 集)` 具体实现时定;`build.py:195` 的矛盾注释同步修正。
  - `leak_eval` 口径升级:writer 上下文层(vector_hits + POV 记忆)泄漏率纳入评测,与查询层口径分开报告。
- **6.7 伏笔 thread_id 全链路**:plant 落库生成 uuid 后**把 id 写回 GraphState.thread_changes**;伏笔评审(thread_reviewer,ADR-0020)输出 schema 强制 `thread_id` 引用;中断卡/Codex 展示 id;`commit_finalize()` advance/resolve/drop/escalate 一律按 `thread_id` 精确 UPDATE;id 缺失或不命中时降级为 LIKE 匹配 + `retrieval_audit` 留痕(兼容旧数据),新数据不允许无 id。
- **6.8 单事务落库**:`confirm_master_outline()` 重构——persist_characters 拆两步:①准备阶段(embedding 等外部 IO,失败降级 None 并**记录**,不再静默 `except: pass`);②纯 DB 写事务(outline + characters + entities + aliases + links 同一 `BEGIN...COMMIT`),失败整体 rollback。重试幂等由 upsert + outline 版本号判断保证。
- **6.11 阶段摘要含末章**:`SummaryNode` 聚合输入 = DB 查询(`stage_chapter_summaries`)**并入 `state["chapter_summary"]`**(当前章,`state.py:53` 已有字段),不再依赖 finalize 后才可见。
- **6.12 指令延迟消费**:`take_pending_directives()` 改为只读不标记;`commit_finalize()` 事务末尾按 id 批量标记 `consumed_at`(同一事务,原子);失败/中断时指令保持 pending,下轮重新注入(幂等)。

验收:rejected 不出现在 POV 记忆;pending 带低置信标记;伏笔同描述前缀两条时按 id 精确命中(回归测试);outline 落库中途注入失败 → 角色零落库且 outline 可重试;阶段末章聚合断言含本章摘要;生成失败后指令仍 pending。
ADR:**ADR-0024 ADR-0014 修订:向量兜底分级硬过滤**;**ADR-0025 伏笔 thread_id 精确账目**;pending 标注并入 ADR-0024 或单列。

### 批次 4:LLM 强契约与弹性(6.9 + 6.10 + 建议项)

改动:
- **输出 schema(6.9)**:为每个 JSON stage 建 Pydantic 模型(新增 `app/graph/agents/schemas.py`):细纲、大纲评审、章节评审、伏笔评审(thread_changes 含 thread_id)、事件抽取、实体消歧 verdict、意图卡。`ask_json()` 泛型化 `ask_json[T](..., schema: type[T]) -> T`:解析 → `model_validate` → 失败则**将校验错误回喂模型重试 1 次**(self-correction,行业常规)→ 仍失败抛 `LLMFormatError`。
- **失败台账**:新表 `llm_failures(id, story_id, run_id, stage, node, trace_id, raw_output(截断 4k), error, created_at)`(走迁移);SSE error 事件带 `trace_id` + 稳定 error_code。评审类节点解析失败**安全默认 `revise`**,绝不静默 pass。
- **parse_json_loose 加固**:首尾花括号截取改为 `json.JSONDecoder.raw_decode` 扫描 + 截断 JSON 检测(原始输出入 llm_failures 便于归因)。
- **弹性(6.10)**:provider client 按 provider 缓存复用(`factory` 层,`openai_compat.py:116` 不再每次 new);显式配置 timeout(默认 120s,可配)与 `max_retries=3`(SDK 内置 429/5xx 指数退避,显式化);`GenerateRequest.target_chapters: int = Field(default=1, ge=1, le=50)`;**每日 token 预算**:`usage_log` 按日聚合比对配额(用户级/全局),超限返回 429 + 明确错误码(公网成本闸门)。

验收:坏 JSON/错枚举/截断输出 → 重试一次 → llm_failures 落痕 → 节点安全降级;fake provider 验证重试路径;target_chapters=-1 → 422;预算超限 → 429。
ADR:**ADR-0026 LLM 输出强契约(schema 校验/自纠重试/失败台账)**。

### 批次 5:run 状态持久化 + run_id 贯通(6.4 + 6.13 核心)

改动:
- **新表 `story_run_state`**:`story_id PK / run_id / status(running|waiting|idle) / interrupt_type / interrupt_payload(JSON, 中断卡持久化) / target_chapters / error_code / error_stage / started_at / updated_at`(走迁移)。
- **写入点**:worker 启动 → running;`__interrupt__` → waiting + payload;done/stopped/error → idle + 错误码;批次 2 的 run_id 在此落库。
- **run_state() 合并逻辑**:DB 状态为主,进程 `_active` 为辅(DB=waiting 且本进程活跃 → running)。服务启动时:`status='running'` 的残留(进程崩溃)重置为 waiting(若 checkpoint 有 interrupt)或 idle,并提示用户可 resume——LangGraph checkpoint 本就在 SqliteSaver,恢复语义不变。
- **事件流**:中断卡经 `interrupt_payload` 持久化;一般事件流不持久化(重启后前端凭 run-state + codex 重建视图,避免引入事件表)。
- **run_id 贯通**:`usage_log / agent_traces / review_results / llm_failures` 全部加 `run_id` 列(用户维度已在批次 1 收口时随端点写入 usage 侧);SSE 每事件带 run_id。

验收:模拟重启(重建 engine + Deps)后 run_state 返回 waiting + 中断卡可 resume;崩溃残留 running 被启动逻辑收敛;run_id 在 run_state/usage/trace/review 四表一致。
ADR:**ADR-0027 运行状态持久化与 run_id 贯通**。

### 批次 6:可观测性补全(6.13 剩余)

改动:
- `usage_log / agent_traces` 加 `user_id`(批次 1 已有 owner 语义,观测侧补列);llm_failures 补 provider status_code、retry_count。
- **结构化错误**:worker 异常不再裸 `str(exc)`——服务端 `logging.exception` 全栈,SSE error 事件只带 `error_code / run_id / trace_id / message(脱敏)`。
- **统计端点** `GET /admin/stats`(admin):active runs、近 24h LLM failure rate、token cost(by user/story/day)、queue(等待中断数)、finalize 失败数。JSON 口径,不引外部 metrics 栈(遵守"不建议现在修改"精神)。

验收:错误事件结构断言;stats 端点权限 + 数值测试。

### 批次 7:文档与宣称修正(6.14 + 部署建议项)

改动:
- `frontend/src/Landing.jsx:80-81` 移除 IF 线宣传。
- `README.md`:"47 项测试"→ 实时口径(修完后以最终数为准);"篇幅无上限"→ 实测规模表述(60 章世界);"查询层零泄漏"→"POV 查询层 0/1345 + 上下文分级硬过滤(ADR-0024)"双口径。
- `DESIGN_FINAL.md` 补记 ADR-0021~0027。
- `AUDIT.md` 状态表更新(认证/互斥/持久化等从"未实现"移出)。
- **部署文档**(建议项 9):`docs/DEPLOY.md`——前端 build 产物由 FastAPI static 托管或 nginx 反代、HTTPS(caddy 自动证书为最短路径)、JWT SECRET 与 admin 初始账户的环境变量约定、SQLite WAL 单机部署边界(100 用户内)。

### 批次 8:端到端测试补强(汇总)

各批自带单测,本批补集成面:
- 双击 generate → 第二个 409,首个 run 不受扰动。
- 跨用户:列表/详情/审核/配置四类越权 401/403/404 矩阵。
- 重启恢复:kill -9 模拟(重建 engine)→ waiting 可 resume。
- LLM 容错:fake provider 注入坏 JSON/429/超时 → 重试/降级/台账全链路断言。
- 双 story 并发:事件归属、usage 归属、checkpoint 互不污染。
- 预算:配额耗尽 → 429 且无部分写入。

---

## 5. ADR 沉淀规划(DESIGN_FINAL.md 实现期补记)

| ADR | 主题 | 批次 |
|---|---|---|
| ADR-0021 | schema migrations 版本化 | 0 |
| ADR-0022 | 认证与多租户边界(JWT/管理员开户/story_members) | 1 |
| ADR-0023 | active run 互斥 + contextvars 事件归属 | 2 |
| ADR-0024 | ADR-0014 修订:向量兜底分级硬过滤(+pending 低置信标注) | 3 |
| ADR-0025 | 伏笔 thread_id 精确账目 | 3 |
| ADR-0026 | LLM 输出强契约与弹性(超时/重试/预算) | 4 |
| ADR-0027 | 运行状态持久化与 run_id 贯通 | 5 |

编号按实际落地顺序可调。

## 6. 风险与取舍

| 风险 | 应对 |
|---|---|
| 批次 1 认证令现有 82 测试全部 401 | conftest `auth_client` fixture 一次性改造;此为横切收口的必要成本,越晚越贵 |
| 伏笔 id 贯通链路长(评审输出 schema + 中断卡 + 前端 Codex + 落库) | 旧数据 LIKE 降级路径保留 + audit 留痕,新数据强制 id;分两步提交(后端 → 前端展示) |
| vector_hits 硬过滤伤长尾召回 | 分级设计只滤"信息差事实",无 visibility 行的背景保留;leak_eval 增设召回率对照,过滤前后各跑一次 |
| run 状态机合并边界(waiting vs running) | 单进程事实简化:启动收敛 + `_active` 佐证;多进程部署不在本轮范围(报告 §11 一致) |
| JWT 无法主动踢线 | 短有效期(2h)+ users.status 校验在每次请求(禁用即失效),不引黑名单表 |
| 每日预算查询开销 | usage_log 按 (user, day) 建索引,聚合查询每 run 一次,量级可忽略 |

## 7. 交付节奏

- 顺序:批次 0 → 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8;批次 3/4 内子项可并行。
- 每批验收:新增回归测试 + `pytest -q` 全绿 + `importlinter` 通过 + ADR 补记 + commit(功能完成即提交,不攒批)。
- 粗估实现量:批次 1/4/5 各约 1.5-2 天,批次 2/3 各约 1-1.5 天,批次 0/6/7/8 各约 0.5 天,合计 **9-12 个工作日**。
- 终验标准:报告 §7 必须项 11 条全部关闭 + §8 建议项中 run_id/错误码/摘要漏章/指令消费/client 复用/migrations/越权测试 7 条关闭;重跑三方同口径检查,`pytest`/`importlinter` 全绿。
