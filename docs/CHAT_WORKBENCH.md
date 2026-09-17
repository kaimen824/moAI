# 对话式工作台方案稿(ChatDock + ReAct Agent 驱动模式)

状态:方案 v3,待批(2026-09-17)。演进:v1 意图路由 → v2 双模式单内核
→ v3 ReAct 循环 + 节点即工具(所有者定调)。不变的两条根:一键生成
不是唯一入口;Agent 模式同样生成、同样落库、该有的审核同样有。

## 一、双模式,一个内核

| | 一键生成(现有) | Agent ReAct 模式(新) |
|---|---|---|
| 驱动 | 点按钮,图编排全自动 | 对话下命令,ReAct 循环逐步调工具 |
| 状态 | 图 state(checkpoint) | **同一份**(读写全经 get_state/update_state) |
| 落库 | commit_finalize 单事务 | **同一个**(闸门确认后照常走) |
| 人审 | 中断卡 | **同样的闸门**,对话内确认卡 |
| 成本观测 | usage_log / agent_traces / 预算闸门 | 全部共用(ReAct 思考步也计入) |

**执行内核只有一份**:图的路由谓词/评审循环/定稿事务不做第二套。
ReAct agent 的自由是"选择下一步做什么",不是"改流程规则"。

## 二、ReAct 循环(chat_service)

```
用户消息 ──► LLM(挂工具表)──► tool_call ──► NodeRunner 执行 ──► 观察结果回填
                ▲                                              │
                └────────────── 循环,直到最终回复 ◄────────────┘
每步 SSE 推送(思考摘要 / 工具执行卡 / 观察摘要);用户可随时打断
(协作式停止复用);步数上限兜失控。
```

- 循环为手写轻量实现(~百行,与项目"不引框架"一致,ADR-0030 精神),
  不用 create_react_agent。
- **前置改造**:LLMFacade.chat 增加 tools/tool_choice 透传(GLM 的
  OpenAI 兼容接口原生支持;provider 层为 openai sdk,参数直通);
  tool_call 消耗照常进 usage_log/agent_traces。

## 三、工具表(四类)

### 1. 节点工具(白名单三档,直调单节点)

| 档 | 节点 | 权限 |
|---|---|---|
| 生成型 | chapter_slice / stage_outline / gen_master_outline / write_draft / polish_draft / summary / event_extract / build_context | 调用即执行,产出经 update_state(as_node) 写回正式 state |
| 裁决型 | review_draft_outline / review_quality / review_threads / merge_reviews | 可调可看,**产出 AI 无权改**——verdict 是代码逻辑,agent 只转述 |
| 闸门/落库型 | confirm_* / user_review_chapter / finalize / commit_character_seeds | **不是工具**,永远不进工具表;确认动作只来自用户 |

### 1b. ReAct 独占节点(2026-09-17 所有者拍板)

**revamp_chapter(重构历史章节)**:选已定稿章 + 意见 → 重写 → 照常走
三评审/人审/定稿。**一键模式拓扑上不可达,只有 ReAct 工具表能发起**:

- **门禁四层**:① wiring 注册**无入边**(START→route_entry 永远到不了,
  一键模式没有"重构旧章"入口);② 执行只在工具内(get_state 取底 →
  跑节点函数 → update_state(as_node="revamp_chapter") 写回);③ 出边
  接回写作后评审链,写回即占位,后续闸门照走;④ 工具表只挂 chat 端点。
  已验证(LangGraph):无入边节点可编译且 invoke 不可达;as_node 写回
  后 stream(None) 从其出边续跑;写回不执行节点函数本身。
- **边界**:目标章必须是已定稿章;主 run 在跑时 409(与一键互斥一致);
  重写后该章 event_extract 重跑,fact_changes 正常进台账。
- **后续章节追溯语义(2026-09-17 所有者拍板:b 冲突标注)**:revamp
  评审通过后、目标章人审确认前,跑冲突检查——拿重写稿(或其摘要)对照
  N+1 起已定稿章节的 chapter_summary,LLM 产出冲突清单
  [{chapter_no, conflict, suggest}]。清单进对话内确认卡,作者逐章勾选
  哪些后续章要重写;每章重写复用 revamp_chapter,独立走评审+人审闸门
  (不级联、不静默改文)。实现落工具层:冲突检查是 revamp 专属逻辑,
  不进图拓扑,不触碰一键模式路由;检查结果与逐章拍板都发生在对话内。

### 2. 复合工具(不变式的守门人)

- **run_reviews**:一次调用 = 三评审 fan-out + merge 表决 + 分流裁决
  (polish/rewrite/needs_user 判定)——重写计数、REWRITE_LIMIT、
  needs_user 转人工全部在工具内部经 state 强制,agent 无法绕过或手搓表决。
- **rewrite_with_feedback(feedback)**:带意见重写 = write_draft + 自动
  重跑 run_reviews,内部维护 rewrite_count;达上限工具直接返回
  "需用户裁决",循环终止。
- 设计原则:**凡有循环/上限/表决逻辑的流程,打包成复合工具,代码守
  不变式;agent 只表达意图**。不做:把三个评审节点裸露给 agent 自由编排
  (它可能跳过 merge、可能无限重摇)。

### 3. 流程工具

- start_generate(chapters / auto_confirm / tags)/ stop_run / resume_run
  (action+feedback+threads)——复用既有 sse/互斥/闸门语义。
- 主 run 在跑时,生成型节点工具与 start_generate 409(与一键模式互斥一致)。

### 4. 查询工具(只读)

- book_detail / get_chapter / codex / plot_threads / usage——现有
  queries.py 直接复用,agent 答书内问题用。

## 四、状态通道(ReAct 与图互通的根)

- 工具执行统一走 NodeRunner:`get_state(thread_id)` 取底 → 实例化节点
  → 执行 → `update_state(config, values, as_node=节点名)` 写回。
- 好处:**两种模式随时互换**——agent apply 了 write_draft 产出后,
  切回一键模式,图按 as_node=write_draft 的路由继续走三评审;
  反之中断卡确认后 agent 也能继续。同一 checkpoint,无状态分叉。

## 五、人审闸门(不旁路)

- 走到确认点(总纲/细纲/章节),ReAct 循环暂停,对话内渲染确认卡
  (复用中断卡紧凑版):确认 / 意见打回 / 伏笔勾选 → 映射为 resume 语义。
- agent 可以**建议**("评审通过了,建议定稿"),不能**代替**——
  确认动作的执行者永远是用户。

## 六、会话与成本

- chat_messages 表(id, story_id, user_id, role, content, meta_json,
  created_at),按 story 单会话;工具调用与观察摘要入 meta_json。
- 成本:ReAct 每步思考是额外 LLM 调用,写一章全流程约为一键模式的
  1.5-3 倍;步数上限(15 步/轮,可配)+ 预算闸门(已有)兜底;
  工具观察回填做摘要截断(草稿全文不进思考上下文,引用定稿链接)。

## 六A、全链路 debug 日志(硬需求,所有者 2026-09-17 追加)

ReAct 每一步落盘 JSONL,按 story 分文件:`logs/chat/{story_id}.jsonl`。

- **事件类型**:user_message / step(思考+工具选择)/ tool_call
  (name+参数全文)/ tool_result(观察**全文不截断**——落盘全量供
  debug,LLM 上下文回填才做摘要截断,两者独立)/ state_update
  (as_node + 写回字段清单)/ gate_wait(闸门暂停)/ final_reply /
  error(含 traceback)。
- 每条含:ts、轮次 id(turn_id,一次用户输入到最终回复)、步号、
  耗时、tokens(思考步与工具步分开)。
- 与 agent_traces 的分工:traces 表管 LLM 调用快照(可查询、关联
  run_id);JSONL 管 agent 决策链时序(打开文件即读完一轮对话的
  完整推理过程)。写文件失败不阻断主流程(与 llm_failures 同策略)。

## 七、分期

| 期 | 内容 | 量级 |
|---|---|---|
| P0 | facade tools 透传 + chat_messages + ReAct 循环(查询+流程+directive 三类工具)+ ChatDock UI ✅(2026-09-17,08d3a60) | 2 天 |
| P1 | NodeRunner(get/update_state 通道)+ 节点工具与复合工具(run_reviews/rewrite_with_feedback)+ ReAct 独占节点 revamp_chapter(重构历史章节,所有者 2026-09-17 拍板;开工前先拍"后续章节追溯语义")+ 对话内确认卡 → Agent 模式成型 | 2-3 天 |
| P2(独立) | ②③ 对话命令(regen_stage / reoutline;"修订确认保留进度"行为变更另拍板) | 1 天 |

## 八、明确不做

- 不做第二套执行内核 / 不自研 state channel(只走 get/update_state)。
- 不把闸门/落库放进工具表;AI 不代签评审与确认。
- 不用 create_react_agent 框架,手写循环。
- 不做多会话轮换(单 story 单会话,历史超长后:最近 N 条 + 摘要)。
