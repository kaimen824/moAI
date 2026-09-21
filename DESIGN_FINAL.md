# 小说生成 Agent — 最终设计文档(v1.1)

> 本文档是**最终定稿设计**,自包含、以"系统是什么"呈现,作为实现的直接依据。
> 决策过程、被否方案与完整论证见 `PROJECT_DESIGN.md`(过程文档);面试叙事见 `RESUME_HIGHLIGHTS.md`。
> 定稿日期:2026-09-06 · v1.1 修订(外部评审响应):facts/beliefs 分表、抽取置信度分层、P2.5 记忆层评测前置、插件契约轻量化——**设计冻结,进入实现**

---

## 1. 项目概述

**定位**:基于 LangGraph 的多 Agent 长篇小说生成系统,面向一线大厂 LLM 应用 / Agent 开发岗的简历项目。

**核心挑战**:

| 挑战 | 解法 |
|---|---|
| 篇幅无上限,上下文装不下 | 结构化事实库 + 检索式回忆,检索精度不随规模衰减 |
| 长篇逻辑闭环(设定/伏笔/因果一致) | 全局事实层 + 版本链回放 + 双评审 + 伏笔人工复核 |
| 角色视角正确(不出现"全知泄漏") | POV 视图投影:权限 = 查询时过滤 |
| 生成质量不稳定 | 结构化裁决 + 审校-重写自动闭环 + human-in-the-loop |
| 成本控制 | 按 Agent 模型分级路由 + 全链路用量埋点 |

**技术栈**:Python · LangGraph · SQLite(WAL) · FastAPI + SSE · React

---

## 2. 系统架构

### 2.1 分层与依赖(单向,机械守护)

```
api(FastAPI + SSE)
   ↓
graph(LangGraph 编排:6 Agent + 生产循环)
   ↓
memory(仓储层 + 检索服务)→ db(SQLite)
   ↘ core/llm(模型接入层:抽象工厂 + 策略路由 + 埋点;最底层,被所有层可用)
```

- 依赖只准自上而下,禁止反向与跨层;**import-linter 契约测试进 CI**,违例即测试失败
- 引擎(LangGraph 图)与 API 层解耦:图不感知 FastAPI,经 thread_id/checkpoint 交互,CLI 与 Web 复用同一引擎

### 2.2 Agent 拓扑

LangGraph supervisor 进程内编排,子 Agent 为图中节点,经共享 State 通信(不引入 A2A——单机场景互操作收益为零,且破坏 checkpoint/中断恢复的图完整性)。

| Agent / 服务 | 职责 | 权限(表级 + story 隔离) |
|---|---|---|
| 主控 supervisor | 用户交互(共创/大纲确认/审阅)、任务派发、大纲写入、章节定稿 | 全域读写 |
| 大纲 Agent | 大纲一致性评审(必检,独立 critic,不参与生产) | 只读 |
| 写作 Agent | 章节正文生成 | 只读 + POV 强制过滤 |
| 审校 Agent | 质量评审(一致性/伏笔/文风)+ 伏笔维护 | plot_threads 写,其余只读 |
| 事件管理 Agent | facts 与 beliefs 写入(单写者)+ 时间线偏序维护 | facts / beliefs / temporal_relations 写 |
| 角色管理 Agent | 角色卡与实体链接图维护 | characters / entities 写 |
| 记忆检索服务 | **确定性代码,非 LLM**:POV 过滤 + 混合检索 | 只读,fail-closed |

**不变式**:每张表写者唯一(facts→事件管理,plot_threads→审校,characters→角色管理);大纲 Agent 只读(自己评自己改的裁判员问题)。

### 2.3 模块结构

```
app/
├── main.py / api/routes/      # FastAPI、SSE、中断点交互端点
├── core/config.py             # 配置优先级:前端配置页 > 环境变量 > 默认值
├── core/llm/                  # base / providers / factory / router / observed
├── graph/state.py, build.py   # 图状态(工作记忆)与图装配
├── graph/agents/              # 6 Agent(BaseAgent 契约 + 注册表)
├── memory/schemas.py          # 全表数据模型
├── memory/repository.py       # 仓储层:ACL + story_id 强制注入
├── memory/retrieval.py        # 检索服务
├── db/                        # SQLite 初始化与迁移
└── observability/             # usage_log / retrieval_audit
```

---

## 3. 记忆系统

### 3.1 三级记忆

| 级 | 载体 | 内容 |
|---|---|---|
| **长期记忆** | facts + beliefs(及关联表) | 权威事实库(客观,置信度分层,版本链)+ 角色认知库(含误信,演化链);POV 投影 |
| **短期记忆** | 查询组装(不独立存储) | 最近一章状态变化 + 结尾原文(解决纯摘要衔接失真);记忆节点 = 章节定稿点 |
| **工作记忆** | LangGraph 图状态 | 当前细纲、草稿、审校反馈等流程态;**草稿/反馈不落库,定稿沉淀才落库** |

- 上下文构建 = 长期 + 短期共同拼装;全书大纲常驻
- 章节原文全量入库;窗口外历史细节靠检索式回忆(章/卷摘要索引定位 → 原文段落下钻),不做摘要晋升

### 3.2 数据模型(SQLite,WAL,15 张表)

```
facts               客观事实层(纯上帝视角;版本链;置信度分层)
  id, story_id, type(event|state|setting|relation), content,
  chapter_established, branch_id,
  prev_version_id            版本链:指向被推翻/修正的上一版(NULL=首次确立)
  confidence(high | low)     抽取置信度:显式陈述=high;隐含推断=low
  status(confirmed | pending_review)  low → pending_review 进人工抽检队列
  embedding(BLOB), created_at

beliefs             角色认知层(与客观事实分离——生命周期/来源/冲突逻辑均不同)
  id, story_id, character_id, content,
  source_fact_id(可空,关联的客观事实;误信=belief 与 fact 冲突),
  status(believed | dispelled),
  established_chapter, dispelled_chapter, branch_id,
  prev_version_id            认知演化链(误信A → 误信B → 得知真相)
  embedding(BLOB), created_at

fact_visibility     可见性矩阵(角色 × 客观事实;缺省即未知,不落 unknown 行)
  fact_id, character_id,
  knowledge_level(known_full | known_partial),
  detail(自由文本:known_partial 时记录"知晓的部分"),
  learned_chapter, branch_id

characters          角色卡(身份/外貌/性格/目标,含 wiki 条目内容)
chapters            章节元数据与版本链(原文全量 + 段落级 embedding;
                    version_no / prev_version_id / status: active|stale|archived)
chapter_summaries   分层摘要(layer: chapter|volume|book)——检索索引用
plot_threads        伏笔(埋设章/回收章/状态: open|resolved|dropped)
temporal_relations  事件时间偏序(event_a, event_b, before|after|during|parallel)
entities + entity_links   Wiki 式实体条目与显式链接图
branches            分支簿记(kind: main|if_line;status: active|archived)
agent_acl           Agent 权限矩阵(agent_name, data_domain, can_read, can_write)
review_results      评审记录(章节/轮次/评审者/维度分/裁决/反馈/forced_pass)
usage_log           LLM 调用埋点(agent/model/tokens/latency/trace_id)
retrieval_audit     检索审计(caller/query/返回条数/耗时)
```

### 3.3 核心机制

**POV 视图投影**(角色视角 = 查询,零副本):
```
角色长期记忆 =
  客观认知: facts ⋈ fact_visibility (character_id=?, 无后续版本)   [partial 附 detail]
  ∪ 主观认知: beliefs (character_id=?, status=believed, 沿认知演化链取最新)
```

**fact / belief 分表**:世界事实("张三死亡")与角色认知("李四以为张三死了")分离建模——生命周期不同(世界变了走 facts 版本链;认知变了走 beliefs 演化链 + dispelled)、冲突处理不同(误信 = belief 与 fact 冲突,审校以事实层校验误解是有意设计还是抽取 bug)。认知演化链:误信A → 误信B → 得知真相,均为 append + dispelled 标记,可回放角色认知史。

**抽取置信度分层**:facts 带 `confidence(high|low)` 与 `status(confirmed|pending_review)`——显式陈述自动入库;隐含推断标 pending 进人工抽检队列,控制 LLM 抽错的"静默错误"入口。

**版本链(事件溯源)**:事实推翻 = 追加新版本行,append-only;世界状态回放 = 按 (chapter, branch) 沿链取每条事实最新有效版本,主线按各章**活跃版本**取 facts。

**两层 checkpoint**:

| 层 | 职责 | 实现 |
|---|---|---|
| 流程 checkpoint | 图状态、human-in-the-loop 中断点 | LangGraph checkpointer(SQLite) |
| 世界 checkpoint | 某章某分支的世界快照 | facts 版本链按 (chapter, branch) 回放 |

### 3.4 检索服务(确定性代码,fail-closed)

**调用契约**:`retrieve(query, caller_agent, pov_character=None, ...)`——调用者身份必传,无身份直接拒绝;仓储层私有,Agent 无旁路。内部管线:agent_acl 查域 → 强制 story_id 过滤(多租户隔离)→ POV 过滤 → 检索链路。每次调用落审计日志。

**混合检索三级**(篇幅增长下精度不衰减):
1. **结构化查表**(主路):当前场景在场角色 → 角色卡 + 可见事实 + 活跃伏笔,不随规模衰减
2. **链接扩展一跳**:沿 entities 链接图扩展相关条目
3. **向量兜底**:embedding(SQLite blob + 内存暴力余弦,毫秒级,零外部向量库)召回长尾

**分层摘要索引**(RAPTOR 式):全书大纲常驻语境;检索时先搜章/卷摘要定位章节,再下钻原文段落——避免全量段落检索的噪声淹没。

### 3.5 读写链路

**读**(生成第 N 章):主控确定在场角色 + 活跃伏笔 → 检索服务组装(常驻大纲 + POV 事实/认知 + 角色卡 + 衔接原文 + 检索结果)→ 交给写作 Agent。

**写**(章节定稿,**编排原子性**——LLM 调用不进数据库事务):
1. 定稿管道:事件管理抽取事实(对照回放世界状态做冲突检测,冲突标记不静默写入)→ 角色管理消费事实变更集更新角色卡 → 审校落伏笔 → 章摘要生成 → 全部产出**暂存变更集**(存图状态)
2. **DB 事务只包纯写**(秒级):全部表批量落库,一次提交
3. 任一步失败 → 暂存丢弃,世界状态零污染

---

## 4. 业务流程

### 4.1 初始共创(从零到第一章)

```
新建小说
  ① 主控共创式访谈:多轮对话收集世界观/基调/核心冲突/角色构想
  ② 角色管理 Agent 初始化角色卡(characters + entities)
  ③ 主控产出总大纲(含卷/阶段结构)
  ④ 大纲 Agent 评审总大纲(结构化裁决)
  ⑤ [中断点 0] 用户确认(可提意见回流 ③)
  → 进入阶段生产循环
```
访谈产出写入 facts(type=setting)与 entities,享受版本链/检索/POV 同等待遇;总大纲确认后常驻上下文,修改 = 主控写入(带审计)。

### 4.2 章节生产循环

```
① 细纲环节
   阶段首章:a. 主控生成阶段细纲 → b. [中断点 A] 大纲 Agent 评审 + 用户确认
   非首章:  c. 主控从阶段细纲切片派生本章要点(无评审、不打断用户)
③ 检索服务组装上下文
④ 写作 Agent 初稿(SSE 流式)
⑤ 双评审(fan-out 并行):大纲 Agent(一致性)+ 审校 Agent(质量/伏笔)
   ├─ 任一 revise/block → 合并反馈一次重写(block 优先,反馈按维度分列)
   │   重写上限 3 次(可配置),达上限 forced_pass 带警告交用户
   └─ 双 pass ↓
⑥ [中断点 B] 用户审阅:提改写意见回流 ④;或确认定稿
   ↳ 并入同一界面:伏笔变更清单人工二次确认 + 事实冲突裁决
⑦ 定稿管道(编排原子性,见 3.5)
```

- **细纲粒度**:阶段性(卷/剧情单元)过用户,不逐章;阶段边界由总大纲规划
- **评审并行**:LangGraph fan-out/fan-in,汇聚节点合并两份结构化裁决
- **伏笔人工复核**:审校的伏笔裁决(不论通过与否)必须人工确认;审校-重写中间轮的伏笔变更只存图状态,最终轮才落库

### 4.3 章节改写(版本化,可回退)

> 实现状态(P7+ 演进项,见 AUDIT.md):**未实现**——定稿前重写已由中断点
> B 循环覆盖;定稿后版本化改写未排期。

- 定稿前:中断点 B 循环内提意见重写
- 定稿后:改写第 N 章 = 第 N 章 v2(v1 归档不删除),N+1..M 章自动标 `stale` 进入连锁重生成队列,用户批量触发;**可随时回退**(v2 归档、v1 恢复、stale 解除;若已重生成需确认丢弃)
- **主线不变式:单一活跃版本链**——任意时刻每章恰有一个活跃版本,不是平行分叉

### 4.4 IF 线(番外)

> 实现状态(P7+ 演进项,见 AUDIT.md):**未实现**——branches 表结构预留,
> 无 fork API/图路径/前端;落地页不得宣传。

定稿后可从任意章节 checkpoint fork 出 IF 线(轻小说 IF 模式),branch_id 区分,共享 facts 库、无数据复制;**永不合并回主线**。正文走向由用户在阶段细纲确认时掌控,正文内不设分支点。

---

## 5. 质量保障与可观测性

| 机制 | 设计 |
|---|---|
| 结构化裁决 | `pass / revise_with_feedback / block` + 维度评分,可接自动回流;评审维度 rubric 配置化 |
| 双评审闭环 | 大纲一致性 + 质量审校并行,合并反馈一次重写,上限可配,forced_pass 显式降级不静默 |
| 伏笔双保险 | 审校建议 + 人工确认(不可逆高风险操作不接受全自动) |
| 冲突检测 | 事件抽取时对照世界状态自查,冲突交用户裁决;有意推翻(剧情反转)走版本链并标记 |
| 抽取置信度 | high 自动入库 / low 进 pending 抽检队列——LLM 抽错的静默入口受控 |
| 评分落库 | review_results:重写次数分布、维度评分、分级路由前后质量对比 |
| 用量埋点 | usage_log:全部 LLM 调用经 core/llm 唯一出口,装饰器一处埋点;LangSmith 留开关非必需 |
| 检索审计 | retrieval_audit:caller/query/返回条数/耗时 |

零外部可观测性依赖(SQLite + 查询脚本);外部化(OTel 等)为长期演进项。

---

## 6. 模型分级与接入层

| 环节 | 档位 | 当前配置(阿里云百炼聚合) |
|---|---|---|
| 主控(细纲/裁决)、大纲 Agent、审校 Agent | 强模型 | glm-5 |
| 写作 Agent | 强模型(默认,可配置降档做成本实验) | glm-5 |
| 事件管理(事实抽取) | 中档 | deepseek-v3 |
| 角色管理、摘要生成 | 便宜模型 | deepseek-v3 |
| embedding | 独立配置 | qwen3.7-text-embedding |

**Provider 路由:key 可用性优先**——配了 `DASHSCOPE_API_KEY`(百炼)时一切模型(含 glm-5)走百炼聚合;无百炼 key 时 glm-* 才直连智谱。

- 配置粒度 = 按 Agent:`MODEL__<AGENT_ROLE>` 环境变量;优先级 前端配置页 > 环境变量 > 默认值
- **接入层三件套**:抽象工厂(provider 客户端族:chat + embedding)+ 策略路由(角色→模型)+ 装饰器(usage 埋点);Agent 唯一入口 `llm.chat(role=..., messages=...)`
- 分级策略本身是可实验对象:量化"全强 vs 分级"成本/质量曲线

---

## 7. 可插拔架构

| 机制 | 设计 |
|---|---|
| 依赖规则 | 单向分层,import-linter 进 CI,机械守护 |
| Agent 注册表 | 新 Agent = 实现 BaseAgent 契约(role/权限/输出 schema)+ 注册 + ACL 加行,**零改主控** |
| 定稿管道化 | ⑦ = 显式管道,新功能注册新步骤,不动其他步骤 |
| 评审维度配置化 | rubric 声明式,加维度不改代码 |
| 插件契约(轻量) | BaseAgent 接口约束 + 权限声明(fail-closed 不豁免)+ 输出 schema 校验,以**实现模式**落地;待出现第二个真实扩展场景再升格为契约测试设施 |
| **核心不变式** | 定稿编排原子性 / 每表单写者 / fail-closed / 单一活跃版本链——设计原则层面不可违背 |

不引入事件总线/消息中间件(破坏单机可调试性与图的显式性);插拔 = 注册表 + 显式管道。

---

## 8. 关键 Trade-off 速查(面试向)

| 决策 | 取 | 舍 | 理由 |
|---|---|---|---|
| Agent 通信 | LangGraph 进程内共享 State | A2A 协议 | 单机互操作收益为零;A2A 破坏 checkpoint 图完整性,与交互需求冲突 |
| 记忆检索 | 确定性服务 | LLM 记忆 Agent | 检索全链路为确定性逻辑,不在最需正确性的环节注入不确定性 |
| 角色记忆 | 事实库 + POV 查询过滤 | 每角色独立记忆流 | SSOT/RLS 实践;独立副本漂移且矛盾无裁决基准 |
| 事实 vs 认知 | facts / beliefs 分表 | 单表混装(perspective 字段) | 生命周期/冲突处理/被引用关系均不同;混表致查询分叉、稀疏字段、校验分叉 |
| 抽取入口 | 置信度分层 + 人工抽检 | 全自动信任 LLM 抽取 | LLM 抽取不可避免,但静默错误必须受控 |
| 全局概览 | 全书大纲常驻 | 纯检索拼装 | 全局信息无具体位置可搜,检索拼不出(Re3/DOC/RAPTOR 均用递归摘要) |
| 摘要层 | 保留,但只做检索索引 | 作为上下文材料 | 中观细节已由 facts+检索覆盖;摘要层降为索引提升检索精度 |
| 定稿写入 | 编排原子性(暂存变更集 + 秒级 DB 事务) | 单个大事务 | LLM 调用不可进事务;长事务锁库 |
| 向量存储 | SQLite blob + 内存暴力余弦 | 外部向量库 | 千级条目毫秒级,零部署负担 |
| 主线分支 | 单一活跃版本链 + IF 线隔离 | 正文内多分支并行 | 不变式简单可回放;分支合并是世界状态管理的深坑 |
| 伏笔变更 | 审校建议 + 人工确认 | 全自动 | 错误代价不可逆(误埋/提前泄底贯穿全书) |
| 可插拔 | 注册表 + 显式管道 + 契约测试 | 事件总线 | 单机场景保留可调试性与图显式性 |

---

## 9. 阶段规划(P0-P7,产出导向)

| 阶段 | 产出 | 验收(DoD) |
|---|---|---|
| P0 骨架 | 目录、pyproject、config、SQLite 全表 DDL、测试框架 | DB 可初始化,pytest 通过 |
| P1 LLM 接入层 | 抽象工厂 + 策略路由 + 埋点;GLM 先行;import-linter 进 CI | GLM 调通;usage_log 有记录;分层契约测试通过 |
| P2 记忆系统 | schemas、repository(ACL/fail-closed)、retrieval(结构化+POV+链接;向量可桩) | POV / 权限拒绝 / 版本链回放单测全绿 |
| **P2.5 记忆层评测** | 合成问题集 + 基线对比:**naive 最近 N 章上下文 vs 结构化记忆(facts+POV+混合检索)** | 报告:召回正确率 / POV 正确率(不该知道的信息泄漏率)/ 检索精度@章节数——核心卖点的早期证据 |
| P3 Agent 与图 | 6 Agent、图装配(生产循环/并行评审/重写/中断点/定稿管道)、两层 checkpoint、共创前置流程 | 脚本端到端一章生成,含中断模拟恢复 |
| P4 API 层 | FastAPI:小说 CRUD、生成指令、SSE、中断点端点、pending_review 抽检队列端点 | HTTP 驱动全流程,SSE 实时收正文 |
| P5 React 前端 | 小说管理 / 共创 / 细纲确认 / 阅读审阅伏笔面板 / 事实抽检面板 / 模型配置 | 浏览器完成全流程 |
| P6 量化评测(端到端) | 一致性 / 伏笔回收率 / 重写分布 / 成本报告;分级路由对比 | 量化报告(数据可复现) |
| P7+ 演进 | IF 线、向量兜底完善、Self-RAG 反思检索、可观测性外部化 | 按需 |

---

## 附:实现期备忘

摘要执行者(主控在定稿编排调用,便宜模型)/ 单本小说章节生成串行(SQLite 单写者,facts 写冲突防护)/ 检索按 branch_id 过滤 embedding / 中断点 B 用户改写循环不限次但成本可见化 / pending_review 抽检可与中断点 B 合并呈现亦可独立队列(实现时定交互形态)/ 量化指标操作性定义在 P3 定(一致性 rubric、伏笔回收率公式)/ 大纲 Agent 双评审模式(细纲评审 vs 成稿评审 prompt 分模式)/ 段落切分自然段优先 / thread_id 粒度 story+branch 一线程

---

## 附:实现状态对照(2026-09-07 审计,详见 AUDIT.md)

> 设计全集 vs 实现子集的显式对账。落地页宣传的功能以此为准,避免承诺与实现脱节。

### 已实现并投入使用

- 三级记忆 / POV 投影 / facts-beliefs 分表 / 置信度分层 + 抽检队列 / facts 版本链推翻(含 setting 场景链)
- 双评审 fan-out + 重写上限转人工(needs_user)/ 伏笔人工复核 / 三中断点
- 分层摘要:chapter + **stage(实现期新增,补齐设计枚举)**;story_recap 多维回顾
- **实体知识库(ADR-0015,2026-09-09 激活):共创种子/每章消歧(先查询再语义识别)/
  阶段末条目滚动/链接扩展一跳进 writer 上下文/别名识别/Codex 实体图谱/合并提案人工队列**
- 检索:结构化主路 + 向量兜底(embedding 已接入,含"世界背景"语义渲染)
- 协作式中断(用户按钮)+ 循环自动中断(细纲 3 轮上限 + recursion_limit 兜底)
- 全链路可观测:agent_traces 落库 + SSE 实时 + 历史回溯 UI
- 评测:memory_eval / e2e_eval 报告可复现
- C 端工作台:三栏 + 术语双模式(用户/开发者)+ 暗色 + 动效体系

### 部分实现(语义有裁剪)

| 模块 | 裁剪说明 |
|---|---|
| 短期记忆 | 设计"最近一章状态变化+结尾",实现为最近 2 章摘要 + 结尾 400 字 |
| 大纲版本化 | 落库+归档,无回看旧版 UI |
| 共创访谈 | 单发输入(tags+premise+构想),无多轮 |
| belief 演化链 | 表结构就绪,抽取不产 supersede/dispell |

### 未实现(表先建,功能未排期——空转表)

temporal_relations(时间偏序)、paragraphs(段落下钻)、
volume/book 摘要层、known_partial(部分知晓)

(entities/entity_links 已随 ADR-0015 激活转正,移出本清单)

### 明确降级(依赖或场景未到)

IF 线番外(依赖章节版本化)、章节版本化改写(v2/stale 连锁)、Self-RAG、OTel 外部化

### 实现期 ADR 补记

**ADR-0013 编排层事务豁免仓储 ACL**:定稿落库(commit_finalize)为满足"编排原子性"
(单事务、LLM 不进事务),绕过 Repository 写方法族直接执行 SQL。设计 §3.4 "仓储层私有、
Agent 无旁路"由此降级为:**读路径(检索)fail-closed 强制;写路径由编排层单点自律,
以 supervisor 权限语义执行**。repo 写方法族保留(测试与未来多 Agent 扩展用)。
权衡:单事务完整性 > 每方法 ACL;风险由"每表单写者"不变式与审计日志兜底。

**ADR-0014 向量兜底的世界背景语义**:兜底召回不做可见性过滤(区别于 POV 主路)——
无 visibility 行的客观事实(环境/背景)正是长尾召回的目标;POV 边界由 writer 渲染层
区分语义保障("世界背景:叙事可用,角色言行不得引用")。

**ADR-0015 实体层激活(先查询再语义识别)**:entities/entity_links 从空转表转正,
落地 EntityService(app/memory/,与 RetrievalService 同层,经 Deps 注入)。
四项所有者裁决(2026-09-09):
① 范围=全量(角色/势力/地点/物品/功法/概念),接受抽取噪声换长尾丰富;
② 条目内容=阶段末滚动摘要,复用 stage 边界机制,便宜模型并入该管道;
③ 去重=三层漏斗后置裁决——精确名/共创别名表(确定性)→ 向量 top-k 召回(非阈值二分)
   → LLM 批量语义裁决(结构化 verdict: same/new/uncertain),仅 uncertain 进抽检队列
   (复用 facts 抽检骨架)。放置位置=后置管道步骤而非 function calling 工具:
   消歧是无条件批量行为,不构成"模型决定是否调用"的工具语义(参见 ADR-0016 候选讨论);
   候选先写后合并(合并可逆、漏检不可逆),双阈值控队列量;
④ relation=自由文本,接受同义碎片化,消费端靠向量聚合同义关系。
消费端默认(可逆,未单独裁决):writer 渲染一跳邻居条目摘要;Codex 展示实体图;
评审基线暂不含实体图。build_context 在场角色识别升级为实体别名表匹配
(修复 name-in-brief 字符串包含的别称盲区)。
权衡:LLM 裁决每章多一次便宜模型调用 + uncertain 人工尾巴,换阈值方案无法覆盖的
道号/俗称/尊称消歧;先写后合并接受瞬时重复条目。

**ADR-0016 自动模式(双闸门可选自动确认)**:generate 请求携带 auto_mode(前端
工作台开关,按书记忆)。自动模式下:阶段细纲确认与章节审阅在评审绿时自动通过,
连续写作不中断;伏笔变更随评审建议自动生效(ADR-0007 的人工勾选降级为事后
Codex 台账可查)。**总大纲确认(中断点 0)两种模式都保持人工**——整本书的根基
方向必须作者亲自拍板(所有者裁决)。升级保底:任一路轮次耗尽(细纲 3 轮/
章节重写 3 次/总大纲重生成 3 轮)强制回人工,自动模式不吞升级信号;总大纲回炉
历史上无上限(靠 recursion_limit 兜底),本次补齐 3 轮上限与细纲/重写同口径。

**ADR-0017 反AI味机制(根源级,题材无关)**:诊断——模板化的根源不是某本书的
设定,而是:细纲把每章定义为"一个待完成事件"、评审奖励既有风格的加码(正反馈
回路)、上下文只注入"全部为真的上帝摘要"(无信息差)。六项机制(全部随书自适应
或题材无关):① 阶段细纲改为"张力计划"(阶段目标/各方意图/强制受挫/悬念锁/
分章计划),推进≠解决;② **能力契约**随共创生成(能做/不能做至少两类禁区/
成本/失效),注入大纲与每章写作,能力只给线索与现象、结论留给人物推理;③ 信息差:
角色意图卡(goal/knows/doesnt_know/self_interest)按在场过滤注入,对手底牌只留
在规划层不进主角上下文;抽取分层——口头陈述/听来情报默认进 beliefs(可为假),
只有叙述者客观描写进 facts;④ 评审加反向维度(能力越权/信息倾泻/主角全知/
无代价胜利/复读表达,命中即降分),禁"延续风格"式加码夸奖——拆掉放大器;
⑤ 伏笔悬置:埋设后 3 章内只可加深、严禁解释回收,可推进伏笔每章至多一条;
⑥ **动态句式黑名单**:定稿前统计近 5 章跨章高频(≥2 章、≥3 次)中文 4-gram
并合并为最长短语,注入写作与双评审的禁用清单——每本书自己长出自己的黑名单。
附带修复:writer 输出剥离残留 markdown 标题行。


**ADR-0018 精校通道(分级返工,修订:三值 fix_scope)**:全量重写仅用于**大范围
结构性偏离**(增删场景/改因果/改人物行动逻辑/能力越权/信息边界——未必是
偏离大纲,所有者裁决)。评审 verdict 的 fix_scope 三值:style(纯文风)/
local(局部事实修正,**评审必须给出"将X改为Y"精确处方**:数值衔接、称谓统一、
删越权结论句改推理线索等局部手术)/content(结构性)。merge 裁决:所有 revise
均为 style|local 且无 block → polish_draft 精校(新 AgentRole.POLISH,
deepseek-v3 便宜档);缺失/block/任一 content → 全量重写(缺省安全侧)。
精校输入仅草稿+评审意见+禁用清单,铁律:只改表达+**逐条执行评审精确处方**
(唯一的事实改动豁免,处方来自看过全上下文的评审),其余事实层面一律不动;
字数 ±10%,首尾句场景锚点不变;产物回同一双评审复检兜底。预算共享:精校轮
计入 rewrite_count(上限 3),耗尽照旧 needs_user 转人工。


**ADR-0019 称谓统一与评审职责正交**:诊断两案,同源连环——
①双评审 rubric 重叠:大纲一致性评审的契约携带文风职责(复读表达/措辞层倾泻/
[禁用表达]清单直喂"出现即扣分"),产出整份 local 级替换处方而不审吻合度
(ch11 实证:"将'江城精神病院'改为'那栋废弃建筑'(至少2处)"),与质量
审校的反AI五项逐字重复——多 judge fan-out 的前提是 rubric 正交,重叠即意见
冗余、无法按性质分流返工。②称谓漂移与猎杀循环:实体表 canonical
("精神疗养院")与文本层("精神病院",ch10 单章 12 次)从未对齐;该 4-gram
跨章高频以频次 13 进入动态黑名单(ch11-14 每章禁用),而写手受 brief/摘要
存量驱动必然回归该指称 → 评审每轮开处方 → 规避式改写稀释指代 → 重燃循环,
空转烧 REWRITE_LIMIT。裁决与机制:
- **职责切分**:大纲评审只评一致性/结构/信息边界(保留:能力越权/主角全知/
  无代价胜利/结构性信息过载);信息倾泻拆轴——场景信息负载归结构(大纲
  评审),罗列的措辞写法归质量审校;复读表达与禁用清单移出其输入,文风
  检查由质量审校独责。
- **正名 = 实体表 canonical name**(所有者裁决:名是什么不重要,统一重要);
  叙述层一律用规范名,别名仅容忍于对白口吻;存量正文不回改(连载语义)。
- **统一三件套**:①规范名词典——build_context 收集 active 非角色实体+别名
  (canonical_entity_registry),writer 上下文注入[实体规范名];②存量变体注册
  alias(一次性:"精神病院"→精神疗养院,黑名单排除集即刻覆盖);③动态黑名单
  召回**排除实体指称**——候选短语与实体名/别名/角色名互为子串即剔除:指称
  密度是叙事骨架而非复读腔(432f3e2 修虚词边界之猎杀语法,本条修实体召回
  之猎杀指称,合璧闭环)。
- 附带:抽取提示词明确机构/固定地点"命名即抽"(faction 覆盖缺口:13 章
  仅 1 个 faction 实体,"后勤部"74 次未入表,词典无锚则统一无从谈起)。
- 待办(第二步,所有者裁决延后):职责切分后 fix_scope 触发结构事实上由
  质量审校单方主导的正式化;writer 全量重写轮的旧稿传递(定向重构 vs 带着
  教训重掷);用户意见通道接入分级返工(现任何 revise 一律全量重写)。


**ADR-0020 伏笔治理(单独评审 + tier 双档账龄)**:诊断——15 章 37 条伏笔
36 open/1 resolved(回收率 2.7%),单向棘轮:ADR-0017 ⑤ 只节流回收("每章
至多推进一条"+悬置 3 章)而埋设零约束(每章新埋 2.4 条),数学上永远追不上;
plant 口径过宽(世界观基调/行动目标入册);drop 通道存在但零使用(评审看不到
账龄);活跃伏笔全量无截断注入写作与评审上下文,随堆积线性膨胀。行业对照:
同时活跃悬念钩健康区间约 5-12 条。裁决(所有者):**伏笔埋设单独评审**;
tier 分类判断给模型侧(语义无规则信号),压力状态机给代码侧——与 ADR-0015
消歧同构。机制:
- **ThreadReviewNode**(AgentRole.THREAD,中档)与双评审并列三路 fan-out,
  不参与 merge 表决;质量审校剥离 thread_changes(只评伏笔处理质量),
  ACL plot_threads 写者移交 thread_reviewer(reviewer 保留读)。
- **plant 准入**:口径=未解悬念钩子(世界观/阶段目标/设定不入册);必填
  tier(short 近程/long 绑定主线终局,basis 写明绑定哪条——长线是承诺不是
  免催收标签);容量上限 short≤8/long≤12(契约告知模型先 drop 腾位,
  commit 落库硬校验拒绝兜底)。
- **账龄梯度**(SHORT_AGE=8 章/LONG_AGE=40 章,可配):超龄活跃伏笔注入
  writer 时标[应回收];NULL(存量未回填)按 short 保守催收。
- **到期复核**(双时点第二点):每章对超龄未升格清单裁决 collect/
  escalate(每条仅一次,escalated_chapter 记章防无限递延)/keep(不持久化,
  允许重复复核直至收敛);escalate 为账本管理动作,定稿直接生效不进剧情
  确认链;thread_changes 仍走中断点 B 人工确认(ADR-0006 语义不变)。
- **存量回填**:36 条批量分类落库(12 long/24 short;long 恰卡上限,
  增量 long plant 需先腾位)。
- 待办:同实体/同地点关联伏笔合并去重;存量中按新口径本不该入册的条目
  (世界观基调/行动目标)是否清洗待所有者裁决;keep 裁决持久化(防同条
  伏笔每章重复复核)。
**ADR-0021 Schema 迁移版本化(生产化批次 0,评审 6.15)**:诊断——schema
升级靠 CREATE IF NOT EXISTS + PRAGMA 探测式 ALTER 无版本记录,多环境部署
无法判位、失败不可知(三方评审 P2)。机制:schema_migrations(version PK,
name, applied_at);version 1 = SCHEMA_SQL baseline(IF NOT EXISTS 天然幂等,
存量库重放补缺表),version >= 2 = 增量迁移函数(内部 PRAGMA 探测后 ALTER),
migrate() 按版本序在单事务内应用,版本记录与 DDL 同事务。实测结论:SQLite
DDL 事务性使失败版本整体回滚(含 ALTER),半迁移不留半成品,重启自愈;
探测幂等保留为防御(外部工具半改库)。规则:依赖迁移新增列的索引必须放
迁移函数内,不得进 SCHEMA_SQL(baseline 重放在存量库上因列缺失而失败,
test_db 回归注释);迁移函数内禁用 executescript(隐式提交破坏事务边界)。
ALL_TABLES 22 -> 23。生产化修复(评审/production_fix_plan_20260914.md)
批次 0;后续 users / story_members / story_run_state / 唯一索引等一律走
增量版本。

**ADR-0022 认证与多租户边界(生产化批次 1,评审 6.1 P0)**:诊断——API 无
认证/授权/租户隔离,任何人可读任意故事、审任意事实、改全局模型配置(三方
评审 P0)。所有者裁决(2026-09-14):公网多用户;JWT Bearer(SSE 已是
fetch+getReader,header 直接可加);管理员开户制(无自助注册,LLM 成本不可刷)。
机制:
- **凭据**:argon2id 哈希;POST /auth/login 签发 HS256 JWT(默认 2h,
  NOVEL_JWT_SECRET 环境变量,默认 dev 值仅限本机);自助改密 + admin 重置。
- **租户模型**:users(id/username UNIQUE/argon2 hash/role admin|user/status
  active|disabled);stories.owner_id;story_members(story_id,user_id,role
  owner|editor|viewer,PK 复合)——单人多书 owner 即够,协作预留。
- **收口**:story 级端点 get_current_user + require_story(越权 404 不泄露
  存在性);审核队列(facts/entities)按 visible_story_ids 过滤 + 审核动作
  复核归属;config/models 与 /admin/* admin 专用(全局配置语义);create_story
  原子写 owner + membership。
- **撤销语义**:短有效期 + 每请求校验 users.status,禁用即时生效,不引黑名单表。
- **种子**:users 空时建初始 admin(NOVEL_ADMIN_USER/PASSWORD,默认
  admin/admin123 打 warning);存量 story owner 回填给首个 admin + 补 membership。
- 迁移 v3 auth_tenancy(ADR-0021 机制首个消费者);ALL_TABLES 23 -> 25;
  前端 api.js 自动带 Authorization + 401 广播踢回登录页 + 登录/退出 UI。
边界:与 Agent ACL(ADR-0006)正交——ACL 管 Agent 对数据域,本 ADR 管
HTTP 用户之间。默认 admin 密码与 JWT secret 生产部署必须经环境变量覆盖
(DEPLOY 文档批次 7 落地)。

**ADR-0023 active run 互斥与并发事件归属(生产化批次 2,评审 6.2/6.3)**:
诊断——_sse_run 无互斥,同 story 双击 generate 启动两个图 run(重复章节/
checkpoint 覆盖/清空首轮事件历史);全局单值 _current_thread 在多 story
并发时被覆盖,节点内 emit 串台。机制:
- **互斥**:main._active 的 check-then-add 持独立锁原子化;第二个 generate/
  resume 409(story run already active);worker finally 持锁 discard。
- **数据闸**:chapters 部分唯一索引 (story_id, chapter_no) WHERE
  status='active'(迁移 v4 chapter_unique_active,存量重复保留最新 active、
  其余置 archived 不丢数据)——互斥之外的第二道闸。
- **事件归属**:删 _current_thread 全局单值,改模块级 ContextVar
  run_ctx[(thread_id, run_id)];worker 线程入口 set,emit() 无显式
  thread_id 时兜底读取;实测 LangGraph fan-out 节点完整继承 ContextVar
  (并行节点/LLM 回调各自归属);事件体统一注入 run_id(批次 5 贯通观测表)。
  writer/polisher 的 token/draft_start emit 显式传 state.story_id(双保险),
  流式判断改 deps.has_subscribers(sid)。
- 附带:run_id 生成于 _sse_run(SSE done 事件携带);空 thread 事件(无
  上下文误 emit)落空串不误归属任何 story。

**ADR-0024 数据正确性口径(生产化批次 3,评审 6.5/6.6/6.8/6.11/6.12)**:
诊断——POV 记忆不过滤 rejected(人工否决的事实仍进上下文,审核裁决形同
虚设);vector_hits 全知兜底无信息差边界(ADR-0014 的"无 visibility 行=
背景"语义被扩大成"任何事实都可兜底命中",角色言行可借向量召回引用他人
独知信息);用户指令"取走即标"在生成失败/中断/重写时丢失;confirm_master_
outline 的角色卡落库在大纲提交之后非原子(中途失败留下"大纲已确认但角色
卡缺失"半成品);阶段末章自身摘要未落库即参与聚合(SummaryNode 先于
finalize),阶段摘要漏末章。机制:
- **POV status 口径**:world 回放与 POV 查询统一 `status != 'rejected'`;
  confirmed 照旧,pending_review 以"低置信线索"语义进入(writer 渲染层
  显式分节:"只能作为暗线/伏笔素材铺陈,严禁作为确定事实写入正文")。
  修订 ADR-0014:**分级硬过滤**——vector_hits 只滤"有 visibility 行且
  知情者不全在当前 POV 集"的信息差事实(硬边界,不靠 prompt);无
  visibility 行的背景事实保留(叙事素材,writer 渲染层标注言行边界)。
- **共创单事务**:persist_characters 拆为 prepare_character_seeds(纯计算,
  embedding 在事务外,失败零残留)+ commit_character_seeds(纯 DB 写,
  commit=False 沿用外层事务);confirm_master_outline 把大纲归档/落库与
  角色卡/实体种子并入单事务。附带:embedding 调用移出 run_lock 临界区。
- **指令延迟消费**:take(取走即标 consumed)改 peek(只读)+ build_context
  注入 user_directive_ids + commit_finalize 在定稿事务内 mark_consumed——
  指令生命周期与章节定稿原子,回滚不丢指令。
- **阶段末章摘要**:SummaryNode 把内存中本章摘要显式并入聚合输入(按章号
  排序去重),不再依赖"先落库再聚合"的时序假设。
- 附带修复分层契约:argon2 哈希下沉 app/core/security(db 层管理员种子不再
  import app.auth——import-linter 实测抓到 db->auth->main->graph 断链)。

**ADR-0025 伏笔 thread_id 全链路(生产化批次 3,评审 6.7)**:诊断——伏笔
advance/resolve/drop/escalate 落库全靠描述前 12 字 LIKE 匹配最近 open 线,
近似描述的两条线会串线改错(评分卡"数据正确性"直接扣分项);且匹配失败
静默跳过,无任何留痕。机制:
- 评审契约(thread_reviewer)强制回传 thread_id:活跃/超龄清单每条开头
  方括号内带 id,advance/resolve/drop/reviews 必填,description 照抄留痕;
  plant 留空(新线 id 落库时生成)。
- 定稿管道 _match_open_thread:优先 `id=? AND story_id=? AND status='open'`
  精确命中(escalate 附加"未升格且 short"条件);id 缺失/失配才降级描述
  LIKE,并在 retrieval_audit 留痕(caller='thread_fallback',query 记缺失
  id 与描述片段)——契约失守可追查,不再静默。
- 中断卡(Workbench)对带 thread_id 的变更显示"定向已登记伏笔 #xxxxxx",
  人工确认环节可见操作对象;确认透传字段原样携带 thread_id。

**ADR-0026 LLM 输出强契约与弹性(生产化批次 4,评审 6.9/6.10)**:诊断——
ask_json 只做宽松 JSON 提取,无 schema/枚举校验、无重试、无结构化失败记录,
模型输出空值/截断/错枚举直接把节点炸穿;provider client 每次调用 new 一个
(连接池浪费)、无显式 timeout/retries;target_chapters 无上限;token 成本
无预算闸门——公网多用户下成本不可控。机制:
- **schema 硬约束**:app/graph/agents/schemas.py 每个 JSON stage 一个
  Pydantic 模型(12 处调用点全接线);真枚举(verdict/action/tier/decision/
  confidence/fact type)Literal 硬校验,自由文本给安全缺省。
- **自纠重试**:ask_json 解析/校验失败时把校验错误回喂模型重试一次
  (self-correction,行业常规);仍失败抛 LLMFormatError(trace_id +
  error_code)。实现期裁决:校验通过后返回 model_dump() 的 dict——节点侧
  保持 dict 协议,12 处调用点不必改对象访问。
- **失败台账**:迁移 v5 新表 llm_failures(story_id/run_id/stage/node/
  trace_id/raw_output 截断 4k/error);build._node 包装层统一落痕;SSE
  error 事件携带 error_code + trace_id 关联归因。
- **安全降级**:评审类节点捕获 LLMFormatError 后——大纲/质量评审默认
  revise(宁可误返工,绝不静默 pass);伏笔评审默认零账本动作(不虚构
  plant/advance);降级裁决照常进 review_results 审计。
- **parse_json_loose 加固**:首尾截取改 JSONDecoder.raw_decode 扫描第一个
  完整对象(容忍前后杂文);截断输出抛 JSONDecodeError 走重试/台账归因,
  不做括号补全式猜测修复。
- **弹性**:factory 按 provider 缓存 chat/embed client;OpenAI 客户端显式
  timeout(Settings.llm_timeout_seconds,默认 120s)+ max_retries=3(SDK
  内置 429/5xx 指数退避);流式 last_usage 改 threading.local——客户端
  复用后多 story 并发流式 usage 不串台。target_chapters Field(ge=1,le=50)。
- **预算闸门**:每日 token 预算(UTC 日按 usage_log 聚合),story 级与
  全局级,环境变量 NOVEL_STORY/GLOBAL_DAILY_TOKEN_BUDGET(0=不限);
  超限 429 + budget_exceeded,generate 入口前置检查。

**ADR-0027 run 状态持久化与 run_id 贯通(生产化批次 5,评审 6.4/6.13)**:
诊断——运行状态只存进程内(_active 集合 + in-memory 事件快照):进程重启后
"等待人工确认"的 story 状态归零,前端刷新丢失中断卡,无法判断哪些书在跑;
run_id 只存在于事件流,usage_log/agent_traces/review_results 无归属,事后
按轮次归因成本与诊断不可行。机制:
- **story_run_state 表**(迁移 v6,PK story_id 单行 upsert):status
  (running/waiting/idle)+ interrupt_type + interrupt_payload(完整 JSON
  中断卡)+ target_chapters + error_code/error_stage + run_id。
  Deps.set_run_state 用 ON CONFLICT COALESCE 语义:run_id/target_chapters
  缺省保留旧值,interrupt/error 字段整体覆写。
- **写入点在 worker 骨架**:启动→running(清 interrupt/error);__interrupt__
  →waiting + payload json 落库;done/stopped→idle 清卡;error→idle 带错误码。
  端点/节点不直接写状态,单一出口便于排查。
- **读时合并(DB 为主,_active 为辅)**:run-state 端点——DB waiting+在跑
  →running(resume 竞态窗口);DB running+不在跑→idle(崩溃残留兜底);
  中断卡数据优先 DB payload(跨重启存活),事件快照兜底。附带修复:done 后
  事件快照里的旧 interrupt 不再误报 waiting(此前 last_interrupt 扫描无
  失效语义)。
- **启动收敛**:build_engine 在 init_db 后把 status='running' 的残留收敛为
  idle 并清 interrupt——可恢复性由 LangGraph checkpoint(SqliteSaver)保证,
  重新 generate 即续跑;waiting 行原样保留(用户未裁决的中断卡不丢)。
- **run_id 贯通**:usage_log/agent_traces/review_results 基线加 run_id 列
  (迁移 v6 add_column 探测);usage sink 经 run_id_provider 注入读取
  ContextVar(observability 不反向依赖 graph,保持分层);_trace_sink 与
  log_review 直接读 current_run()。四表按 run_id 可还原任一轮次的全链路。
- **前端恢复**:Workbench 挂载时 waiting 且事件快照无 interrupt 事件
  (服务重启后 in-memory 快照已失)→ 从 run-state 的持久化 payload 重建
  中断卡——刷新/重启不再丢卡。

**ADR-0028 可观测性补全(生产化批次 6,评审 6.13)**:诊断——四张观测表
缺 user_id(成本无法按触发用户归因)、llm_failures 缺 provider 状态与重试
口径;worker 异常裸 str(exc) 直发前端,服务端无全栈日志;运营无全局视图。
机制:
- **user_id 贯通**:迁移 v7 给 usage_log/agent_traces 补 user_id;新增
  user_ctx ContextVar(worker 入口 set,与 run_ctx 同机制,节点/fan-out
  继承),usage sink 经 user_id_provider 注入读取——observability 不反向
  依赖 graph,保持分层。
- **失败台账补列**:llm_failures 加 provider_status_code(异常自带
  status_code 时记录,如 openai.APIStatusError)/ retry_count(应用内
  重试口径:ask_json 自纠=1)。数字纪律:SDK 内部退避次数不对外暴露,
  不虚报。_node 包装层扩展:非坏-JSON 异常(provider 429/超时、DB 约束)
  同样落台账留痕,StopRequested(用户主动中断)除外。
- **结构化错误**:worker 异常时服务端 logging.exception 留全栈
  (logger="novel.agent");SSE error 事件只带 error_code / run_id /
  trace_id / message(截断 300)——前端拿稳定标识做提示与关联,堆栈
  只留在服务端日志。
- **/admin/stats**(admin 专用):active runs、等待中断数、错误 run 数、
  近 24h LLM 调用数与失败率(分母 usage_log、分子 llm_failures)、
  token 成本按日+story+owner 聚合(近 200 行)。JSON 口径,不引外部
  metrics 栈。

**ADR-0029 双 token 认证(评审后运营反馈)**:诊断——access JWT 默认 2h,
过期即踢回登录;前端门卫只查 localStorage 有无 token,长会话(写作一上午)
每隔 2h"莫名"断一次,且无诊断线索。机制:
- **类型互斥**:payload 写 `typ`(access|refresh),消费端校验——refresh
  不能当 access 用(防长效凭证顶替绕过 2h 窗口),access 不能当 refresh
  用(防短期 token 自我续期成永动机)。`POST /auth/refresh` 专用依赖
  `get_refresh_payload` 校验 refresh 类型。
- **滑动续期,无状态**:refresh 7 天(NOVEL_JWT_REFRESH_EXPIRE_HOURS,
  默认 168),每次 refresh 签发全新 access+refresh 对——活跃用户永不
  被踢;不引 token 表、不旋转(旧 refresh 自然过期即失效),撤销语义
  仍靠 access 短效 + 每请求查 users.status(禁用即时失效,refresh 签发
  时同样再查 status)。
- **前端单飞**:401 → 单飞 refreshOnce()(并发多请求只发一次
  /auth/refresh)→ 重试原请求一次 → 仍 401 才踢出,且 console.warn
  记录请求 URL 与原因(refresh 失败/无凭证)——"莫名被踢"从不可诊断
  变为控制台一行定位。SSE 请求同路径(generate/resume/attach)。
- **trade-off**:无状态 refresh 的代价是无法主动吊销单个会话(丢了
  refresh token = 7 天窗口内可换新)——接受:单人/小团队部署,禁用
  账户(status)与改 jwt_secret(全员下线)已覆盖实际撤销需求;引
  token 表换吊销粒度不属于当前威胁模型。


**ADR-0030 后端解耦重构(2026-09-17,所有者拍板"先重构")**:诊断——
importlinter 分层契约中 app.api 是空包,813 行 main.py 游离在契约外;
graph/runtime.py 的 Deps 单类混 7 种职责(事件总线/SQL 直访/记忆拼装/
风格策略/定稿大事务/运行状态机/失败台账),conn.execute 直写 runtime 57 处、
main 39 处,仓储被 repo.conn 穿透名存实亡;build.py 编排与节点业务混杂。
功能线(数据飞轮/分卷大纲②③)排队,重构先行。机制(docs/REFACTOR.md):
- **绞杀者五阶段**:0 契约硬化(main.py 迁 app/api,旧路径兼容转发)→
  1 main.py 拆 routes/+sse+RunService → 2 Deps 拆显式协作者
  (EventBus/RunStateStore/RecapBuilder/StylePolicy/FailureLedger/
  FinalizeUoW,ports 先立,Deps 变兼容门面)→ 3 SQL 收敛仓储+定稿 UoW →
  4 build.py 三分(wiring/routes/nodes)。每阶段 146+ 测试全绿即 commit,
  可独立停止/回退。
- **依赖方向**:api → application ← infrastructure;graph 编排挂
  application 之上;core 垫底;全项目仅装配点(build_engine 进化)知道
  具体实现。
- **trade-off(裁剪,防过度设计)**:不拆多进程(进程内互斥/单事务是
  ADR-0023/0027 有意设计);不引 DI 框架(装配点唯一,手工构造注入);
  不引 ORM(手写 DDL/SQL 是资产);不立 domain 目录(纯规则三次法则)。
  解耦深度匹配单机单进程约 100 用户体量,不为"层数"付费。
- **实施结果(2026-09-17 当日交付)**:五阶段全部完成——runtime.py
  973 行上帝对象 → 门面 254 行 + 九组件(runtime_components,端口见
  application/ports);api 层内联 SQL 清零(queries.py 四域读模型:
  StoryQueries/ReviewQueues/UserStore/ObservabilityQueries;Agent 侧
  ACL 仓储与用户侧读模型分工);build.py 三分(nodes/routes/wiring,
  build.py 变兼容转发)。定稿 UoW 落位 infrastructure(FinalizeStore
  端口+单事务)而非 application 转发层——纯 DB 事务无业务规则。
  全程 150 测试绿 + 契约 KEPT,行为零变化,每阶段独立 commit(46c72f6/
  af57507/40b6e15)可回退。


**ADR-0031 对话式工作台 ChatDock(2026-09-17,所有者三轮定调)**:诊断——
一键生成是唯一入口,"写快点/换个方向/现在到哪了"这类意图只能等下一次
生成或去读界面;工具条折叠输入框只到 directive 通道,不可查询不可对话。
所有者三条根定调:一键生成不是唯一入口;Agent 模式同样生成、同样落库、
该有的审核同样有;用 ReAct 模式(方案演进 v1 意图路由 → v2 双模式单内核
→ v3 ReAct,docs/CHAT_WORKBENCH.md)。机制:
- **双模式单内核**:Agent 模式与一键生成共享同一图 state/checkpoint/
  评审循环/闸门/commit_finalize/usage_log;ReAct 循环手写(~百行,不引
  create_react_agent,ADR-0030 精神)。agent 的自由是"选择下一步做什么",
  不是"改流程规则"。
- **function calling 透传**:ChatMessage 加 tool_calls/tool_call_id/name,
  LLMResponse 加 tool_calls;openai_compat 序列化 assistant 工具请求与
  tool 回执;facade.chat 加 tools 参数直通 provider(override 回放分支
  不受影响)。新 AgentRole.CHAT(默认 glm-5,温度 0.6)。
- **P0 工具三类**(生成型节点工具 P1 接入):查询(book_detail/chapter/
  codex/usage/run_status,只读复用 queries.py)/流程(stop_run 置协作
  停止;启动仍引导按钮,P1 桥接)/指令(record_directive 走既有通道,
  下一次生成生效)。工具执行异常作为观察回填,循环不中断。
- **chat_messages 表(migration v8)**:id/story_id/user_id/role(user|
  assistant|tool|system)/content/meta_json/created_at,单 story 单会话;
  tool_calls 与 tool 回执结构化入 meta_json,history_messages 完整还原
  工具链(下一轮 ReAct 可见上一轮调用过程)。
- **全链路 debug 日志(所有者硬需求)**:logs/chat/{story_id}.jsonl 每
  事件一行(user_message/step/tool_call 参数全文/tool_result 观察**全文
  不截断**/final_reply/error),含 ts/turn_id/步号/耗时/tokens;落盘全量
  与 LLM 上下文回填截断(3000 字)两者独立;写失败不阻断主流程。与
  agent_traces 分工:traces 管 LLM 调用快照,JSONL 管决策链时序。
- **步数上限兜失控**:chat_max_steps(默认 15,可配)耗尽后强制一次无
  工具总结调用,保证每轮有最终回复;成本上 ReAct 思考步额外消耗,预算
  闸门(BudgetExceeded→429)照常生效。
- **人审不旁路**:确认/审核类操作不进工具表,agent 只能建议不能代替;
  对话内确认卡 P1 落地。
- **trade-off**:P0 不做对话内触发生成(启动引导按钮,P1 NodeRunner 桥
  接 SSE 双流);不做多会话轮换(单 story 单会话,超长后截近 N 条);
  chat_messages 直写不加引擎锁(WAL 单行原子,与 queries.py 读模型同口径)。
- **实施结果(P0 当日交付)**:facade tools 透传链 + migration v8 +
  application/chat_service.py(ReAct 循环 + ChatToolbox 七工具)+
  api/routes/chat.py(SSE chat + history)+ 前端 ChatDock.jsx(消息流/
  工具执行卡/Enter 发送 Shift+Enter 换行,替换原"指示…"折叠入口)。
  155 测试绿(新增 test_chat.py 五用例:ReAct 循环/工具链还原/坏参数
  回填/步数上限/401)+ 契约 KEPT。
- **P1 第一块:ReAct 独占节点 revamp_chapter(2026-09-17,所有者拍板
  功能与追溯语义 b)**:重构已定稿章——工具组装输入(作者意见+原文进
  chapter_brief)→ update_state(as_node="chapter_slice") 写回 → sse_run
  (None, subscribe=False, fresh=False) 触发后台续跑,写作/三评审/人审/
  定稿全走既有图,一键模式零改动。门禁:工具表只挂 chat 端点 + revamp
  字段只在工具写回时存在(route_next 短路 END、finalize 计数不增、
  commit_finalize 旧版归档+新版 version_no+1 挂 prev_version_id 链,
  复用 R2 章节版本化)。冲突标注(拍板 b):人审卡前 sse worker 调
  run_conflict_check(REVIEWER,重写稿对照 N+1 起各章开头,JSON 清单
  {chapter_no, conflict, suggest}),坏 JSON 降级 None 不阻断——标注是
  辅助不是闸门;作者逐章决定是否再 revamp。NodeRunner(graph 层,
  graph_provider/launcher 由 api 装配注入,本模块零 api 依赖)是后续
  生成型/裁决型节点工具的扩展点。159 测试绿(test_revamp.py 四用例:
  端到端落库挂链/守卫不触发/冲突解析与降级/无后续章)+ 契约 KEPT。

**ADR-0032 书籍删除·软删除(2026-09-20,所有者拍板四决策)**:背景——书架
此前无任何删除能力(全库无 DELETE)。决策与机制:
- **软删除语义**:migration v9 给 stories 补 deleted_at(TEXT,NULL=在架);
  子表(chapters/outlines/facts/entities/chat_messages 等全部 FK 表)与
  LangGraph checkpoint(thread_id=story_id)、story_run_state 全保留——
  waiting 中断卡留待未来"恢复/彻底删除"端点(本期不做)。
- **观测表物理清除例外**:usage_log/agent_traces/review_results/
  retrieval_audit/llm_failures 五表只有 story_id 无 FK,属过程遥测,
  拍板随删物理清除(书删即不再计入跨书统计);与 soft_delete 同一显式
  事务(BEGIN..COMMIT,isolation_level=None 下手工管理)。
- **权限 owner+admin**:DELETE /stories/{id} 检查顺序 story_role(None→
  404 不泄露存在性,含软删过滤)→ 非 owner/admin 403(editor/viewer 同)
  → _active 运行中 409(X-Error-Code: story_running,先停止再删)→
  soft_delete_story rowcount=0 兜底 404(防并发双删)。@locked 与
  generate/resume 同锁串行,消除 _active 检查与图启动的 TOCTOU。
- **读路径收口**(其余 18 个 story 级端点零改动,require_story 统一 404):
  auth.story_role 两分支 + visible_story_ids 两分支(审核队列不再出现
  软删书 pending 项)+ queries.list_for_user/story_row/main_branch(纵深
  防御)+ ops_stats 等待中断/错误 run 两计数过滤(软删书 run_state 行
  保留但不污染运营口径)。响应 200+{"ok","deleted_at"} 而非 204:
  前端 j() 封装假定 JSON body,跟随 stop 端点先例。
- **已知权衡**:chat 回合不持引擎锁(ADR-0031 既有口径),已在流的对话
  可在软删提交后经 NodeRunner 为已删书起 run——窗口窄、后果轻(写进的
  章节数据本就保留、读路径全过滤),不在 sse_run 层加校验。
- **实施结果**:ddl v9 + auth 收口 + queries 读过滤/soft_delete_story +
  DELETE 端点 + 前端书架垃圾桶(AlertDialog 确认,409 内联文案)。
  166 测试绿(新增 test_story_delete.py 七用例:全端点 404/幂等/权限
  矩阵/admin 跨删/运行中 409/观测清除与子表留痕/审核队列过滤)+
  契约 KEPT。

**ADR-0033 DeepSeek 官方 API provider(2026-09-20)**:诊断——deepseek 系模型
此前仅经百炼聚合调用,无官方直连通道。机制(ADR-0012 扩展点标准用法):
- DeepSeekFamily(OpenAICompatFamily 子类,name="deepseek",
  base_url=https://api.deepseek.com);DEEPSEEK_API_KEY 环境变量,配了
  key 才注册进工厂。官方 OpenAI 兼容,零新客户端代码。
- 路由优先级保持不变:百炼 key 在 -> 全走百炼(现状零变化);无百炼
  key 时 deepseek-* 且有官方 key -> 直连官方;glm-*/其余分支不动。
- **模型名差异**(部署须知的坑):官方名是 deepseek-chat(通用)/
  deepseek-reasoner(推理),不是百炼的 deepseek-v3——走官方时用
  MODEL__<ROLE> 或前端配置页指定官方名。
- **专属优先(2026-09-20 所有者提供 key 并委托拍板)**:deepseek-* 配了
  官方 key 即直连,优先于百炼聚合;glm/qwen/MiniMax 系不受影响仍走百炼。
  部署配置同步:六个中/便宜角色(EVENT/CHARACTER/ENTITY/POLISH/SUMMARY/
  THREAD)显式指到 deepseek-chat——ENTITY/POLISH/THREAD 此前用代码默认
  deepseek-v3,切官方后该名会 400,必须在 .env 显式覆盖。reasoner
  (推理版)未用:这些角色全是结构化抽取/摘要任务。key 经真实最小调用
  验证有效。

**ADR-0034 章节字数下限三层守卫 + DeepSeek 思考开关(2026-09-21,《古真神》
诊断驱动)**:诊断——章字数 3711→3031→2222 逐章下降。根因两层:①v4-pro
(V4 双模)默认思考,ch3 的 WRITER 调用 out=12385 token 中约 1 万是推理、
散文仅 ~2200 token,思考挤占产出;②系统无篇幅闸门:writer 提示词"2500-4000
字"是软约束,quality 评审契约无篇幅维度(2222 字照样 pass),merge 无代码
校验。机制(所有者拍板:强角色切 deepseek-flash + 充值,守卫落地):
- **三层守卫(settings.chapter_min_chars,默认 2500,0=关)**:writer 产出
  <下限 → 带反馈补写一次(仿空输出重试模式;二稿严格更长才采用,否则保留
  一稿);quality 评审输入携带[字数下限/实际字数]标注 + 契约条款"低于下限
  必须 revise";merge_reviews 代码兜底——双 pass 但低于下限强制 revise,
  复用 rewrite_count/REWRITE_LIMIT 轮次上限,不新增循环机制(与"循环控制
  只用轮次上限"纪律一致)。提示词字数区间 f-string 引用同一配置,与守卫
  同源不漂移。
- **思考开关(settings.deepseek_disable_thinking)**:DeepSeekChat 覆盖
  chat/stream(openai_compat 加通用 extra_body 透传)注入 thinking
  disabled;flag 关时不附。全局生效(评审也关)——flash 定位成本优先,
  质量降档由所有者实测后再议。
- **模型名口径**:/models 实查官方仅 deepseek-flash / deepseek-v4-pro /
  deepseek-chat(第三方"v4-flash"叫法不存在);强角色四行 → flash,.env
  部署配置(gitignore 不入库)。
- **测试策略**:conftest autouse 置 chapter_min_chars=0,167 既有测试零
  回归;test_length_guard.py 五用例(补写一次/二稿不更长保留一稿/双 pass
  强制 revise 带 feedback 落库/floor=0 不影响 pass/评审输入标注)+
  thinking 开关单测。173 绿 + 契约 KEPT;flash 关思考实调 in/out 9/1
  对比 v4-pro 开思考 88/10 且 content 空,确认思考关闭生效。

**ADR-0035 剧情承载短语豁免——禁用清单死锁修复(2026-09-21,《古真神》
ch4 实证)**:诊断——两章连续跑满 3 轮重写转人工。分数轨迹:quality 的
style 三轮锁死 6.0(<7 即 revise),反馈每轮都是"月圆复读"。根因是
**动态黑名单捕获了剧情死线**:ch2 立下"月圆之约"(苏家退婚期限),跨章
高频后 4-gram"在月圆之"进了 ch4 禁用清单;本章要点又必须提及该期限,
写手换用"月圆之日/距十五"等变体,评审把**同义变体**一并按复读抓——
写手永远无法合规,3 轮必然耗尽。与 ch10"精神病院"(ADR-0019 实体排除)、
ch12 语法黏连(虚词修剪)同族:禁了叙事骨架,评审-重写必死锁。机制:
- **派生层排除**:recent_phrase_blacklist 加 exclude_texts(默认 None);
  与豁免文本有 ≥3 字交叠的短语不入清单。build_context 组装豁免集 =
  本章要点 + 阶段细纲 + 活跃伏笔描述 + 上期衔接(剧情文本全在 state/
  检索产物里,零额外查询)。StylePolicy 端口与 Deps 门面同步。
- **评审契约收口**:quality 契约加复读豁免条款——剧情承载词(伏笔名/
  期限约定/专有设定)的必要指称不算复读,不追究同义变体;复读仅限修饰性
  口头禅与句式;清单外莫再扩大化(今天死锁的另一半正是评审自行扩大化)。
- **验证**:回归用例(死锁形态复现 + 豁免后口头禅仍命中);《古真神》
  真实数据复算 ch5 清单,月圆系全部消失,剩余均为可自然规避的修饰短语。
  174 测试绿 + 契约 KEPT。
- **遗留观察**:'经脉闭塞''炼体三重'等设定术语仍在清单边缘——它们出现在
  facts/前文而非要点文本时不受豁免;但写手可自然换称,反馈可执行,不构成
  死锁;若后续实证再抓,同机制扩豁免集(facts 内容)即可。

**ADR-0036 串行双闸评审——结构先行,风格后置(2026-09-21,所有者拍板 B 方案
+ 结构 3 轮/风格 2 轮)**:诊断——《古真神》7 次 needs_user。ADR-0035 除掉
月圆死锁后仍大概率转人工,新根因:①**合取闸门**——三评审并行跑完 merge 要求
outline AND reviewer 同时 pass,历史单路通过率 reviewer 7%(2/27)、outline
24%(8/33),联合 ~2%/轮,3 轮全过 <6%,数学上必然转人工;②**双反馈同注**——
rewrite 时两份意见同时塞给 writer,修结构顾不上风格、修风格破了结构
(r1 能力越权→r2 要点缺失→r3 信息过载的打地鼠轨迹)。机制:
- **拓扑**:write → [结构闸:review_draft_outline ∥ review_threads →
  struct_merge] →(pass)→ [风格闸:review_quality → style_merge] →
  user_review。结构 revise → write_draft 全文重写(只带结构反馈——
  quality_review 此时未跑,上一章残留由 next_chapter/revamp values 清空);
  风格 revise → polish_draft 精校局部修(**不重掷全文骰子,结构成果不被
  风格返工翻掉**,ADR-0018 精校通道专属风格闸)。
- **计数**:结构闸 rewrite_count/REWRITE_LIMIT=3(口径与旧 merge 零变化:
  首稿+2 次重写);风格闸 polish_count/STYLE_POLISH_LIMIT=2(执行 2 轮
  精校,第 3 次 revise 判定耗尽)。字数兜底(ADR-0034)移入结构闸——
  字数是内容问题。伏笔评审与结构并行,仍不表决。
- **语义变化**:旧 _polishable(双评审 fix_scope 分类)删除——一切 outline
  revise 归结构闸全文重写(outline 的意见本质是内容层),风格闸只消费
  review_quality。merged_verdict 保留为风格闸终态(人审/auto_mode 消费);
  新增 struct_verdict;中断卡聚合 rewrite_exhausted(任一闸)并新增
  polish_exhausted 明示。
- **trade-off**:墙钟变长(串行两阶段);风格闸期间结构不复检(polish
  ±10% 字数约束保结构不被翻);单闸 pass 率低的问题(评审贴线抖)不因此
  消失——串行后 outline 单维 3 轮通过率 ~56%,仍可能转人工,留待实测
  再议(阈值线/评审模型)。
- **实施**:merge_reviews 一分为二(struct_merge/style_merge,reviewer 审计
  名同步);route_after_merge/_polishable 删除,新增 route_after_struct_review/
  route_after_style_review;wiring 重接;build.py 再导出更新;SSE payload 加
  struct_verdict/polish_count/polish_exhausted。测试:e2e 审计断言与精校
  两用例按串行语义重写(风格耗尽:polish×2/quality×3/draft×1),字数兜底
  用例挂 struct_merge,_polishable 用例换串行路由断言。174 绿 + 契约 KEPT。
- **补记(同日,所有者指出):人审打回分阶段**——原 route_after_review 对
  一切打回都走全文重写,风格闸耗尽的卡打回后重新过结构闸(实证:03:34
  打回后 threads/outline 全部重跑)。修正:struct_verdict=pass 时打回路由
  polish_with_feedback(精修带用户意见,用户"将X改为Y"式修正视同处方
  必须执行),结构闸未过/耗尽仍全文重写。链路:用户直修 1 次 + polish_count
  随人审重置再满 2 轮;大改需求走 revamp_chapter 全文通道。176 绿。
- **补记 2(同日,所有者指出低级遗留):风格闸契约职责收口**——串行化时
  只改了拓扑/路由,quality 契约没同步审:fix_scope 仍含 content 选项、
  反AI检查仍含能力越权/主角全知/无代价胜利(全是结构闸专属)。实际伤害:
  风格闸以 consistency=6 打回"数值矛盾/晨景三遍"等内容理由,精校(表达层
  工具)修不了 → 必然不收敛的打地鼠。修正:quality 契约明确"风格闸评审,
  同稿已过结构闸"——fix_scope 只剩 style|local(全部发现必须可精校落实);
  反AI检查删结构类只留表达层(信息倾泻/复读);consistency 维度收口为
  同章单句级自洽(数值/称谓/时间线对得上,local 可修),与基准的结构吻合
  度归结构闸;结构性发现仅可在 feedback 末尾【结构风险】标注,不扣分不改
  verdict。降级兜底 fix_scope 同步 content→style。排查确认无同类遗留
  (outline 保留完整分类是正确归属;style_merge 不消费 fix_scope)。
  176 绿 + 契约 KEPT(代码先行提交 41c1377,本条补沉淀)。


**ADR-0037 结构闸通过率修复四件套(2026-09-21,《古真神》串行双闸上线后
outline 0/9 轮诊断,所有者拍板全修)**:诊断分两层——
- **提示词产物欠约束(切片侧)**:①时间线不定死——要点写"从当夜或次日清晨
  起笔"却不说对应倒计时数字,写手按逻辑选"次日→六天"反而被抓"时间线
  硬伤"(评审 r1 自己给出两个均可的修法却仍 revise);②要点 4 是四连复合
  条件(察觉 AND 反制 AND 收缩 AND 监视加严),flash 关思考一轮落不全,
  每轮补一条评审换着抓——打地鼠。
- **判据字面化无弹性(评审侧)**:r3 反馈自标"local(单句可改)"却强行升
  content 给 revise;分数永远贴 6.0-7.0 线;关思考的评审对数值/时间线
  退化为字面匹配(抓数字不抓逻辑)。
机制四件:
- **倒计时事实化**:chapter_slice 要点第一行固定时间线锚([时间线]开场时点
  +距期限 X 天,数字从上期衔接推得)——写手与评审拿同一个数,基准不定死
  数字双方就各写各的。
- **判据弹性化**:outline 契约加判定纪律——按逻辑不按字面(时间推进与基准
  一致递变即过,仅自相矛盾/无依据跳变算硬伤);local 不单独致 revise
  (全部问题均 local 时 pass+处方入 feedback);"深化(可选)"未落实只提示
  不扣分;主条件实质达成即算落实(措辞/顺序差异不扣)。
- **要点瘦身**:chapter_slice 要点 ≤4 条、每条单主条件,递进要求拆"主条件
  (必须落实)+深化(可选)"显式标注——与评审契约的"可选不扣分"配套。
- **评审模型开思考(per-role)**:DEEPSEEK_THINKING_ROLES 白名单
  (部署配 REVIEWER,OUTLINE)路由到 deepseek_think family(不注
  thinking disabled);全局关思考 flag 保留(写作侧维持关,字数守卫压着)。
  一致性核对/数值推演是推理型任务,关思考退化成字面匹配。工厂注册双
  family:deepseek(跟随全局 flag)+ deepseek_think(固定思考)。
- 测试:白名单路由/双 family body 注入两用例;175 绿 + 契约 KEPT。
  预期:结构闸单轮通过率回升(时间线类 0 成本消失、复合条件拆单、评审
  有推理弹性),仍偏严则下一个旋钮是 7 分阈值线。
