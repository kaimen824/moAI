# 书籍导入设计稿 v2.2 — 多Agent评审报告(终版)

> **2026-10-08 回写完成**:12 项阻断修法已全部并入设计稿 v3;决策点 D10-D13(状态组装层)已拍板并入;
> 剩余 D1-D9 待表态(不阻塞 M1,阻塞点见 v3 §14)。本报告转为历史评审记录。

> 评审对象:`docs/BOOK_IMPORT_DESIGN.md` v2.2(2026-09-07)
> 评审方式:内部多Agent三轮迭代——第一轮 4 视角并行独立评审(工程落地/架构并发/效果成本/前端契约)→ 第二轮红队攻击修正方案 + 修正后效果重估 → 第三轮终审判定收敛。所有发现均对照真实代码验证,关键机制(update_state 种子写入、POV JOIN 语义、pending_review 检索行为)经 langgraph 1.2.11 实证。
> 终审结论:**有条件通过**——12 项阻断级修法全部闭合,工程侧无未解异议;剩余分歧全部收敛至 9 个决策点(§五),待所有者表态后回写设计稿。

---

## 一、最终结论

**工程落地**:可回写、可开工。原稿的复用声明大部分成立(双图 thread 隔离/update_state 种子机制/检索同构/中断点模式均有代码与实证支撑),但 12 项阻断级问题在回写前必须落实修法——其中 3 组耦合(§二 B2'/B3'/B5' 系)是"复用现有代码时现有语义与导入语境错配",恰藏在"零新基建"承诺之下。

**实际预估效果**(300章/100万字标准书):

| 指标 | 终版口径(替换原稿 §4.6/§10 旧口径) |
|---|---|
| 模型调用 | ≈480-530 次(便宜模型 ~90%;embedding 另 ~80 次不计入话术) |
| tokens | 输入 ≈220-280 万(≈2.2-2.6× 全文字数;原稿 1.5× 低估 25-35%),输出 ≈30-50 万 |
| 费用 | ¥5-15,中位 ¥8-12 |
| 时长 | **1.2-2.5 小时**(原稿 15-20min 严重低估;DashScope 低档 TPM 限流账号 3-5h,话术留余量) |
| 用户在场 | 4 个时点(上传+3 中断点),动手合计 15-45 分钟;两段等待可离场(断线自动恢复) |

**M2 判据("导入完直接续写一章")三维度达标概率**:设定一致性 65-75%(所有者在中断 II 花 10 分钟手补金手指/体系规则 → 80-90%,全流程性价比最高的人工投入);剧情衔接 80-90%;文风"读者不跳戏"50-60% / "像原作者"仅 20-30%(平台无文风产物,是最大能力缺口,见 D3)。

**结构性边界**(修正不能解决,话术须明示):①story_recap 对 ch1-290 的信息压缩率 0.55%(分层记忆物理上限,长期续写的旧梗免疫"勉强");②规则级设定经四级压缩保留率 20-40%(名字级 80-90%);③实体消歧缺失(60 批名字变体碎卡);④文风零产物。合理定位:**"设定名字级基本不错、剧情不重排的高质量草稿基座"**,而非"原作者复生"。

**3 条预期管理话术**(上传屏/完成态,随 D5 结果微调):
1. "导入约 1-2.5 小时,只需你决策 4 次、动手 15-45 分钟;其余时间可以关掉页面离开,回来自动恢复到断点。期间这本书暂不能续写。"
2. "机器沉淀尽力保证'不重排旧剧情、设定名字基本不错';规则级设定一致性最有效的 10 分钟,是你在'大纲确认屏'亲手补写金手指/力量体系规则。抽检是 5 章冒烟,不保证全库无误。"
3. "本版不承诺'续写得像你本人写的'——文风延续在路线图上;请把首次续写当高质量草稿用,用修订指令校正文风。设定集默认展示最近约 300 条记忆,全书记忆在持续续写中生效。"

---

## 二、阻断级问题与终版修法(12 项,回写必改)

> 依据代码行号均为当前 main;`runtime`=app/graph/runtime.py,`build`=app/graph/build.py,余同。

**B1 种子缺 `master_outline`**
StageOutlineNode 直接读 `state['master_outline']`(supervisor.py:92),WriterNode 同(writer.py:21-22),均不读 outlines 表;原稿种子五字段 → 导入后首次续写,细纲与写作的总大纲段为空串,§1"无需任何续写侧特殊代码"不成立。
**修法**:种子七字段 `{story_id, branch_id, chapters_done:N, outline_confirmed:True, chapter_no:N, master_outline:逆向大纲定稿全文, stage_outline:""}`;中断 II 确认稿 = outlines 表落库 = 种子,**同一字符串三处同步**。已实证(8015 字大纲种子 + invoke,StageOutlineNode 收到全文)。补测试:参照 tests/test_graph_e2e.py:124 模式。

**B2' setting 链治理(三修正确确耦合,终版解法)**
`_fact_supersede_target`(runtime.py:285-296)对无显式 supersedes 的 setting 自动接"当前最新有效 setting"链尾——主图语境正确(场景切换失效),导入语境语义反转:60 批连续落库把世界观/地理串成一条链,早期设定被判"推翻"而检索不到。红队发现:单禁兜底(旧 setting 只能靠显式 supersedes)+ 召回按新近度截断(LLM 看不到旧设定→无法显式推翻)+ 余弦阈值候选(误报漏报)三者互相拆台。
**终版修法**:① apply_fact_changes 加 supersede_policy,导入禁用链尾兜底;② setting 归并做**全局确定性扫描**(全量扫描,超长按章号分段;setting 量级 200-600 条/300章),按章号序喂薄归并 LLM 一次裁决继承链,产出经中断 III 确认——**没有任何路径绕过用户确认建链**;③ setting 类召回上限按"链头/未链接全保留"而非纯新近度;④ 抽取 prompt 保留 supersedes 教学(event_extractor.py:21-22 现有)。

**B3' 批/波事务语义 + 两个执行者**
原稿 §4.6"波=事务边界"与 §11"批事务回滚"矛盾;按整波重跑必重复落库(重抽 LLM 措辞漂移,精确去重 content=? 拦不住,runtime.py:344-349)。
**终版修法**:批=事务+幂等单位,波=仅并发调度单位;**import_tasks 表为唯一 SSOT,done_batches 的 UPDATE 与批数据同事务**;恢复从 done_batches+1 续,同波已落库批跳过;落库异常重放变更集(内存)/服务重启重抽(计成本)分写。**paused 落笔者 = worker 的 except StopRequested 分支**(main.py:134-136 现只 emit 事件),且 paused 为标志位、stage 保留原值(与 B10' 一致)。

**B4' 恢复入口三处矛盾 + pending 期原文丢失**
§6"import 端点续跑" vs §7"409 已有任务" vs multipart 要求文件;stage=pending 期暂停,原文未落任何地方(import_tasks 无 source_text)。
**终版修法**:① import_tasks 加 source_text 列;② 新增 `POST /stories/{id}/import/continue`(无 body,按 stage+指针续跑;start 对 stage=pending 幂等);③ 409 收窄为"存在 running 任务";④ 书库 importing 角标配"继续/放弃导入",**放弃仅当 chapters 全部 staging(任一 finalize 前)可用;已翻转 active 的书不提供放弃**——否则 read_only/续沉淀书放弃后 active 章残留 → 守卫解除 → 共创写 ch1 同号并存,正是 B5' 要堵的脏数据路径从"放弃"侧回流(终审发现,一句话封死)。不做超时回收 cron。

**B5' read_only 双缺陷(经 E/F 双源实证)**
(a) finalize_readonly 未写 staging→active 翻转 → read_only 书阅读器永远空,M1 判据"能读"失败;(b) read_only 书 status=active 且无种子 → generate 放行 → route_entry 看 chapters_done=0 → 走完整共创 → `confirm_master_outline` 写 chapters_done=0(build.py:104-105,还会清零后续种子)→ **chapters 表无 UNIQUE(story_id,chapter_no)(ddl.py:102-115),导入 ch1 与新生成 ch1 同号并存,静默数据污染**。
**终版修法**:① finalize_readonly 同样执行 staging→active 批量 UPDATE;② **generate 守卫最终形态:存在 import_tasks 且 stage∉{done} 即 409**(read_only 也挡,导去"继续沉淀")——比"story.status=importing"判据更严更准;③ 主图 thread 残留检查(snapshot next 非空→拒绝/审计)**必须在写种子之前**(实证:update_state 后 next 恒非空,顺序反了会永远误拒)。

**B6' `_sse_run` 三合一 key**
main.py:108-120 同一 thread_id 兼作 cfg thread/emit 归属/_active 互斥/clear_stop——导入图直接复用会产生:事件广播到 `import:` 前缀(前端收不到)+ stop 残留清不掉(resume 首节点立即再停)死路径。
**终版修法**:拆参 `_sse_run(graph_input, graph_thread, broadcast_key)`(主图两参同值,行为不变);clear_stop/clear_events/_active 均用 broadcast_key(story_id);互斥检查按 broadcast_key。前端防串台:导入事件加 `source:"import"`(或前端按 kind 前缀忽略)——否则 Workbench 会把 import 的 done/interrupt 当主图事件渲染(Workbench.jsx:252/265);run-state 的 interrupt 载荷同覆盖。已知限制:`_current_thread` 单槽在多书并发运行时事件串台(单用户本地工具暂接受)。

**B7' recursion_limit + 暂停响应**
recursion_limit=200 硬编码(main.py:119),300 章图级循环必炸;且 stop 只在节点入口检查,15-100 分钟的循环节点内暂停无响应。
**终版修法**:摘要/批循环 = 节点内 Python 循环(信号量并发);**check_stop 在节点主循环的章/波调度边界调用并重抛**(线程池内抛出不自动传播);导入图 recursion_limit 按章数推导;§11 措辞"章/波边界退出(≤1-2 分钟,波内已提交调用需跑完)"。

**B8' apply_fact_changes 签名**
原稿 §12 签名(标量 chapter_no)与 §1"逐条章号"直接矛盾;supersedes 匹配含 chapter 条件需参数化(runtime.py:279);embedding 尾部约定(emb_vecs[-1]=summary,runtime.py:417)是重构陷阱;现有测试无 embed 调用计数。
**终版修法**:条目级 chapter 覆盖 + facts 向量注入(**公共函数只收 facts 向量,summary 由调用方自理**)+ supersede_policy;**threads.chapter clamp 到批内 [start,end]**(便宜模型章号幻觉防线);新写"主图 embed 调用形状不变"回归测试;D2 结果(force_confirmed 类参数)并入签名。

**B9' 主路召回无上限**
retrieval 无 LIMIT,1000 章书 POV 池 1500+ 条;红队修正论据:POV 主路只含"有 visibility 行"的 facts,真正暴露面是 context_bundle 进 checkpoint 体积与全表余弦成本;writer 渲染层 max_facts=40 只是最后一道。
**终版修法**:检索层 LIMIT(如 50):setting 类按链头全保留 + 其余按新近度;vector_top_k=15;get_pov_memory/_vector_fallback 提公有。1000 章书的纯 Python 余弦 2-4s/章 → M4 前换 numpy/预解码(记录不阻塞)。

**B10' read_only 状态机不可达**
§6"从 digest_done 断点续跑"——但 read_only 出口在摘要前(done_summaries=0),该状态不可达;paused 作为 stage 值覆盖原阶段。
**终版修法**:状态机补 `read_only → summarizing` 转移(触发=继续沉淀,走 continue 端点);paused 改标志位;read_only 续沉淀期间 story.status **不翻转**,角标与守卫一律读 import_tasks(M13 同源);§4.4 全书概要补幂等键。

**B11(新) 批输入无字符预算**
超长章("疑似未切开">3 万字被用户确认/合并)单批可达 12.5 万字 ≈25 万 token,超 deepseek-v3 128K;静默截断 = 批尾章零事实且无感知。
**修法**:批窗改"5 章 **或** ≤4 万字孰先";截断 dropped 标记。

**B12(新) 无 visible_to 的世界级设定 POV 不可达**
get_pov_memory JOIN fact_visibility(world.py:88-100)——没有 visibility 行的 fact 永不进 POV 主路,只走 5 槽向量兜底;世界级设定(金手指/体系/地理)天然无具体可见角色,便宜模型 visible_to 留空估 20-40% → **最需长程保鲜的设定恰好落在最弱通道**。续写期同构问题一直存在,导入将其放大为结构性缺陷。
**修法**:见决策点 D8(①导入 prompt 强制世界级事实 visible_to 填全员——落库时展开为全部/在场角色各一行(fact_visibility 无通配字段,必须展开);②检索层 type=setting 加 OR 分支)。工程依据的倾向是①(爆炸半径锁在导入侧,②动 get_pov_memory 会改变所有续写期检索行为)——**非推荐,所有者拍板**。

---

## 三、高严重度问题(12 项,修法摘要)

| # | 问题 | 修法要点 |
|---|---|---|
| H1 | route_next 累计 chapters_done≥target(build.py:221-224):种子 300+target 3 → 只写 1 章(主图既有缺陷,导入使其主流化) | 改增量对比;语义二选一见 D9((a) 端点注入基线缺省 0=老测试绿,或 (b) 图内真增量=须改 test:124——E 已证明该测试会被打穿且侥幸通过) |
| H2 | 409 守卫排 M3 而 story_recap 的 layer=stage 查询(runtime.py:183-188)不 JOIN chapters,M1 卷聚合即泄漏 | 守卫提前 M1(判据用 B5'② import_tasks 形态);recap stage 查询拍 chapters active JOIN |
| H3 | 同波重复抽取去重不闭环(写路径仅精确 content 匹配;归并候选机制缺失;裁决前双版本已生效) | 候选=同 type+章号距离≤波窗+**字符 bigram Jaccard/共享专名数字 token 预筛**(纯 Python);余弦只作候选内部排序不作门槛(中文短文本余弦压缩在 0.75-0.95 高位带,0.90/0.96 阈值双向失真);embedding 补算提前到归并前;统一薄归并 LLM 裁决+中断 III 确认;失败矩阵加"中断 III 弃置"行 |
| H4 | beliefs 无 dispelled 输入 → 导入误信永久 believed,与 world._BELIEF_ACTIVE 消费语义不对齐(writer 的[角色认知]区全量渲染,过期误信会塞满续写 prompt) | 见 D1 |
| H5 | 伏笔 resolve 靠描述前 12 字 LIKE(runtime.py:404-407),跨批措辞漂移 30-60% 该结未结([活跃伏笔]区全量渲染,陈年伏笔塞满 prompt) | 匹配升级"plant 章号区间+描述"双键 + B8' 章号 clamp |
| H6 | `/stories/import`"200 JSON+SSE 流"自相矛盾;前端 sse() 拿不到 story_id | 拆两步:POST /stories/import 返 JSON → POST /{id}/import/start 开 SSE;僵尸 task 由 B4'④ 幂等+放弃入口解决 |
| H7 | 批次点阵数据源断裂(无批级状态持久;_history 2000 环形缓冲重连补不齐;import_batch 无 status) | import_tasks 加 batch_statuses json 列;progress 响应加 batches[];import_batch 事件加 status:"ok"\|"failed" |
| H8 | confidence=low→pending_review(runtime.py:361)→ /facts/pending 全局队列(无 story 过滤,main.py:327-334)被数千条淹没 | **关键事实(已查证):pending_review 不是检索闸门**(POV/向量兜底/codex 均只排 rejected)——两选项在生成行为上零差异,争的全是审核 UI。见 D2 |
| H9 | SSE 断线恰逢中断点:progress 无 waiting/interrupt;outline_draft 不在响应 | progress 加 {waiting, interrupt}(镜像 run-state 语义)+ outline_draft |
| H10 | 手工模式(<3 章)无屏无 stage;chapterize-fix 参数语义(custom_regex≠样例/split.at 无定义) | 屏 3 补手工模式空态;参数改 custom_sample + split:{chapter_no, offset, sample_line};互斥声明;探测失败也停 stage=chapterized(mode=manual) |
| H11 | 成本/时长口径低估(见 §一 终版口径) | §4.6/§10 旧数字全部替换;上传屏金额+时长并列 |
| H12 | update_state 不清 pending interrupt:主图 thread 残留共创中断 → 种子后首次续写撞陈旧中断卡 | 并入 B5'③(种子前检查,顺序钉死) |

## 四、中严重度(摘要,回写时逐项过)

M1 embedding 失败降级+finalize 前批量补算(提前到归并前,见 H3)/ M2 chapters_meta·角色名单出 state(入 import_tasks json 列,保 state<10KB;**§5 ImportState 须按 v2.2 重写,删 rolling_codex/chapters_meta**)/ M3 12 态→5 步映射表(含三异常态)/ M4 resume 按 stage 校验 action 白名单(现有节点对未知字段静默忽略)/ M5 上传端点同步跑切分探测返回 estimated_chapters+屏 3 复核 / M6 卷聚合按真实卷边界(4.2 已捕获卷标题)+ 卷聚合生成 cap ~400 字(recap 渲染截断)/ M7 统一 2.5 万/3 万阈值 / M8 60 条预算软约束+dropped 溢出标记+setting 类放宽 80 字 / M9 中断 III 默认一键采纳+按置信度分组+伏笔按 planted 章号批量勾选(裁决负担从 100-400 条逐条 → 3-5 组)/ M10 抽样话术"5 章冒烟"/ M11 codex 遮罩(见 D5)/ M12 中断 II revise 上限 3 / M13 list_stories LEFT JOIN import_tasks(read_only 角标数据源)/ M14 layer 用 'stage' 勿照 DDL 注释写 'volume' / M15 章数>3000 软警告+超长书"过夜任务"话术 / M16 staging 读取端点(拆分预览)/ M17 借档调用 usage 归 SUPERVISOR 桶,对账按 stage / M18 文档:"零新基建"改"零新依赖"+stories.status 注释+§12 补 api.js/DDL 变更 / M19 codex 首开:facts LIMIT 300 只见尾部 25-40 章+角色碎卡(60 批无实体消歧,主角 2-6 张变体)→ 中断 III 加角色合并 UI / M20 DashScope TPM:峰值并发 4×36K≈15 万 TPM,低档账号时长翻倍,话术留余量或自适应降并发 / M21 纯 Python 余弦规模曲线(见 B9')。

**交叉确认成立的设计声明**(不再争议):update_state 种子机制(实证)、多中断点+revise 循环与 LangGraph 语义匹配、波间 RAG 检索与续写期同构(get_pov_memory/_vector_fallback 现成,提公有+top_k=15)、staging 不泄漏到现有 6 处 chapters 查询(除 H2 特例)、interrupt 事件同构/InterruptCard 兜底/ProcessPanel 复用、stop 端点能停导入图(check_stop 键=state.story_id)、ACL 一行覆盖读写(_WRITER_DOMAINS (1,1))、导入期 stop 键/摘要输入窗口/分层记忆结构/管线骨架(对照 Recursive Summarization+Story Bible 主流形态,**波间确定性检索替代 LLM 设定卡是本稿最好的设计决策**——Sudowrite 等主流靠人肉维护 Story Bible,本管线自动产出+可编辑确认+时点回放+POV 过滤,自动化程度反超)。

---

## 五、决策点(所有者表态,工程侧不再争议)

| # | 问题 | 选项与事实 |
|---|---|---|
| D1 | beliefs 的 dispelled | (a) 抽取 schema 直出 dispelled_chapter(ddl.py:72 列已存在) (b) 归并 pass 做"后续 facts 与 belief 矛盾→建议 dispel"。(a) 靠便宜模型识别澄清时点,(b) 靠确定性比对+LLM 裁决 |
| D2 | 导入期 confidence 语义(H8) | (a) 一律 confirmed(confidence 列留痕,不进审核流;改动面≈0,由中断 III 冲突报告+抽样校验替代抽检) (b) pending 队列按 story 隔离+批量裁决 UI(改动面大,且**不改变任何检索行为**——pending_review 非检索闸门已查证)。红队事实陈述:(a) 与现有语义冲突显著更小 |
| D3 | 文风(最大能力缺口) | (a) 不加(话术③明示) (b) few-shot 尾部 2-3 章各取 800 字进 writer prompt(每次续写 +~5K token,文风达标 20-30%→45-60%,近零成本) (c) 完整文风笔记(1-2 次便宜调用) |
| D4 | 波式 vs 全并行+全局归并 | 行业主流是全并行+一次全局实体归并(wall-clock 更优);波式对"演进设定密集"的书(无限流)是必需品,对多数书是次优折中。若 wall-clock 成投诉点,第一刀是改全并行而非加并发 |
| D5 | codex 导入后展示(M19) | 遮罩 / 分组+搜索 / 接受现状(话术③是现状版,拍其他选项话术要改) |
| D6 | 中断 III 默认一键采纳(M9) | 交互取向:默认全采纳+异议逐条改,还是默认逐条过 |
| D7 | 原稿 §13 九项决策点 | 在 v2 修法下重新表态(尤其:决策点 4"波式 RAG"确认为本稿最强设计,维持;决策点 9 重构 commit_finalize 维持且 B8' 签名已定) |
| D8 | 世界级设定可见性(B12) | (a) 导入 prompt 强制 visible_to 填全员(落库展开为逐角色行;爆炸半径锁在导入侧) (b) 检索层 setting 加 OR 分支(动 get_pov_memory,**改变所有续写期检索行为**——既是卖点也是风险)。工程依据倾向 (a),非推荐 |
| D9 | H1 增量语义 | (a) generate 端点读 get_state 注入 run_start_done,缺省 0=绝对语义(老测试绿,老 bug 仅存于直调路径) (b) 图内缺省=当前 done(真增量,须回改 test_graph_e2e.py:124——E 证明其会被打穿且侥幸通过留下悬空中断) |

## 六、回写清单(设计稿更新时逐项同步)

1. §1:衔接细节按 B1/H1/B5' 重写;"无需任何续写侧特殊代码"改为"需两处主图修正(route_next 增量 + generate 守卫)"
2. §3.2 DDL:import_tasks 补 source_text/paused 标志/batch_statuses 三列;§3.1 补 stories.status 注释
3. §4.6:"波=事务边界"删除→B3' 语义;setting 治理按 B2';批窗"5 章或 ≤4 万字"(B11);"15-20min"→H11' 口径;归并候选按 H3';visible_to 按 D8 结果
4. §5:ImportState 按 v2.2 重写(删 rolling_codex/chapters_meta);循环=节点内+B7' 检查点
5. §6:状态机按 B10'(read_only→summarizing 转移,paused 标志位);§7:端点表补 /import/start、/import/continue,409 收窄,resume 加 stage×action 白名单;§8:三型 interrupt 载荷补全+import_batch 加 status+import_cost 发送方;§9:屏 3 手工模式空态、屏 4 batches[]、12 态→5 步映射表、暂停后界面
6. §10:成本公式补归并/重试项+tokens 2.2-2.6×+¥区间+时长区间
7. §11:矩阵补 embedding 失败/中断 III 弃置/僵尸 task;测试清单补 B1 种子测试、B8' embed 形状回归、B5' 守卫(含放弃回流)、批事务原子性
8. §12:变更清单补 main.py _sse_run 拆参、api.js、D8① 落库展开;"零新基建"→"零新依赖"
9. §13:重列为 D7(并入本报告 D1-D9)

---

*评审痕迹:第一轮 4 报告(工程落地/架构并发/效果成本/前端契约)→ round1_synthesis → 第二轮红队实证攻击+效果重估 → round2_final_plan → 第三轮终审(有条件通过,4 组回写条件已并入本报告)。原稿最危险的三组耦合——setting 链治理、批/波事务、双图与种子衔接——均已闭合。*
