# 书籍导入与沉淀模块 — 完整设计稿 v2(操作级,待评审)

> 状态:**评审中,未实现** · v2 2026-09-07(v1 阶段级 → v2 操作级)
> 需求(所有者):导入已写好的书,由专门 Agent 沉淀为平台可直接续写的状态;文本量百万字级
> 本稿为实现唯一依据;评审通过后按 §12 分期执行,改动回写本稿

---

## 目录

1. 目标状态与数据字段级映射
2. 总体架构:双图并存与隔离
3. 存储设计:staging/进度/DDL 变更
4. 流水线操作级规格(每步:输入→处理→输出→落库→失败处理)
5. 图结构:节点/边/状态/中断点
6. 状态机(story.status × import_tasks.stage)
7. API 契约(端点/字段/错误码)
8. SSE 事件契约
9. 前端交互规格(界面/按钮/状态)
10. 配置项与成本模型
11. 错误处理矩阵 + 测试清单
12. 分期与目录变更清单
13. 评审决策点

---

## 1. 目标状态与数据字段级映射

导入完成后,每张表的每一行从哪来:

| 目标表 | 来源操作 | 关键字段映射 |
|---|---|---|
| `stories` | 建 story 时 | title=文件名/用户填;premise=用户填;status 走 §6 状态机 |
| `chapters` | 切分器直写 | chapter_no=切分序;title=章节标题行原文(无题则"第N章");content=章正文;status: staging→active;branch_id=main |
| `chapter_summaries` | 操作 4.2 逐章摘要 | layer='chapter',content=摘要,chapter_no 对应 |
| `chapter_summaries`(stage层) | 操作 4.3 卷聚合 | layer='stage',chapter_no=卷末章号 |
| `outlines` | 操作 4.5 逆向大纲 | version=1,status='confirmed'(用户确认后),content=markdown 大纲 |
| `characters` | 操作 4.7 批角色合并 | name=抽取名;profile=增量合并(后批 append);entity_id 空 |
| `facts` | 操作 4.6 批事实抽取 | type/confidence/visible_to/supersedes 同现有语义;chapter_established=批内该事实所在章 |
| `fact_visibility` | 同上 | knowledge_level 恒 known_full(v1);learned_chapter=所在章 |
| `plot_threads` | 操作 4.6 同批产出 | planted_chapter/resolved_chapter=识别出的章号;status=open/resolved |
| `beliefs` | 同上 | 误信类抽取(如"李四以为张三已死") |
| `import_tasks` | 全程维护 | 见 §3.2 |
| 主图续写衔接 | 完成时写图 state | chapters_done=N、outline_confirmed=True、chapter_no=N、stage_outline 清空(续写第N+1章时按边界重新生成细纲) |

**续写衔接细节**(导入完成 → 工作台点"继续写作"会发生什么):主图 `route_entry` 读 checkpoint:chapters_done=N>0 → `next_chapter` → chapter_no=N+1 → 无 stage_outline → 视为阶段首章 → 生成细纲(输入=逆向大纲+story_recap,recap 里已含全部章摘要/卷聚合/细纲进度指针)→ 正常续写。**无需任何续写侧特殊代码**。

---

## 2. 总体架构:双图并存与隔离

```
                    ┌─ 主图(coauthor→生产循环)   thread_id = story_id
engine(Deps)───────┤
                    └─ 导入管道(本模块)          thread_id = "import:" + story_id
```

- **双图共用** engine 全套:Deps/锁/checkpointer(LLGraph SqliteSaver 按 thread 隔离)/LLMFacade/embedding/SSE 事件总线/协作式中断(stop_requests)/agent_traces 落库/usage 埋点——零新基建
- **thread 隔离是硬约束**:主图与导入图 state schema 不同,同 thread 会互相污染 checkpoint。导入 thread 前缀 `import:`,SSE 订阅/事件按 story_id 广播不变(emit 归属 story thread,_current_thread 用 story_id,与图 thread 解耦——现有 emit 与 cfg thread 是两回事,不需改)
- **互斥**:generate/import 启动前查 `_active`(story_id 维度),已在跑 → 409;导入中前端隐藏"继续写作"按钮
- **续写前置校验**:generate 端点加守卫——story.status=='importing' → 409 "导入未完成"(read_only 除外,见 §6)

## 3. 存储设计

### 3.1 chapters 增加中间态

status 枚举扩展:`draft|active|stale|archived` → 增加 **`staging`**(导入中,阅读器不显示、检索不命中)。DDL 注释变更,无迁移。

### 3.2 新表 import_tasks(进度持久化,服务重启可恢复)

```sql
CREATE TABLE IF NOT EXISTS import_tasks (
  id            TEXT PRIMARY KEY,
  story_id      TEXT NOT NULL REFERENCES stories(id),
  stage         TEXT NOT NULL,      -- 见 §6 状态机
  source_chars  INTEGER,            -- 原始字数(成本估算基数)
  total_chapters INTEGER,
  done_summaries INTEGER,           -- 已摘要章数(幂等指针)
  total_batches INTEGER,
  done_batches  INTEGER,            -- 已沉淀批数(幂等指针+断点)
  chapterize_meta TEXT,             -- json:分隔模式/异常清单/用户修正
  outline_draft  TEXT,              -- 逆向大纲稿(中断点II的编辑底稿)
  conflicts      TEXT,              -- json:抽样校验冲突报告
  error          TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
```

### 3.3 ACL seed 增行

`importer`: facts/beliefs/fact_visibility/characters/chapters/chapter_summaries/plot_threads/outlines 写域(写路径走编排层单事务,ADR-0013 同款豁免;ACL 行为未来多 Agent 复用预留)。

### 3.4 AgentRole 与路由

- `AgentRole.IMPORTER = "IMPORTER"`;DEFAULT_MODELS 增加 `IMPORTER: "deepseek-v3"`(便宜档为默认,大纲/校验节点按 stage 显式升强模型:节点内用 `self.llm.chat(AgentRole.SUPERVISOR, ...)` 借强档,不新增"IMPORTER-strong"角色——路由表保持一角色一档,节点选角色)

---

## 4. 流水线操作级规格

> 每个操作:输入 → 处理 → 输出 → 落库 → 失败处理。LLM 调用全部经 facade(trace/usage 白拿)。

### 4.1 上传与预检(无 LLM)

- 输入:multipart 文件(txt;M1 仅 txt,M3 扩 docx/epub)或 JSON 纯文本;上限 50MB
- 处理:
  1. **编码探测**:utf-8 试解 → 失败 gb18030 → 失败报错"请转存 UTF-8 后重传"
  2. 基础校验:非空、≥2000 字(否则"内容太短,无需导入,建议直接共创")
  3. 建 story(status=importing)+ import_tasks(stage=pending)+ 返回 story_id
- 失败:400,不建 story

### 4.2 切分 chapterize(纯代码,无 LLM)

- 处理:
  1. **模式探测**:候选正则各计数,取命中最多者:
     `第[一二三四五六七八九十百千万零两\d]+[章回节卷][^\n]{0,40}`、`Chapter\s+\d+`、`^\d+[\.、\s]`
  2. 切分 → `[{no, title, text}]`;**卷标题行**(`第X卷`)单独捕获,用于卷分组建议
  3. **异常检测**:
     - 章数 <3 → 判定失败,进手工模式(用户在界面输入分隔符样例)
     - 单章 >3 万字 → 标记"疑似未切开",高亮给用户
     - 单章 <300 字 → 标记"疑似误切"
     - 章号跳号/重复 → 高亮
  4. 全部章写 `chapters(status='staging')`;chapterize_meta 记录模式与异常清单
- **中断点 I 前置**:切分结果+异常清单给用户(见 §9 界面 3),用户可:合并/拆分指定章、换分隔符重切、直接确认
- 失败:纯代码无外部故障;用户修正指令走 `import/chapterize-fix` 端点重切(重切=删 staging 重写)

### 4.3 逐章摘要 summarize(便宜模型,并发 3)

- 输入(每章):`[第N章 原文]`(≤2.5万字;超限→两跳:对半分块各自摘要→合并)
- prompt 要点:150 字摘要(事件/状态变化/悬念)+ **出场角色名单**(json)
- 输出 json:`{summary, characters:[...]}`
- 落库:chapter_summaries(layer=chapter);characters 名单暂存 import_tasks.chapterize_meta 卷册?——存内存 state(名单小,几KB,可进 state)
- **幂等**:开跑前查已存在摘要的章,跳过;done_summaries 推进
- 并发:信号量 3;每完成 1 章 emit `import_progress`
- 失败:空输出重试 1 次;仍失败标记该章 failed 继续,**汇总报告**(不阻塞)
- 成本:每章 1 调用

### 4.4 卷聚合 + 全书概要(便宜→强)

- 卷聚合:每 25 章一条(便宜模型,输入=25 条章摘要)→ chapter_summaries(layer=stage,chapter_no=卷末章)
- 全书概要(强模型):输入=全部卷聚合 → 输出 ~1000 字:主线/世界观/力量体系/主要人物/当前剧情位置。存 import_tasks.outline_draft 上下文部分
- 失败:同 4.3;卷聚合幂等按 layer=stage 已存在跳过

### 4.5 逆向大纲 outline(强模型)→ ★中断点 II

- 输入:全书概要 + 首/尾各 3 章摘要 + 活跃伏笔暂无(此时还没抽)
- 输出 markdown(与 GenMasterOutline 同构,便于续写复用):已写部分按卷(每卷:范围/核心事件/结局状态)+ **续写方向建议**(下一卷的 5-8 个 beat,基于尾部悬念)
- 中断点 II:用户可**直接编辑大纲文本**再确认(resume 带修正稿),或 revise 让 AI 重生成(带意见)
- 确认后落 outlines(version=1,confirmed)

### 4.6 批事实抽取 extract_batch(核心,便宜模型,**波式并行 + 波间 RAG 设定召回**,v2.2)

> v2.2 修订(所有者提议的 RAG 方向):批间上下文供给从"LLM 维护的设定卡"
> 改为**确定性检索已抽取 facts**——符合项目哲学(DESIGN_FINAL:记忆检索确定性,
> 不引入 LLM 记忆 Agent);零幻觉、零卡片维护成本,且与续写期 writer 消费
> facts 的方式**同构**(结构化主路+向量兜底),沉淀与消费自洽。
>
> 执行模型:批按"波(wave)"执行——波内并行(互不依赖),波间检索刷新
> (每波开始,对本波各批召回当前有效设定)。演进型设定天然正确:
> 召回走推翻链过滤,拿到的自动是最新有效版(优于任何冻结卡)。

```
for wave in waves(batch_size=IMPORT_WAVE_SIZE, 默认4):
    并行启动波内各批,每批上下文:
      输入 = 批内原文(staging 读)
           + [已知设定](确定性检索,本波开始时刷新):
               主路:批内出场角色名 → facts ⋈ fact_visibility 直查(精确)
               兜底:批摘要 embedding → 向量召回 top-15(推翻链过滤,当前有效版)
           + [前情] 前 N 章摘要聚合 + [后情] 后 N 章摘要(伏笔回收识别,阶段1产物)
    波完成 → 变更集按批号顺序落库(链/合并确定性)→ 下一波
全部波完成后:薄归并 pass(仅波内并行的批间漂移窗口):
    对同波批的 state/setting/relation facts 做重复/矛盾整理 → 建议 supersede
    (便宜模型,~⌈波内冲突候选/50⌉ 次),产出待确认清单并入中断点 III
```

- **批窗口**:5 章/批(IMPORT_BATCH_CHAPTERS);**波大小**=并发度(IMPORT_WAVE_SIZE,默认4;=1 退化为串行)
- 每批输出 json(单次调用产全批变更,同上下文一致性最好):
  ```
  {
    facts: [{content, type, confidence, visible_to:[角色名], supersedes?}],
    beliefs: [{character, content}],
    characters_new: [{name, profile 一行卡}],
    character_updates: [{name, profile_append}],
    threads: [{description, action: plant|advance|resolve, chapter}]
  }
  ```
  输出预算控制:提示"条目精炼,每条 ≤40 字;总数 ≤60 条;与[已知设定]语义相同的不要重复抽取"
- **批落库(单事务,ADR-0013 同款)**:facts(去重/supersedes 链/setting 场景链复用公共函数 apply_fact_changes)+visibility+characters 合并+threads+批聚合摘要(每批第 2 次调用,输入=批内章摘要)
- **幂等/断点**:done_batches 指针;恢复时已完成波跳过、断波整波重跑(波=事务边界)
- 失败:单批 JSON 解析失败 → parse_json_loose → 重试 1 次 → 标记 failed 继续,汇总报告
- emit `import_batch {done, total, batch_no}`
- wall-clock:300章 60 批 / 波4 = 15 波 ≈ 15-20min;对比纯串行 ~1h
- 召回 miss 兜底:网文设定词(金手指名/角色名)高频复现,主路实体名直查命中率极高;
  仍 miss 的(本批首次引入新概念)本就无需旧设定,原文自足
- 风险与退化:设定演进极密集的书(如无限流每副本换规则)可 IMPORT_WAVE_SIZE=1 串行,
  RAG 召回仍在(等效滚动检索,无卡片)

### 4.7 抽样校验 verify(强模型)→ ★中断点 III 前半

- 抽样:分层随机 5 章(头部 1/中部 2/尾部 2)
- 每章 1 调用:输入=该章原文 + 该章 chapter_established 区间的有效 facts + 角色卡;输出 json:`{conflicts:[{fact_content, chapter_text_evidence, issue}], missing:[重要但未沉淀的设定]}`
- 冲突清单入 import_tasks.conflicts;**不静默修正**

### 4.8 结果确认 ★中断点 III → 定稿

- 中断卡呈现:冲突报告(逐条:采纳记忆/按原文修正)+ 角色卡预览(可编辑)+ 伏笔台账(勾选确认,复用主图伏笔人工确认模式)+ 导入统计(章数/记忆条数/角色数/伏笔数/成本)
- 用户裁决 resume:`{action: confirm, fact_fixes:[{id,content}], threads:[...], character_fixes:[...]}`
- 定稿事务:修正应用 + **chapters staging→active 批量 UPDATE** + import_tasks.stage=done + story.status=active + **写主图 checkpoint 种子**(见下)
- **主图衔接写种**:向主图 thread(story_id)invoke 一次空转 state 种子 `{story_id, branch_id, chapters_done:N, outline_confirmed:True, chapter_no:N}`——技术上:用主图 graph.update_state(cfg,{...}) 直接写 checkpoint(LangGraph 支持),不跑图。这是导入→续写衔接的唯一桥梁

---

## 5. 图结构(导入管道)

```
节点:upload_validate → chapterize → summarize(循环)→ volume_rollup → book_digest
      → interrupt_I(确认/修正切分/止步read_only)
      → gen_outline → interrupt_II(确认/改/revise)
      → extract_batch(循环)→ sample_verify → interrupt_III(裁决)
      → finalize_import → END
路由:
  upload_validate → chapterize → interrupt_I(切分确认合并在结构感知前:
     v2 决策——先确认切分再花摘要的钱,故 interrupt_I 在 summarize 前!)
  interrupt_I --confirm--> summarize → volume_rollup → book_digest → gen_outline → interrupt_II
  interrupt_I --refix--> chapterize(带用户修正)
  interrupt_I --read_only--> finalize_readonly(跳过全部沉淀)→ END
  interrupt_II --confirm--> extract_batch*(循环批) → sample_verify → interrupt_III
  interrupt_II --revise(feedback)--> gen_outline
  interrupt_III --confirm(fixes)--> finalize_import → END
全部节点入口挂 deps.check_stop(协作式中断,复用 _node 包装)
```

ImportState(TypedDict):story_id/branch_id/task_id/chapters_meta(章号+标题,不存原文)/summaries 缺口集合/rolling_codex/outline_draft/conflicts/user_input/error。**原文永不入 state**(从 staging 表按需读)——state 常态 <10KB,checkpoint 无压力。

## 6. 状态机

```
story.status:  (新建)importing ──finalize_import──> active
                    │
                    └──finalize_readonly──> active(read_only 标记:import_tasks.stage=read_only)

import_tasks.stage 流转:
pending → chapterized(切分完,等中断I)
       → summarizing → digest_done
       → outline_pending(中断II)→ outline_confirmed
       → codex_running(批循环)→ verify_pending(中断III)→ done
       → read_only(止步)/ failed(error 记录)/ paused(协作中断,可续)
恢复规则:import 端点收到已在跑/中断的同 story → 从 import_tasks.stage+指针续跑(幂等)
```

**read_only 补沉淀**:书库/工作台对 read_only 书显示"继续沉淀"按钮 → 从 digest_done 断点续跑(摘要已完成的直接进大纲)。

## 7. API 契约

| 端点 | 方法 | 请求 | 响应/错误 |
|---|---|---|---|
| `/stories/import` | POST | multipart:file + title? + premise? + copyright_claim:true(必填勾选) | 200 {story_id, task_id} + SSE 流;400 编码/过短/未勾声明;409 该书已有导入任务 |
| `/stories/{id}/import/chapterize-fix` | POST | {mode:"auto"\|"custom", custom_regex?, merge:[{from,to}], split:{at, title?}} | 200 重切结果;409 非中断I态 |
| `/stories/{id}/import/resume` | POST | 中断点裁决:I {action:confirm\|refix\|read_only,...};II {action:confirm, outline_override?}\|{action:revise,feedback};III {action:confirm, fact_fixes, threads, character_fixes} | SSE 流 |
| `/stories/{id}/import/progress` | GET | — | {stage,done_summaries/total_chapters,done_batches/total_batches,costs:{calls,tokens},conflicts?,error?} |
| `/stories/{id}/stop` | POST | 复用 | 阶段边界退出,stage=paused |
| `/stories/{id}/generate` | POST | 复用 | **新增 409**:status=importing 时"导入未完成" |

## 8. SSE 事件契约(新增 4 个,复用其余)

```
import_stage   {stage, label}                       阶段切换
import_progress {kind:"summary", done, total, chapter_no, title}
import_batch   {done, total, batch_no, chapters:[起,止], facts_n, threads_n}
import_cost    {calls, tokens_in, tokens_out}       每 5 次调用滚动播报
interrupt      {type:"import_confirm_structure"|"import_confirm_outline"|"import_confirm_result", ...}
agent_call / stopped / error / done                 全部复用
```

## 9. 前端交互规格

**界面清单(7 屏)**:

1. **书库·导入入口**:「导入旧作」按钮 → 向导对话框
2. **上传屏**:拖拽 txt / 粘贴文本二选一;书名/简介(预填文件名);**版权声明勾选**(未勾不能下一步);底部"预计成本:约 N 次模型调用 · 约 M 万字"实时估算
3. **切分确认屏(中断点 I)**:统计条(章数/总字数/检测模式);章节列表**虚拟滚动**(300+章);异常章高亮(超长/过短/跳号)+快捷修复(合并/拆分/换分隔符重切);三个出口:「确认并开始沉淀」「重新切分」「**仅导入阅读**」
4. **进度屏**:五阶段步进条(切分→摘要→大纲→沉淀→校验);批次进度点阵(每批一格,done=绿/failed=红/待跑=灰);滚动成本条(调用数/tokens 实况);「暂停」按钮;过程面板同款紧凑行(每次 agent_call 自动进)
5. **大纲确认屏(中断点 II)**:逆向大纲**可编辑文本域**(衬线)+「续写方向建议」高亮块;确认/让AI重写(带意见)
6. **结果确认屏(中断点 III)**:冲突报告逐条卡(原文证据 vs 记忆,二选一裁决);角色卡列表(点击编辑);伏笔勾选;统计汇总;「完成导入,开始续写」CTA
7. **完成态**:跳工作台;书库卡片 read_only 书显示「继续沉淀」角标

**状态联动**:导入中隐藏工作台生成按钮;progress 轮询(10s)+ SSE 双通道(SSE 断线 fallback 轮询)。

## 10. 配置项与成本模型

| 配置 | 默认 | 说明 |
|---|---|---|
| IMPORT_BATCH_CHAPTERS | 5 | 批窗口 |
| IMPORT_WAVE_SIZE | 4 | 波大小=抽取并发(=1 退化为串行;RAG 召回仍生效) |
| IMPORT_SUMMARY_CONCURRENCY | 3 | 摘要并发 |
| IMPORT_VOLUME_SIZE | 25 | 卷聚合窗口 |
| IMPORT_VERIFY_SAMPLES | 5 | 抽样章数 |
| IMPORT_MAX_FILE_MB | 50 | 上传上限 |

成本估算公式(上传屏实时展示):
`调用数 ≈ 章数(摘要) + ⌈章数/25⌉(卷聚) + 2(概要/大纲) + ⌈章数/批窗⌉×2(抽取+批聚) + 5(校验)`
100 万字(≈300 章)≈ 300+12+2+120+5 ≈ **440 次调用**,便宜模型为主;预估 tokens in ≈ 1.5×全文字数。确认后开跑,实况滚动播报对照。

## 11. 错误处理矩阵 + 测试清单

| 故障 | 处理 |
|---|---|
| 编码不可识别 | 上传即 400,提示转 UTF-8 |
| 切分 <3 章 | 进手工模式(用户给分隔样例) |
| LLM 空输出/JSON 解析失败 | 重试 1 次;仍败标记 failed 继续,汇总报告不阻塞 |
| 单章超窗口 | 两跳摘要 |
| 批落库中途异常 | 批事务回滚,断批整体重跑 |
| 服务重启 | import_tasks+staging 持久,重启后 resume 从 stage+指针续 |
| 用户协作中断 | 阶段边界退出,stage=paused |
| 重复导入 | 409 |
| 导入中点续写 | 409 |

**测试清单**:切分器 8 例(标准/卷章/无题/跳号/超长/GBK/空/手工模式)/摘要幂等跳过/批事务原子性(注入中断)/setting 链跨批衔接/状态机全路径(含 read_only+补沉淀)/成本公式/双图 thread 隔离/checkpoint 种子衔接(导入完立跑主图续写一章)/e2e 回放 3 章迷你书。

## 12. 分期与目录变更

| 期 | 内容 | 判据 |
|---|---|---|
| M1 | 4.1-4.4 + 中断点I + read_only + staging/进度/SSE/进度屏 | 大书导入**能读**,断点续跑 |
| M2 | 4.5-4.6 + 中断点II + checkpoint 种子 | 导入完**直接续写一章** |
| M3 | 4.7-4.8 + 中断点III + 冲突裁决 UI + 状态机收尾 + generate 409 守卫 | 全流程闭环 |
| M4 演进 | docx/epub、entities 沉淀、视角矫正 pass、版本化重导 | 按需 |

```
新增文件:
app/graph/import_pipeline/{__init__,state,build}.py
app/graph/import_pipeline/agents/importer.py      # 节点族
app/db/ddl.py                                     # import_tasks 表 + chapters.status 注释
app/db/seed.py                                    # importer ACL
app/core/config.py                                # AgentRole.IMPORTER + 默认模型
app/main.py                                       # 5 个端点 + 409 守卫
frontend/src/components/ImportWizard.jsx          # 向导(7屏状态机)
docs/BOOK_IMPORT_DESIGN.md                        # 本稿
复用(不改):commit_finalize 抽出的批落库公共函数、emit/stop/trace 基建、
           GenMasterOutline 同构大纲、extract_facts prompt 骨架
重构 1 处:runtime.commit_finalize 的 facts 落库段抽为
          `apply_fact_changes(conn, story_id, branch, chapter_no, changes)` 供主图/导入共用
```

## 13. 评审决策点(请逐项表态)

1. **双图 + `import:` thread 前缀隔离 + checkpoint 种子衔接**——认可?
2. **中断点 I 移到摘要之前**(v2 修正:先确认切分再花摘要的钱)且提供 **read_only 止步**——认可?
3. **批抽取单次调用产全批变更**(事实+角色+伏笔+滚动卡一次出)vs 拆四次调用——取前者?
4. **波式并行 + 波间 RAG 设定召回**(v2.2,确定性检索替代 LLM 设定卡,与续写期
   检索同构)vs v2.1 三段式(头基线冻结卡)vs 纯串行——取 v2.2?
5. **staging 中间态**(导入中章节不可见)vs 直接 active——取 staging?
6. **版权声明勾选**必填——认可?
7. 批窗 5/波 4/摘要并发 3/卷 25/抽样 5 默认值——认可?
8. **M1→M3 分期**——认可?M4 是否明确降级?
9. 重构 commit_finalize 抽公共函数(主图行为不变,测试回归)——认可?
