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

- 定稿前:中断点 B 循环内提意见重写
- 定稿后:改写第 N 章 = 第 N 章 v2(v1 归档不删除),N+1..M 章自动标 `stale` 进入连锁重生成队列,用户批量触发;**可随时回退**(v2 归档、v1 恢复、stale 解除;若已重生成需确认丢弃)
- **主线不变式:单一活跃版本链**——任意时刻每章恰有一个活跃版本,不是平行分叉

### 4.4 IF 线(番外)

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

temporal_relations(时间偏序)、entities/entity_links(Wiki 图)、paragraphs(段落下钻)、
volume/book 摘要层、known_partial(部分知晓)

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
