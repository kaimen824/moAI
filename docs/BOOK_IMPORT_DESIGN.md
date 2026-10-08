# 书籍导入与状态重建模块 — 设计稿 v3(操作级,开工依据)

> 状态:**v3 定稿,按 §13 分期开工** · v3 2026-10-08
> 演进:v1 阶段级 → v2 操作级 → v2.2 波式 RAG → **v3 状态重建层并入(评审 12 阻断修法回写 + D10-D13 拍板)**
> 需求(所有者):导入已写好的书,由 Agent 沉淀为平台可直接续写的状态;文本量百万字级
> 本稿为实现唯一依据;改动回写本稿

## 0. v3 变更摘要(相对 v2.2)

| 变更 | 来源 |
|---|---|
| **产物定义升级**:知识抽取 → 状态重建。最终产物是 `get_state_at(story_id, N)` 投影出的 CurrentStoryState,不是"抽了多少条" | 所有者 v3 定性 |
| **新增状态组装层**(§4.9/§5):`get_state_at()` 纯确定性投影函数 + 导入末 Snapshot Artifact(产物身份,非事实源,不建 character_states 表) | D10 拍板 |
| facts 增三型 `possession/location/ability`,`subject_id/object_id` 接实体消歧 | D11 拍板 |
| plot_threads 增 `mentioned_chapters`,派生 recovery_signal(LOW/MEDIUM/HIGH);READY 不成为代码状态 | D12 拍板 |
| **WriterContextContract**:CurrentStoryState ≠ WriterContext,Writer 只消费 POV/认知/相关性投影后的视图(POV 隔离不变式延伸到组装层) | D13 拍板 |
| **M2 判据双层**:State Reconstruction Pass + Continuation Pass(替换"导入完能续写一章") | 所有者拍板 |
| 评审 12 项阻断级修法(B1-B12)+ 高/中严重度修法全部并入对应章节(逐节见 §1-§12 内联标注) | BOOK_IMPORT_REVIEW 终版 |

---

## 目录

1. 目标状态与数据字段级映射
2. 总体架构:双图并存 + 状态组装层
3. 存储设计:staging/进度/DDL 变更
4. 流水线操作级规格(含 4.9 状态组装)
5. 图结构:节点/边/状态/中断点
6. 状态机
7. API 契约
8. SSE 事件契约
9. 前端交互规格
10. 配置项与成本模型
11. 错误处理矩阵 + 测试清单
12. WriterContextContract
13. 分期与目录变更清单
14. 决策点状态表

---

## 1. 目标状态与数据字段级映射

导入完成后,每张表的每一行从哪来:

| 目标表 | 来源操作 | 关键字段映射 |
|---|---|---|
| `stories` | 建 story 时 | title=文件名/用户填;premise=用户填;status 走 §6 状态机 |
| `chapters` | 切分器直写 | chapter_no=切分序;title=章节标题行原文(无题则"第N章");content=章正文;status: staging→active;branch_id=main |
| `chapter_summaries` | 操作 4.3 逐章摘要 | layer='chapter',content=摘要,chapter_no 对应 |
| `chapter_summaries`(stage层) | 操作 4.4 卷聚合 | layer='stage'(**勿写 'volume'**,M14),chapter_no=卷末章号 |
| `outlines` | 操作 4.5 逆向大纲 | version=1,status='confirmed'(用户确认后),content=markdown 大纲 |
| `characters` | 操作 4.7 批角色合并 | name=抽取名;profile=增量合并(后批 append);entity_id 空 |
| `character_intents` | 操作 4.6 批产出 | goal/knows/doesnt_know/self_interest,按批章号区间合并 |
| `facts` | 操作 4.6 批事实抽取 | type 含新三型(见 §3.1);chapter_established=条目级章号(B8');三型带 subject_id/object_id |
| `fact_visibility` | 同上 | knowledge_level 恒 known_full(v1);learned_chapter=所在章;世界级设定 visible_to 处理见 D8(待表态) |
| `plot_threads` | 操作 4.6 同批产出 | planted/resolved 章号(**clamp 到批内 [start,end]**,B8');mentioned_chapters=提及章号列表(D12) |
| `beliefs` | 同上 | 误信类抽取;dispelled 处理见 D1(待表态) |
| `import_tasks` | 全程维护 | 见 §3.2(v3 含 snapshot_json) |
| 主图续写衔接 | 完成时写图 state | **种子七字段**(B1):`{story_id, branch_id, chapters_done:N, outline_confirmed:True, chapter_no:N, master_outline:逆向大纲定稿全文, stage_outline:""}`——中断 II 确认稿 = outlines 表落库 = 种子,**同一字符串三处同步** |

**续写衔接细节**(导入完成 → 工作台点"继续写作"):主图 `route_entry` 读 checkpoint:chapters_done=N>0 → `next_chapter` → chapter_no=N+1 → 无 stage_outline → 视为阶段首章 → 生成细纲(输入=master_outline 种子+story_recap)→ 正常续写。

**v3 修正(替换 v2.2"无需任何续写侧特殊代码")**:需两处主图修正——①route_next 增量语义(H1,具体形态见 D9 待表态);②generate 守卫(B5'②:存在 import_tasks 且 stage∉{done} 即 409)。

**最终产物定义(v3 新增)**:结构化沉淀的终点不是表,而是 `get_state_at(story_id, N)` 的投影输出(§4.9)。所有落表数据是该投影的**事实源**,CurrentStoryState 本身是 **Projection,不是 Source of Truth**(D10)。

---

## 2. 总体架构:双图并存 + 状态组装层

```
                    ┌─ 主图(coauthor→生产循环)   thread_id = story_id
engine(Deps)───────┤
                    └─ 导入管道(本模块)          thread_id = "import:" + story_id

状态组装层(独立于双图,纯函数):
  facts/entities/beliefs/intents/threads/chapters_summaries
                   ↓ get_state_at(story_id, N)   ← 禁 LLM/Embedding/Agent
          CurrentStoryState(JSON)
                   ├─→ 导入末:Snapshot Artifact(中断 III 人审 + Eval fixture)
                   └─→ 续写期:WriterContext 投影(POV/认知/相关性过滤,§12)
```

- **双图共用** engine 全套:Deps/锁/checkpointer/LLMFacade/embedding/SSE 事件总线/协作式中断/agent_traces/usage 埋点——**零新依赖**(M18,替换原"零新基建"措辞)
- **thread 隔离是硬约束**:导入 thread 前缀 `import:`;`_sse_run` 拆参 `(graph_input, graph_thread, broadcast_key)`(B6'),emit 归属/互斥/clear_stop 均用 broadcast_key=story_id;导入事件加 `source:"import"` 防前端串台
- **互斥**:generate/import 启动前查 `_active`(story_id 维度),已在跑 → 409
- **续写前置校验**(B5'② 终版):generate 端点守卫——存在 import_tasks 且 stage∉{done} → 409(read_only 也挡,导去"继续沉淀");主图 thread 残留检查(snapshot next 非空→拒绝)**必须在写种子之前**(H12,顺序钉死)

---

## 3. 存储设计

### 3.1 facts 类型扩展与结构化主体(D11)

```sql
-- type 枚举: event|state|setting|relation + possession|location|ability (v3)
-- 三新型的结构化主体(B8' 签名同步):
ALTER/建表列: subject_id TEXT REFERENCES entities(id),   -- 持有者/所在者/拥有者
              object_id  TEXT REFERENCES entities(id),   -- 物品/地点/能力名
              object_text TEXT                           -- 冗余展示名(免 JOIN 渲染)
```

- 三型语义示例:`possession(subject=林渊, object=青云剑)` / `location(subject=林渊, object=青云城)` / `ability(subject=林渊, object=太虚剑诀·筑基初期)`
- subject/object **经实体消歧后挂 entity_id**(复用 ADR-0015 消歧,抽取输出名字→resolve→id);消歧 uncertain 时 object_text 留痕、id 空(检索降级到文本)
- 版本链/有效区间语义不变:三型同样走 `prev_version_id` + chapter_established,`replay_world` 时点过滤自动覆盖("玉佩已毁"推翻"持有玉佩")
- 落库沿用 apply_fact_changes(supersede_policy 参数,B2')

### 3.2 plot_threads 提及追踪(D12)

```sql
mentioned_chapters TEXT,   -- json 章号列表;导入期由 thread advance/reslove 动作与批摘要提及统计填充
```

- 派生:`last_mentioned = max(mentioned_chapters)`;`recovery_signal` 纯代码计算(§4.9.4)
- **主状态保持 OPEN/RESOLVED 不变**(D12 拍板:READY/INVALIDATED 不进代码状态机;INVALIDATED 仅导入期 LLM 建议标注进 description 尾注,人工在中断 III 确认)

### 3.3 chapters 中间态

status 枚举:`draft|active|stale|archived` + **`staging`**(导入中,阅读器不显示、检索不命中)。DDL 注释变更,无迁移。

### 3.4 import_tasks(进度持久化,服务重启可恢复;v3 含 B4'/H7/D10 列)

```sql
CREATE TABLE IF NOT EXISTS import_tasks (
  id            TEXT PRIMARY KEY,
  story_id      TEXT NOT NULL REFERENCES stories(id),
  stage         TEXT NOT NULL,      -- 见 §6
  paused        INTEGER NOT NULL DEFAULT 0,   -- B10':标志位,不覆盖 stage
  source_text   TEXT,               -- B4':pending 期原文不丢
  source_chars  INTEGER,
  total_chapters INTEGER,
  done_summaries INTEGER,           -- 幂等指针
  total_batches INTEGER,
  done_batches  INTEGER,            -- 幂等指针(UPDATE 与批数据同事务,B3')
  batch_statuses TEXT,              -- H7: json 批级状态 [{no,start,end,status}]
  chapterize_meta TEXT,             -- json:模式/异常清单/用户修正
  outline_draft  TEXT,
  conflicts      TEXT,              -- json:抽样校验冲突报告
  snapshot_json  TEXT,              -- D10:导入末 Snapshot Artifact(见 §4.9.3)
  error          TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
```

### 3.5 ACL seed 增行

`importer`: facts/beliefs/fact_visibility/characters/character_intents/chapters/chapter_summaries/plot_threads/outlines/entities 写域(写路径走编排层单事务,ADR-0013 同款豁免)。

### 3.6 AgentRole 与路由

`AgentRole.IMPORTER = "IMPORTER"`;DEFAULT_MODELS `IMPORTER: "deepseek-v3"`(便宜档默认,大纲/校验/状态归并节点借 SUPERVISOR 强档;usage 归属见 M17)。

---

## 4. 流水线操作级规格

> 每个操作:输入 → 处理 → 输出 → 落库 → 失败处理。LLM 调用全部经 facade。

### 4.1 上传与预检(无 LLM)

- 输入:multipart txt(M1;M3 扩 docx/epub)或 JSON 纯文本;上限 50MB
- 处理:编码探测 utf-8→gb18030→报错;≥2000 字校验;建 story(status=importing)+import_tasks(stage=pending,source_text 落列)+返回 {story_id, task_id, estimated_chapters}(M5:同步跑切分探测)
- 失败:400,不建 story

### 4.2 切分 chapterize(纯代码,无 LLM)

- 模式探测:候选正则计数取最多;卷标题行单独捕获
- 异常检测:章数<3→手工模式(H10:停 stage=chapterized mode=manual,屏 3 空态);单章>3万字→"疑似未切开";<300字→"疑似误切";跳号高亮
- 全部章写 chapters(status='staging')
- **中断点 I 前置**(先确认切分再花摘要的钱)
- 用户修正走 `/import/chapterize-fix`(H10 参数:`{mode, custom_sample, merge:[], split:{chapter_no, offset, sample_line}}`);重切=删 staging 重写

### 4.3 逐章摘要 summarize(便宜模型,并发 3)

- 每章 1 调用:150 字摘要(事件/状态变化/悬念)+出场角色名单;超 2.5 万字两跳摘要(M7 统一阈值)
- 落库:chapter_summaries(layer=chapter);角色名单入 import_tasks.chapterize_meta(json,M2)
- 幂等:已存在摘要的章跳过;done_summaries 推进;每章 emit import_progress
- 失败:重试 1 次→标记 failed 继续,汇总报告不阻塞

### 4.4 卷聚合 + 全书概要

- 卷聚合:按**真实卷边界**(M6,4.2 已捕获)或 25 章兜底,便宜模型,cap ~400 字/条(recap 渲染截断)
- 全书概要(强模型):~1000 字,主线/世界观/力量体系/主要人物/当前剧情位置;幂等键补(B10')
- **NarrativeState 统计层(v3,D13 前置,纯代码零 LLM)**:章均字数/对话行比例/POV 归属分布(章首第三人称有限视角检测复用 ADR-0041 认知主体逻辑)/章末钩子类型统计(末段启发式分类:悬念/升级/揭示)——存 import_tasks.chapterize_meta.narrative_stats,风格层(节奏/文风)归 D3 待表态

### 4.5 逆向大纲 outline(强模型)→ ★中断点 II

- 输入:全书概要+首/尾各 3 章摘要+narrative_stats
- 输出 markdown(与 GenMasterOutline 同构):已写部分按卷+**续写方向建议**(下卷 5-8 beat,基于尾部悬念)
- 中断点 II:用户可直接编辑大纲文本再确认(resume 带修正稿)或 revise 重生成(**上限 3 轮**,M12);确认稿 = outlines 落库 = 主图种子 master_outline,**三处同一字符串**(B1)
- **伏笔-outline 关联(可选增强,D12 配套)**:确认时 LLM 建议活跃伏笔与续写 beat 的关联,写入 threads.description 尾注(供 recovery_signal 参考,不参与硬状态)

### 4.6 批事实抽取 extract_batch(核心;波式并行 + 波间 RAG)

> 执行模型:波(wave)=**并发调度单位**;**批=事务+幂等单位**(B3' 终版)。import_tasks 为唯一 SSOT,done_batches 的 UPDATE 与批数据同事务;恢复从 done_batches+1 续,同波已落库批跳过;落库异常重放内存变更集,服务重启重抽(计成本)。

```
for wave in waves(IMPORT_WAVE_SIZE=4):
    并行启动波内各批,每批上下文:
      输入 = 批内原文(staging 读,批窗 5 章 或 ≤4 万字孰先,B11;dropped 溢出标记)
           + [已知设定](确定性检索,本波开始时刷新):
               主路:批内出场角色名 → facts ⋈ fact_visibility 直查(精确)
               兜底:批摘要 embedding → 向量召回 top-15(推翻链过滤,当前有效版)
           + [前情] 前 N 章摘要聚合 + [后情] 后 N 章摘要
    波完成 → 各批独立单事务落库(顺序 commit)→ 下一波
全部波完成后:
  ① setting 全局确定性归并(B2'②):全量扫描 setting(超长按章号分段),按章号序喂
     薄归并 LLM 一次裁决继承链 → 建议 supersede → 中断 III 确认(无绕过用户建链路径)
  ② 同波重复候选归并(H3):同 type+章号距离≤波窗+bigram Jaccard/专名数字 token 预筛
     (纯 Python;余弦仅候选内部排序不作门槛)→ 薄归并 LLM 裁决 → 中断 III 确认
```

- 每批输出 json(单次调用产全批变更):
  ```
  { facts: [{content, type: event|state|setting|relation|possession|location|ability,
              subject?, object?, confidence, visible_to:[角色名], supersedes?, chapter}],
    beliefs: [{character, content}],
    character_updates/character_intents: [...],
    threads: [{description, action: plant|advance|resolve, chapter}],
    mentioned: [{thread_desc, chapter}] }   // D12:提及追踪
  ```
- **三型抽取教学(D11)**:POSSESSION(谁持有什么物)/LOCATION(谁在什么地点)/ABILITY(什么境界/功法/技能);条目 ≤40 字,subject/object 用正名;**与[已知设定]语义相同的不要重复抽取**;世界级设定 visible_to 填法见 D8(待表态)
- 输出预算:总数 ≤60 条软约束(M8),溢出 dropped 标记
- 批落库(单事务,ADR-0013 同款):apply_fact_changes(**签名:条目级 chapter 覆盖+facts 向量注入+supersede_policy**;导入禁 setting 链尾兜底,B2'①)+visibility+characters/intents 合并+threads(**章号 clamp 批内**,B8')+mentioned_chapters 累积
- 失败:parse_json_loose→重试 1 次→failed 继续,汇总报告
- check_stop 在**波调度边界**调用并重抛(线程池内传播,B7');暂停=worker except StopRequested 落 paused=1(stage 保留)
- wall-clock(终版口径,H11):300 章 60 批/波4 = 15 波,全程 1.2-2.5h(见 §10)

### 4.7 抽样校验 verify(强模型)→ ★中断点 III 前半

- 分层随机 5 章(头 1/中 2/尾 2);每章 1 调用
- 输入:该章原文+chapter_established 区间有效 facts(经 replay_world)+角色卡
- 输出:`{conflicts:[{fact_content, chapter_text_evidence, issue}], missing:[]}`
- 冲突入 import_tasks.conflicts;不静默修正;话术"5 章冒烟,不保证全库无误"(M10)

### 4.8 结果确认 ★中断点 III → 定稿

- 中断卡呈现:冲突报告逐条(采纳记忆/按原文修正)+角色卡预览(**含碎卡合并 UI**,M19)+伏笔勾选(按 planted 章号批量,M9 默认一键采纳+按置信度分组)+**Snapshot Artifact 预览(§4.9.3,D10)**+导入统计
- 用户裁决 resume:`{action: confirm, fact_fixes, threads, character_fixes}`
- 定稿事务:修正应用+chapters staging→active 批量 UPDATE+stage=done+story.status=active+snapshot_json 定稿
- **主图种子写种**(顺序钉死):①主图 thread 残留检查(snapshot next 非空→拒绝/审计)→②`graph.update_state(cfg, 七字段)`(不跑图)
- 放弃导入入口(B4'④):仅当 chapters 全部 staging 可用;已翻转 active 的书不提供

### 4.9 状态组装层 get_state_at(D10,v3 新增)

#### 4.9.1 纯确定性约束(硬边界)

`get_state_at(story_id, chapter_no)` **禁止 LLM/Embedding/Agent/Tool loop**。State Reconstruction Test 测的是知识沉淀系统,不是模型。检索性消费(embedding 召回)只发生在 WriterContext 投影层(§12)。

#### 4.9.2 输出模式 CurrentStoryState

```
CurrentStoryState(story_id, chapter_no,
  world_state,          # replay_world(upto=N):有效 facts/规则(链过滤)
  characters,           # 角色卡 + intents(goal/knows/doesnt_know/self_interest @N)
  relationships,        # type=relation 有效集
  character_knowledge,  # fact_visibility @N(谁知道什么)
  character_beliefs,    # beliefs @N(谁误信什么,未 dispelled)
  locations,            # type=location 有效集(每人当前位置)
  possessions,          # type=possession 有效集(每人持有物)
  abilities,            # type=ability 有效集(境界/功法)
  plot_threads,         # 状态+mentioned_chapters+recovery_signal
  active_conflicts,     # intents 中未了结的敌对/目标冲突聚合
  outline_position,     # 当前卷/beat 位置(逆向大纲结构化锚点)
  narrative_state)      # §4.4 统计层(章均字数/对话比/POV 分布/章末钩子)
```

实现落点:`app/memory/state_projection.py`,数据源=现有各表,**不加事实表**(D10)。

#### 4.9.3 Snapshot Artifact

导入定稿前 `get_state_at(story_id, N_final)` 产出 JSON,身份=**导入产物/审核产物/Eval fixture**,存 import_tasks.snapshot_json。**不是新的数据库事实源,无双真相**。续写期 Writer 消费永远走 §12 投影,不读 snapshot。

#### 4.9.4 recovery_signal(D12)

```
age = N - last_mentioned(mentioned_chapters 最大值)
density = |mentioned ∩ [N-10, N]|          # 近 10 章提及密度
signal: age/density 按 tier 分档映射 LOW/MEDIUM/HIGH
默认阈值(可配 THREAD_RECOVERY_*): short: MEDIUM≥5 或 HIGH≥12;long: MEDIUM≥15 或 HIGH≥30
```

HIGH ≠ READY:仅"值得 Writer/Reviewer 注意";**"现在应该回收"是 LLM 语义判断**(thread_reviewer 职责不变)。outline 关联(§4.5)作 hint 附注。

---

## 5. 图结构(导入管道)

```
upload_validate → chapterize → interrupt_I(确认/修正/止步read_only)
  → summarize(节点内循环+波界 check_stop)→ volume_rollup → book_digest
  → gen_outline → interrupt_II(确认/改/revise≤3)
  → extract_batch(节点内波循环)→ 归并 passes → sample_verify → interrupt_III(裁决)
  → finalize_import(种子写种+snapshot 定稿)→ END
```

- 全部节点入口挂 deps.check_stop(复用 _node 包装);**循环节点=节点内 Python 循环**(B7'),recursion_limit 按章数推导(300 章书 ~40)
- ImportState(TypedDict,v2.2 重写,M2):story_id/branch_id/task_id/summaries 缺口集合/outline_draft/conflicts/user_input/error。**chapters_meta/角色名单出 state**(入 import_tasks json 列);**原文永不入 state**(staging 表按需读);state 常态 <10KB

## 6. 状态机

```
story.status:  (新建)importing ──finalize_import──> active
                   └──finalize_readonly──> active(import_tasks.stage=read_only)

import_tasks.stage 流转:
pending → chapterized(等中断I)
       → summarizing → digest_done
       → outline_pending(中断II)→ outline_confirmed
       → codex_running → verify_pending(中断III)→ done
       → read_only(止步)
失败:error 列记录;**paused 是标志位非 stage 值**(B10'),stage 保留原值
恢复:POST /import/continue(无 body)按 stage+指针续跑(B4');start 对 stage=pending 幂等
read_only → summarizing 转移合法(触发=继续沉淀);期间 story.status 不翻转,
           角标与守卫一律读 import_tasks(M13 同源)
```

## 7. API 契约

| 端点 | 方法 | 请求 | 响应/错误 |
|---|---|---|---|
| `/stories/import` | POST | multipart:file+title?+premise?+copyright_claim:true | 200 **JSON** {story_id, task_id, estimated_chapters}(H6:不返 SSE);400 编码/过短/未勾声明 |
| `/stories/{id}/import/start` | POST | — | SSE 流(H6 拆两步);409 已有 running 任务(收窄,B4'③) |
| `/stories/{id}/import/continue` | POST | —(B4'②) | SSE 流,按 stage+指针续跑 |
| `/stories/{id}/import/chapterize-fix` | POST | {mode, custom_sample?, merge?, split?} | 200 重切结果;409 非中断I态 |
| `/stories/{id}/import/resume` | POST | 按 stage 校验 action 白名单(M4);III 带 fact_fixes/threads/character_fixes | SSE 流 |
| `/stories/{id}/import/progress` | GET | — | {stage,paused,done_summaries/total,done_batches/total,**batches[]**(H7),waiting,interrupt,outline_draft(H9),costs,error} |
| `/stories/{id}/import/state` | GET | ?chapter_no=N(v3) | CurrentStoryState JSON(§4.9;中断 III 屏与调试消费) |
| `/stories/{id}/stop` | POST | 复用 | 波/章边界退出(≤1-2min,B7' 话术),paused=1 |
| `/stories/{id}/generate` | POST | 复用 | **409**:存在 import_tasks 且 stage∉{done}(B5'②) |

## 8. SSE 事件契约

```
import_stage    {stage, label}
import_progress {kind:"summary", done, total, chapter_no, title}
import_batch    {done, total, batch_no, chapters:[起,止], facts_n, threads_n, status:"ok"|"failed"}   # H7
import_cost     {calls, tokens_in, tokens_out}        # 每 5 次调用滚动
interrupt       {type:"import_confirm_structure"|"import_confirm_outline"|"import_confirm_result", source:"import", ...}   # B6' 防串台
agent_call / stopped / error / done                    # 复用;done/interrupt 带 source:"import"
```

## 9. 前端交互规格(7 屏)

1. **书库·导入入口**:「导入旧作」按钮 → 向导;importing 书角标「继续/放弃导入」(放弃仅 staging 全存时可用)
2. **上传屏**:拖拽/粘贴;书名/简介预填;**版权声明勾选必填**;成本+时长并列实时估算(H11 终版口径)
3. **切分确认屏(中断I)**:统计条+章节列表虚拟滚动+异常高亮+快捷修复;**手工模式空态**(H10);三出口:确认沉淀/重切/**仅导入阅读**
4. **进度屏**:五阶段步进条(12 态→5 步映射表,M3);**批次点阵**(batches[],H7);滚动成本条;暂停按钮(暂停后界面:显示波边界退出话术)
5. **大纲确认屏(中断II)**:可编辑文本域+续写方向高亮;确认/重写(≤3)
6. **结果确认屏(中断III)**:冲突逐条卡+角色卡(**含合并 UI**,M19)+伏笔按组勾选(M9 默认一键采纳)+**Snapshot Artifact 预览**(状态重建结果人审)+统计;CTA「完成导入,开始续写」
7. **完成态**:跳工作台;read_only 书「继续沉淀」角标(M13 数据源=import_tasks)

状态联动:导入中隐藏生成按钮;progress 轮询(10s)+SSE 双通道。

## 10. 配置项与成本模型(终版口径,H11)

| 配置 | 默认 | 说明 |
|---|---|---|
| IMPORT_BATCH_CHAPTERS | 5 | 批窗(章数维度) |
| IMPORT_BATCH_MAX_CHARS | 40000 | 批窗(字符维度,孰先,B11) |
| IMPORT_WAVE_SIZE | 4 | 波=并发调度单位(=1 退化串行) |
| IMPORT_SUMMARY_CONCURRENCY | 3 | 摘要并发 |
| IMPORT_VOLUME_SIZE | 25 | 卷聚合兜底窗口(优先真实卷边界) |
| IMPORT_VERIFY_SAMPLES | 5 | 抽样章数 |
| IMPORT_MAX_FILE_MB | 50 | 上传上限 |
| THREAD_RECOVERY_SHORT_MID/HIGH | 5/12 | recovery_signal short 档阈值(D12) |
| THREAD_RECOVERY_LONG_MID/HIGH | 15/30 | long 档阈值 |

**300 章/100 万字终版预估**:模型调用 ≈480-530(便宜 ~90%;embedding 另 ~80 次);tokens 输入 ≈220-280 万(≈2.2-2.6× 全文),输出 ≈30-50 万;费用 ¥5-15(中位 ¥8-12);时长 **1.2-2.5h**(DashScope 低档 TPM 限流账号 3-5h);用户在场 4 时点,动手 15-45 分钟。三条预期管理话术见评审报告 §一。

## 11. 错误处理矩阵 + 测试清单

| 故障 | 处理 |
|---|---|
| 编码不可识别 | 上传即 400 |
| 切分 <3 章 | 手工模式(用户给分隔样例) |
| LLM 空输出/JSON 失败 | 重试 1 次→failed 继续,汇总报告 |
| 单章超窗口 | 两跳摘要 |
| 批落库中途异常 | 批事务回滚,重放内存变更集;重启重抽(计成本) |
| 服务重启 | import_tasks+staging 持久,continue 续跑 |
| 用户协作中断 | 波/章边界退出,paused=1 |
| 重复导入/导入中续写 | 409 |
| embedding 失败 | 降级继续,归并前批量补算(M1) |
| 中断 III 弃置 | 冲突条目可弃置,留痕 |
| 僵尸 task | start 幂等+放弃入口解决,不做超时 cron |

**测试清单**:
- 切分器 8 例(标准/卷章/无题/跳号/超长/GBK/空/手工模式)
- 摘要幂等跳过/批事务原子性(注入中断)/setting 链跨批衔接(B2' 导入语境)
- 状态机全路径(含 read_only+补沉淀+放弃回流封死)
- 成本公式/双图 thread 隔离/**B1 种子七字段测试**(导入完立跑主图续写一章)
- **B8' embed 形状回归**(主图 embed 调用形状不变)
- **B5' 守卫矩阵**(含 read_only 挡 generate/放弃回流)
- **get_state_at 确定性测试**(同输入同输出;禁 LLM 断言)
- **三型版本链时点测试**("玉佩已毁"后 get_state_at(N) 持有物不含玉佩)
- **recovery_signal 纯代码测试**(阈值映射)
- **State Reconstruction Pass**(M2 判据第一层,§13)
- **Continuation Pass**(M2 判据第二层)
- e2e 回放 3 章迷你书

## 12. WriterContextContract(D13,v3 新增)

**CurrentStoryState ≠ WriterContext**。Writer 永远不直接消费组装层全量输出:

```
CurrentStoryState(全量真相投影)
   ↓ POV projection(可见性矩阵过滤,不变式:Writer 不直接消费 Truth)
   ↓ character knowledge filtering(认知边界,ADR-0041 认知主体)
   ↓ relevance retrieval(现有 retrieve_for_chapter:结构化主路+向量兜底)
   ↓ current chapter constraints(本章要点/字数/上期衔接)
WriterContext → WriterNode
```

- 现有 POV 泄漏测试口径(0/1345)**延伸覆盖本边界**:组装层引入的任何新数据通道(三型 facts/narrative_state/recovery_signal)都必须过投影才能进 Writer prompt
- recovery_signal 进入 WriterContext 的形态:活跃伏笔清单附注(供"藏或收"的注意信号),不构成指令

## 13. 分期与目录变更清单

| 期 | 内容 | 判据(v3) |
|---|---|---|
| M1 | 4.1-4.4 + 中断点I + read_only + staging/进度/SSE/进度屏 + **generate 守卫提前**(H2) | 大书导入**能读**,断点续跑 |
| M2 | 4.5-4.6 + 中断点II + 种子 + **三型/提及追踪/组装层 get_state_at + Snapshot Artifact + State Reconstruction Pass + Continuation Pass** | **双层判据**:①State Reconstruction Pass——GT(人工标注第 N 章末状态断言集:人物/位置/持有物/能力/认知/关键规则/主线/伏笔)vs get_state_at() 逐维 diff 达标;②Continuation Pass——从重建状态续写 N+1,无状态穿帮/POV 泄漏/认知越界/伏笔乱回收。GT 基准书:古真神(40 章,已有全文+完整库) |
| M3 | 4.7-4.8 + 中断点III(含 Snapshot 人审/角色合并 UI)+ 状态机收尾 | 全流程闭环 |
| M4 演进 | docx/epub、实体沉淀增强、numpy 余弦(B9')、文风层(D3) | 按需 |

**State Reconstruction Eval 落点**:`evals/run_state_recon_eval.py`(与现有 evals 体系同构;GT fixture 存 evals/fixtures/古真神_state_gt.json)

```
新增文件:
app/graph/import_pipeline/{__init__,state,build}.py
app/graph/import_pipeline/agents/importer.py        # 节点族
app/memory/state_projection.py                      # D10:get_state_at/CurrentStoryState/recovery_signal
app/db/ddl.py                                       # import_tasks 表+facts 三型列+threads.mentioned_chapters+staging
app/db/seed.py                                      # importer ACL
app/core/config.py                                  # AgentRole.IMPORTER+THREAD_RECOVERY_*
app/main.py                                         # 端点族+_sse_run 拆参(B6')+409 守卫(B5'②)
frontend/src/components/ImportWizard.jsx            # 7 屏状态机
frontend/src/api.js                                 # import API 族
evals/run_state_recon_eval.py                       # M2 双层判据第一层
复用(不改):emit/stop/trace 基建、GenMasterOutline 同构、extract_facts prompt 骨架、
           replay_world/intents/实体消歧/POV 投影(组装层数据源与投影地基)
重构 1 处:runtime.commit_finalize 的 facts 落库段抽为
          apply_fact_changes(conn, story_id, branch, changes, *, chapter 覆盖, supersede_policy)
          (主图行为不变+embed 形状回归测试)
```

## 14. 决策点状态表

### 本轮已拍板(v3,2026-10-08)

| # | 决策 | 结论 |
|---|---|---|
| D10 | CurrentStoryState 物化 vs 推导 | **C:推导为唯一事实源**(get_state_at 纯确定性,禁 LLM/Embedding/Agent);导入末物化 **Snapshot Artifact**(产物/审核/Eval fixture 身份,存 import_tasks.snapshot_json,**不是新事实源,无双真相**);不建 character_states 表 |
| D11 | fact type 细分 | 只加 **possession/location/ability** 三型;subject/object 经实体消歧挂 entity_id;不再扩 |
| D12 | 伏笔状态机 | 主状态保持 OPEN/RESOLVED;加 **mentioned_chapters** 列;派生 **recovery_signal(LOW/MEDIUM/HIGH)**(age+近 10 章密度,tier 分档);**READY 不成为代码状态**,"该收了"仍归 LLM 语义判断 |
| D13 | State→Writer 边界 | **CurrentStoryState ≠ WriterContext**;Writer 必经 POV/认知过滤/相关性检索/本章约束四层投影;POV 泄漏测试口径延伸覆盖新数据通道 |
| M2 判据 | 验收标准 | 双层:**State Reconstruction Pass**(GT vs get_state_at 逐维 diff)+ **Continuation Pass**(续写 N+1 无穿帮/泄漏/越界/乱收);GT 基准=古真神 |

### 待表态(不阻塞 M1;各阻塞点如下)

| # | 问题 | 阻塞什么 |
|---|---|---|
| D1 | beliefs 的 dispelled:(a) 抽取直出 vs (b) 归并 pass 确定性比对 | M2 的 beliefs 抽取 schema(§4.6) |
| D2 | 导入期低置信事实:(a) 一律 confirmed 留痕 vs (b) pending 队列隔离(检索行为零差异已查证) | M2 的 §4.6 confidence 处理(倾向 a,改动面≈0) |
| D3 | 文风:(a) 不做+话术 / (b) few-shot 尾章 / (c) 文风笔记 | §4.4 narrative_state 风格层与 WriterContext 注入 |
| D4 | 波式 vs 全并行+全局归并 | 若 wall-clock 成投诉点再动,当前维持波式 |
| D5 | codex 导入后展示:遮罩/分组+搜索/现状 | M3 的中断 III 呈现细节 |
| D6 | 中断 III 默认交互:一键采纳 vs 逐条 | M3 前端 |
| D8 | 世界级设定可见性:(a) 导入 prompt 强制全员(爆炸半径锁导入) vs (b) 检索层 OR 分支(改变全部续写检索) | M2 的 §4.6 visible_to 教学(倾向 a) |
| D9 | route_next 增量语义:(a) 端点注入基线 vs (b) 图内真增量(须回改 1 老测试) | M2 的 B1 种子衔接(H1 主图既有 bug) |
