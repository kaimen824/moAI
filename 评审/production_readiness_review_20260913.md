# 墨澜 MoLan 生产落地评审报告

## 1. 报告元信息

| 项 | 值 |
|---|---|
| 报告生成时间 | 2026-09-13 21:27:55 +08:00 |
| 时区 | Asia/Shanghai |
| 项目路径 | `E:\study\tample_project\text_generate` |
| Git 分支 | `main` |
| Git HEAD | `b5e35b34a204d9c2c8a94217a25e9f0ece4b3382` |
| HEAD 提交 | `feat: LangGraph Studio 验证环境(备份库隔离)+ graph_canvas 图可视化子项目` |
| 采集 Git 状态时 | 尚未创建本报告文件 |
| 当时 Git 状态 | `0 modified / 0 staged / 0 deleted / 1 untracked` |
| 当时未跟踪文件 | `_acl_report.txt` |
| 报告文件 | `评审/production_readiness_review_20260913.md` |
| 报告文件生成时间 | 2026-09-13 21:32:29 +08:00 |
| 评审环境 | `D:\Anaconda\envs\novel-agent\python.exe` |
| Python 版本 | Python 3.12.14 |
| 总分 | 52 / 100 |
| 结论 | ❌ 不具备生产条件，适合继续内部开发 / 单人本机使用 |

## 2. 本次实际验证

在 `novel-agent` 环境中执行：

```bash
D:/Anaconda/envs/novel-agent/python.exe -m pytest -q
D:/Anaconda/envs/novel-agent/python.exe -m importlinter.cli lint-imports
```

结果：

| 检查 | 结果 |
|---|---|
| pytest | 通过，82 个测试全部通过 |
| import-linter | 通过，退出码 0 |
| 警告 | 1 个 `starlette.testclient` / `anyio.BlockingPortal` deprecation warning |

测试通过说明现有回放、权限、实体、检索、图流程在测试范围内稳定；但不能抵消下述生产边界问题。

---

# 3. 真实架构

```text
React / Vite dev proxy
→ FastAPI app/main.py
→ engine() 单例 Deps + LangGraph
→ LangGraph nodes / Agents
→ Repository / RetrievalService / EntityService
→ SQLite WAL
→ LLMFacade → DashScope / GLM / OpenAI-compatible
→ JSON 或 SSE 返回前端
```

异步生命周期：

```text
POST /stories/{id}/generate
→ _sse_run() 启动 daemon worker
→ LangGraph.stream()
→ interrupt：发出中断事件，worker 返回，checkpoint 保留
→ resume：Command(resume=...) 再次启动 worker
→ 章节确认后：抽取、角色更新、实体消歧、摘要并行
→ commit_finalize() 单事务落库
→ route_next() 判断是否继续下一章
```

数据归属：

| 数据 | 定位 |
|---|---|
| `stories / chapters / facts / beliefs / plot_threads / entities / summaries` | 主要 Source of Truth |
| `GraphState` + LangGraph checkpoint | 工作流运行状态 |
| `Deps._history`、SSE queue、`_active` | 进程内 UI/事件状态，不是事实源 |
| LLM 输出 | 大多在 `finalize` 前只是暂存；总大纲和角色/实体种子有单独确认落库点 |

---

# 4. 评分

| 维度 | 分数 | 说明 |
|---|---:|---|
| 业务正确性 | 58 | 正常流程闭环，但事实审核、伏笔匹配、阶段摘要有正确性问题 |
| 架构 | 70 | 分层和 Agent 设计有价值，但 runtime 过重，文档与代码有偏差 |
| 可靠性 | 45 | 有 checkpoint 和原子定稿，但并发、恢复、LLM 失败处理不足 |
| 数据一致性 | 55 | 定稿事务好；存在重复写入、部分提交、弱匹配更新风险 |
| 安全性 | 15 | 无用户体系、无授权、无租户隔离 |
| 可扩展性 | 30 | 单进程状态、单 SQLite 连接、进程内 SSE 总线限制明显 |
| 可维护性 | 68 | 测试和模块边界较好，但关键文件职责偏重 |
| 测试 | 72 | 82 个测试全绿；缺并发、安全、重启、真实 LLM 容错测试 |
| 可观测性 | 55 | usage / trace / audit 有基础；缺 request/run id、metrics、错误栈 |

**总分：52 / 100。**

---

# 5. Top 10 问题

1. **P0：API 无认证、无授权、无租户隔离。**
2. **P1：同一 story 可重复 generate / resume，可能重复章节。**
3. **P1：多 story 并发时 `_current_thread` / SSE 事件归属不可靠。**
4. **P1：pending / rejected facts 仍进入 POV 记忆。**
5. **P1：服务重启后中断恢复状态不持久。**
6. **P1：伏笔变更用 description 模糊匹配，可能改错台账。**
7. **P1：总大纲确认与角色 / 实体种子落库不原子。**
8. **P1：LLM JSON 无 schema 校验和重试。**
9. **P1：vector_hits 让隐藏事实进入 writer 上下文，POV 隔离主要靠 prompt。**
10. **P2：LLM 超时 / 重试 / 预算 /规模限制缺失。**

---

# 6. 问题详情

## 6.1 [P0] API 无认证、无授权、无租户隔离

**位置：**

- `app/main.py` 全部路由。
- 典型位置：`GET /stories` 约 195 行；`GET /facts/pending` 342 行；`POST /facts/{fact_id}/review` 352 行；`GET /entities/pending` 368 行；`POST /config/models` 439 行。

**问题：**

任何能访问服务的人都可以列出全部故事、读取任意章节、审核任意事实、合并任意实体、修改全局模型配置。`Repository` 内的 Agent ACL 只约束 Agent 对数据域的访问，不约束 HTTP 用户之间的数据访问。

**后果：**

1. 用户 A 可读用户 B 的小说、设定、伏笔、trace、usage。
2. 用户 A 可审核用户 B 的 pending fact。
3. 用户 A 可合并用户 B 的实体。
4. 用户 A 可影响全局模型配置。

**修复：**

1. 引入用户认证。
2. 增加 `owner_id` 或 `story_members(story_id, user_id, role)`。
3. 所有 `/stories/{story_id}/...` 校验访问权。
4. 审核队列只返回当前用户有权审核的数据。
5. 模型配置改为用户级或管理员专用。

**是否必须修改：** 必须。

---

## 6.2 [P1] 同一 story 可重复运行

**位置：**

- `app/main.py:40` `_active`
- `app/main.py:109` `_sse_run()`
- `app/main.py:233` `generate()`
- `app/main.py:256` `resume()`
- `app/graph/runtime.py:542` `commit_finalize()`

**问题：**

`_sse_run()` 启动 worker 前没有检查 `thread_id` 是否已 active。同一 `story_id` 可并发进入多个 LangGraph run。

**后果：**

1. 同一 `chapter_no` 可能出现多条 active chapter。
2. checkpoint 可能被另一个 run 覆盖。
3. SSE 事件可能交错或误归属。

**修复：**

```python
if thread_id in _active:
    raise HTTPException(409, "story run already active")

_active.add(thread_id)
```

并增加数据库约束：

```sql
CREATE UNIQUE INDEX idx_chapters_active
ON chapters(story_id, chapter_no)
WHERE status = 'active';
```

**是否必须修改：** 必须。

---

## 6.3 [P1] 多 story 并发时事件归属不可靠

**位置：**

- `app/graph/runtime.py:55` `_current_thread`
- `app/graph/runtime.py:75` `emit()`
- `app/main.py:116` `deps._current_thread = thread_id`

**问题：**

全局 `_current_thread` 只能表示一个当前运行 story。两个不同 story 同时生成时，后启动者会覆盖前者，token 和事件可能发错 thread。

**修复：**

`emit()` 强制携带 `thread_id`，或引入 per-run context：

```text
RunContext(thread_id, run_id, event_bus)
```

**是否必须修改：** 必须。

---

## 6.4 [P1] 重启后中断恢复不可靠

**位置：**

- `app/main.py:40` `_active`
- `app/main.py:162` `run_state()`
- `app/graph/runtime.py:53` `_history`

**问题：**

LangGraph checkpoint 持久化了流程，但 API 用进程内 `_active` 和 `_history` 推导 `running / waiting / idle`。服务重启后这些状态消失，前端可能把待恢复中断显示为 idle。

**修复：**

新增持久化 `runs` 或 `story_run_state` 表，保存 `run_id / story_id / status / interrupt_type / interrupt_payload / checkpoint_id / updated_at`。`run_state()` 从 DB 和 checkpoint 恢复。

**是否必须修改：** 必须。

---

## 6.5 [P1] pending / rejected facts 污染 POV 记忆

**位置：**

- `app/memory/world.py:88` `get_pov_memory()`
- `app/memory/world.py:92-101` SQL 未过滤 `f.status`
- `app/graph/runtime.py:600` 低置信 fact 写入 `pending_review`
- `app/main.py:352` 人工审核后改为 `confirmed/rejected`

**问题：**

POV 客观事实查询没有过滤 `f.status`，因此未审核和已拒绝事实都可能进入后续上下文。

**后果：**

1. 未审核事实影响下一章。
2. 已拒绝事实继续污染长期记忆。
3. 人工审核失去权威性。

**修复：**

至少排除 `rejected`；生产上建议只允许 `confirmed`。若允许 pending fact 作为低置信线索，必须在 prompt 中显式标注。

**是否必须修改：** 必须。

---

## 6.6 [P1] vector_hits 削弱 POV 隔离

**位置：**

- `app/memory/retrieval.py:118-134` `_vector_fallback()`
- `app/graph/agents/writer.py` `render_context()`

**问题：**

向量兜底返回客观世界事实，不按 `fact_visibility` 过滤。writer prompt 虽然禁止角色引用不该知晓的信息，但这是自然语言约束，不是硬访问控制。

**后果：**

1. 可能出现主角全知。
2. 悬念可能被提前揭穿。
3. “查询层零泄漏”不能等价于“writer 上下文零泄漏”。

**修复：**

若 POV 是硬约束，vector fallback 必须按可见性过滤；若只是叙事者素材，必须与角色可知信息明确分离，并增加输出后检测。

**是否必须修改：** 对外宣称 POV 隔离时必须。

---

## 6.7 [P1] 伏笔按 description 模糊匹配

**位置：**

- `app/graph/runtime.py:655-672` advance / resolve / drop
- `app/graph/runtime.py:674-684` escalate

**问题：**

伏笔变更没有稳定 `thread_id`，落库时用 description 前 12 个字符做 `LIKE` 匹配。描述相似、LLM 改写或描述过短时，可能更新错误伏笔或静默丢失。

**修复：**

在评审输入、LLM 输出、中断卡、用户确认、`commit_finalize()` 全链路使用 `thread_id`；description 只用于展示。

**是否必须修改：** 必须。

---

## 6.8 [P1] 总大纲与角色/实体种子落库不原子

**位置：**

- `app/graph/build.py:96-129` `confirm_master_outline()`
- `app/graph/build.py:110-123` 先提交 outline
- `app/graph/build.py:127` 再调用 `persist_characters()`
- `app/graph/runtime.py:383-465`

**问题：**

outline 先提交，characters / entities / aliases / links 后续多段提交。中途失败会出现半提交状态，重试可能造成角色和实体重复。

**修复：**

将 outline、characters、entities、aliases、links 放入同一显式事务，统一 commit / rollback；并增加业务唯一约束或确定性 upsert。

**是否必须修改：** 必须。

---

## 6.9 [P1] LLM JSON 缺 schema 校验和重试

**位置：**

- `app/graph/agents/base.py:35` `ask_json()`
- `app/graph/agents/base.py:52` `parse_json_loose()`

**问题：**

当前只做宽松 JSON 提取和 `json.loads()`，没有 schema、字段类型、枚举校验、有限重试和结构化失败记录。模型返回空值、截断 JSON、错误字段或错误枚举时，节点会直接失败。

**修复：**

1. 每个 JSON stage 定义 Pydantic schema。
2. 解析或校验失败自动重试一次。
3. 仍失败写入 `llm_failures`，SSE error 带 trace_id。
4. 评审输出无效时安全默认为 `revise`，不允许静默 pass。

**是否必须修改：** 必须。

---

## 6.10 [P2] LLM 缺超时、重试、限流和预算

**位置：**

- `app/core/llm/facade.py:85` `chat()`
- `app/core/llm/facade.py:150` `stream()`
- `app/core/llm/providers/openai_compat.py`
- `app/main.py:25` `GenerateRequest.target_chapters`

**问题：**

没有统一 timeout、429/5xx 重试、user/story/day 预算、`target_chapters` 上限。偶发慢响应或 provider 异常会直接中断流程，成本不可控。

**修复：**

1. provider client 复用并设置 timeout。
2. 对可重试错误做有限指数退避。
3. 限制单次章节数。
4. 增加用户、故事、每日预算。
5. SSE error 返回稳定错误码和 trace_id。

**是否必须修改：** 内部测试建议；公网生产必须。

---

## 6.11 [P2] 阶段摘要漏掉阶段末章

**位置：**

- `app/graph/agents/supervisor.py:161` `SummaryNode`
- `app/graph/agents/supervisor.py:185`
- `app/graph/runtime.py:157`

**问题：**

阶段末章生成 stage summary 时，当前章还未 `commit_finalize()`，DB 查询拿不到当前章摘要，导致阶段摘要漏掉最后一章。

**修复：**

将内存中的当前 `chapter_summary` 显式加入聚合输入。

**是否必须修改：** 长篇上线前必须。

---

## 6.12 [P2] 用户指令过早标记消费

**位置：**

- `app/graph/build.py:196`
- `app/graph/runtime.py:120-135`

**问题：**

指令在 `build_context` 立即标记 consumed。如果随后失败、中断或重新生成，指令可能没有真正进入定稿。

**修复：**

延迟到定稿成功后标记，或增加 `consumed_by_chapter_id`。

**是否必须修改：** 建议。

---

## 6.13 [P2] 可观测性不足以快速定位线上问题

已有 `usage_log`、`agent_traces`、`review_results`、`retrieval_audit`，但缺少：

1. request id。
2. run id。
3. user id。
4. chapter/run 关联。
5. error stack。
6. provider status code。
7. retry count。
8. active run / queue metrics。
9. structured logs。

**修复：**

每次 generate / resume 生成 `run_id`，贯通日志、SSE、usage、trace、review 和 finalize；错误记录 stack、stage、node、trace_id，不直接暴露原始异常。

**是否必须修改：** 公网生产必须。

---

## 6.14 [P2] 文档和落地页超前宣称

**位置：**

- `frontend/src/Landing.jsx:80-81` 宣传 IF 线
- `DESIGN_FINAL.md:209-217`
- `AUDIT.md` 明确 IF 线、章节版本化未实现
- `README.md:3` “篇幅无上限”
- `README.md:12` “查询层零泄漏”
- `README.md:50` “47 项测试”，当前为 82 项

**修复：**

1. Landing 移除 IF 线。
2. README 更新测试数和 Agent 数。
3. “篇幅无上限”改为当前实测规模。
4. “零泄漏”限定为 POV 查询层测试口径。

**是否必须修改：** 对外发布前必须。

---

## 6.15 [P2] 数据库迁移缺版本管理

**位置：**

- `app/db/ddl.py` `SCHEMA_SQL`
- `app/db/ddl.py:266-288`

**问题：**

当前依赖 `CREATE TABLE IF NOT EXISTS` 和少量 `ALTER TABLE`，没有 `schema_migrations`、版本号、迁移历史和失败状态。

**修复：**

增加 `schema_migrations(version, applied_at)`，按版本执行并记录。

**是否必须修改：** 多环境生产前必须。

---

# 7. 必须修改项

1. 用户认证 + story 所有权 / 协作者授权。
2. 全局审核和配置端点租户化。
3. 同一 story 的 active run 互斥。
4. active chapter 唯一约束。
5. story run / interrupt 状态持久化。
6. POV 查询排除 rejected，明确 pending 是否可用。
7. 伏笔变更改为 `thread_id` 精确更新。
8. 总大纲确认 + 角色实体落库改为单事务。
9. LLM JSON schema 校验、有限重试、结构化失败记录。
10. `target_chapters` 上限和基础成本保护。
11. 对外文档移除未实现能力宣称。

---

# 8. 建议修改项

1. 引入 `run_id`，贯通日志、SSE、trace、usage。
2. worker 错误返回稳定错误码和 trace id，服务端记录堆栈。
3. 修复阶段末章 stage summary 漏当前章。
4. 用户指令延迟到定稿成功后标记消费。
5. LLM provider client 复用，增加 timeout / retry。
6. 引入 schema migrations 版本表。
7. 降低 `runtime.py` 和 `main.py` 职责密度。
8. 增加并发、重启恢复、双击 generate、跨用户越权测试。
9. 补充前端生产部署、反向代理和 HTTPS 配置。
10. 增加 active runs、LLM failure rate、token cost、queue length、finalize failure 指标。

---

# 9. 不建议现在修改项

1. **不要为了“生产感”引入 Kafka / RabbitMQ。**  
   当前优先问题是认证、互斥和状态持久化。

2. **不要立刻把 SQLite 换 PostgreSQL。**  
   SQLite WAL + 单事务定稿对当前规模合理；确要多进程部署再换。

3. **不要拆散 `commit_finalize()` 单事务。**  
   方向正确，应文档化并最小化直写 SQL 豁免。

4. **不要现在实现 IF 线和章节版本化回退。**  
   `AUDIT.md` 已正确定位为后续演进项。

5. **不要引入 MCP、复杂插件系统、Event Sourcing 或 CQRS。**  
   当前瓶颈不是抽象不足，而是身份边界和运行状态。

6. **不要继续加深 provider 抽象。**  
   优先补 timeout、retry、observability。

---

# 10. 架构债务

1. **`runtime.py` 正在成为 God Object。**  
   同时负责事件总线、stop、context 构建、实体持久化、style blacklist、review log、entity proposal、事务定稿和 engine 装配。

2. **运行状态双轨。**  
   LangGraph checkpoint 是流程状态，`_active / _history` 是 UI 状态，但二者没有一致状态机。

3. **LLM 输出是弱契约。**  
   大量业务对象依赖 prompt 和 loose JSON parser。

4. **身份匹配过度依赖文本。**  
   角色靠名字子串，实体靠名字 / 别名，伏笔靠 description。

5. **DDL 全集先行，功能子集落地。**  
   已造成文档与实现偏差。

6. **Repository ACL 与编排层直写 SQL 的边界豁免缺正式规范。**

---

# 11. 规模增长分析

## 10 用户

问题：

1. 并发生成时事件归属可能错乱。
2. 同一 story 可能重复运行。
3. 无认证问题不可接受。

结论：修复认证和 active run 互斥后，10 人内测可以尝试。

## 100 用户

瓶颈：

1. 单进程 LLM 并发和 provider 配额。
2. SQLite 写锁和单连接争用。
3. 进程内事件总线无法横向扩展。
4. 全局配置和审核队列失去管理语义。
5. 长任务缺队列、优先级、取消和恢复。

结论：不建议 100 用户公开使用。

## 1000 用户

当前架构会系统性失效，需要独立任务队列、多 worker、集中 run 状态、多租户隔离、配额和分布式可观测性。

## 10000 用户

需要完整生产平台能力：job queue / worker pool、多租户数据库、对象存储、分布式 trace、metrics、告警、LLM gateway、配额、降级路由、幂等任务、SLA 与故障恢复。

---

# 12. 最终结论

如果这是生产负责人视角，**不愿意让当前形态上线**。

当前项目有较好的原型质量：正常流程完整、82 个测试通过、定稿事务明确、记忆系统和 Agent 分工有真实设计。但生产暴露前有三个硬阻断：

1. **没有认证、授权和租户隔离。**
2. **同一 story 可重复运行，可能产生重复章节和不可信状态。**
3. **中断 / 运行状态依赖进程内存，服务重启后流程断头。**

最低复杂度的正确路径不是引入 Kafka、Kubernetes 或微服务，而是先完成：

```text
用户/租户边界
→ active run 互斥
→ run 状态持久化
→ fact status 过滤
→ 伏笔 ID 匹配
→ LLM schema 校验
```

主链稳定后，再考虑横向扩展。

---

## 附录 A：Git 状态

评审元信息采集时（本报告尚未写入）：

```text
$ git status --porcelain=v1
?? _acl_report.txt
```

本报告写入后：

```text
$ git status --porcelain=v1
?? _acl_report.txt
?? "评审/"
```

报告文件：

```text
E:\study\tample_project\text_generate\评审\production_readiness_review_20260913.md
Size: 18833 bytes
LastWriteTime: 2026-09-13 21:32:29 +08:00
```

## 附录 B：验证命令

```powershell
D:\Anaconda\envs\novel-agent\python.exe -m pytest -q
D:\Anaconda\envs\novel-agent\python.exe -m importlinter.cli lint-imports
```

结果：

```text
pytest: 82 passed
import-linter: pass
```
