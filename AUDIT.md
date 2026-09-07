# 模块实现/使用状态审计报告

> 审计日期:2026-09-07 · 审计人:Claude(所有者指示)
> 信息源:DESIGN_FINAL.md(设计冻结 v1.1)、DDL 20 张表、生产调用链 grep 核实、evals/reports、git log
> 状态:已归档;处置分级见文末(所有者已授权按分级执行)

## 一、规划了、没实现(设计/表结构承诺,代码缺席)

| # | 模块 | 设计出处 | 现状 | 未实现原因(核实) |
|---|---|---|---|---|
| 1 | IF 线番外 | §4.4;branches 表有 if_line/fork_chapter_no;Landing 页正在宣传 | 无 fork API、无图路径、无前端 | P7+ 演进项;依赖章节版本化(#2),连环缺席 |
| 2 | 章节版本化改写(v2归档/stale连锁/回退) | §4.3;chapters 有 version_no/prev_version_id/status=stale | version_no 恒 1,无 stale 流转 | 定稿前重写被中断点B覆盖;定稿后改写低频重场景,连锁重生成工程量大 |
| 3 | temporal_relations 时间偏序 | DDL+seed 授 event_manager 写权限 | 零写入、零消费 | 抽取 prompt 不产时间关系;时间正确性由 setting 推翻链兜底 |
| 4 | 段落级下钻检索 | §3.4;paragraphs 表 | 零写入(正文整块存 chapters.content) | 规模未到 |
| 5 | volume/book 摘要层 | DDL 枚举 chapter|volume|book | 只有 chapter + stage(实现期新增,不在设计枚举) | 分层金字塔刚起步 |
| 6 | known_partial 部分知晓(R4) | fact_visibility.knowledge_level/detail | 写入恒 known_full | 抽取不产部分知晓语义;误信场景由 beliefs 分担 |
| 7 | 认知演化链(误信A→B→真相) | beliefs.prev_version_id | 恒 None,无 dispel 路径 | event_extractor 不输出 supersede/dispell |
| 8 | 共创多轮访谈 | §4.1① | 一次 initial_input 直通(代码注释承认 API 层聚合未做) | tags+一句话构想兜底,交互工程收益/成本比低 |
| 9 | 评审 rubric 配置化 | §7 | 维度硬编码在 prompt | 未排期 |

## 二、实现了、没使用(生产链路不走)

| # | 模块 | 现状 | 原因 |
|---|---|---|---|
| 10 | vector_hits 向量兜底结果 | 检索层已算(embedding 接入后生效),build_context 的 bundle 不含、writer 不渲染——算了没人消费 | embedding 接入时只接了"算"没接"用" 【断层级,已列入立即修复】 |
| 11 | expanded_entities 实体链接一跳 | 检索查了,writer 不渲染 | 依赖 #12(entities 无生产写入)→永远空集 |
| 12 | entities/entity_links Wiki 图 | repo 方法+ACL 齐备,无节点调用;characters.entity_id 恒空 | 共创设定未沉淀为实体图 |
| 13 | Repository 写方法族 + 生产写路径 ACL | commit_finalize 原生 SQL 单事务直插,repo 写方法仅测试调用;ACL fail-closed 事实上只剩检索读路径 | 编排原子性要求单事务(正当权衡),但设计文档未承认该豁免【已列入文档化】 |
| 14 | 前端 api.traces() | 后端端点+api 方法齐备,无 UI 调用;服务重启后历史过程不可回溯 | 实时流优先,历史 UI 未排期【已列入近期修复】 |
| 15 | replay_world 世界回放 | 仅测试调用;设计定位"审校校验基准",审校实际用 pov_facts | 上帝视角回放依赖 IF线/版本化(#1#2),上游缺席 |
| 16 | stories.status 流转 | 恒 draft;前端 badge 无语义 | 无流转触发点 |
| 17 | story.premise | 创建时存储,generate 的共创 prompt 不读——用户填的一句话简介不参与生成 | 断层级遗漏【已列入立即修复】 |

## 三、使用中但有名无实(半使用)

- outlines 版本化:每次确认插新版+归档,无回看旧版 UI
- fact_visibility.learned_chapter:恒等于本章,无跨章学习延迟场景

## 四、已核实为真实现(非虚报)

- evals:memory_eval_report.md / e2e_eval_report.md 均有产出,Landing 宣称数据有出处
- agent_traces 全链路可观测、协作式中断、分层摘要(stage)、setting 推翻链、embedding 落库

## 五、原因归类

1. 设计全集 vs 实现子集:DDL 一次性建全集 20 表,功能按 P0-P6 排期 → 空转表(temporal/entities/paragraphs 为重灾区)
2. 规模未到:下钻检索/volume 层/向量长尾在 3-4 章量级下需求被掩盖
3. 权衡未文档化:#13 ACL 旁路是正确决策但无记录,成隐性债务
4. 新功能引入断层:#10(embedding 接入没接消费端)、#17(premise 创建→生成断在中途)
5. 营销超前实现:Landing 宣传 IF 线(#1),代码零实现

## 六、处置分级(所有者已授权执行)

| 级 | 项 | 动作 |
|---|---|---|
| 立即(断层) | #10 vector_hits 消费、#17 premise 进共创 | 修复 |
| 近期(低本高效) | #14 历史 trace UI、#13 ADR 文档化 | 修复 |
| 排期(依赖明确后) | #3 temporal、#11/#12 实体图(先定"设定沉淀"产品语义) | 待排期 |
| 降级(文档同步) | #1 IF线、#2 版本化、#4 段落下钻、#5 volume层 | DESIGN_FINAL 加实现状态对照表;Landing 的 IF 线宣传待实现后再上 |
