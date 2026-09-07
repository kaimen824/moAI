/* 术语层:同一信息,两种说话方式。
   原则(所有者裁决):透明是默认态——用户模式是"翻译"不是"遮蔽",
   每个 Agent 步骤依然实时可见、可下钻到模型输入输出;
   开发者模式只是切回工程术语,不改变可见性。 */

export const COPY = {
  // 中断点标题
  interruptTitle: {
    confirm_master_outline: ['故事骨架已就绪', '中断点 0 · 确认总大纲'],
    confirm_stage_outline: ['本阶段剧情规划', '中断点 A · 确认阶段细纲'],
    user_review_chapter: ['AI 写完了一章,请你过目', '中断点 B · 章节审阅'],
  },
  // 关键状态
  needsUser: ['AI 连改几版仍未过审,需要你拿主意', '重写上限 needs_user'],
  rewriteExhausted: ['已达重写上限仍未通过,由你裁决:收下这章,或写下意见让 AI 再改',
    '已达重写上限(forced 语义),用户裁决'],
  escalation: ['剧情规划反复多轮未过审', '细纲循环自动中断'],
  processPanel: ['AI 工作过程', '节点产出 / agent_call 时间线'],
  memoryPanel: ['世界记忆', 'facts / POV 记忆'],
  directive: ['告诉 AI 你的想法', '用户指令通道'],
  confirm: ['就这样定', '确认通过'],
  revise: ['我要调整', '要求修改'],
  confirmChapter: ['收下这一章', '确认定稿'],
  reviseChapter: ['让 AI 再改', '要求修改'],
  generate: ['开始写作', '开始生成'],
  continue: ['继续写作', '继续生成'],
  interruptRun: ['暂停', '中断(协作式)'],
  stopped: ['已暂停。进度已保存,随时继续', '已中断(断点保留,可续跑)'],
  // 阶段名(时间线/流水线共用)
  stages: {
    coauthor: ['构想世界观', '共创世界观'],
    init_characters: ['设计角色', '角色设计'],
    persist_characters: ['角色定稿', '角色入库'],
    gen_master_outline: ['撰写全书大纲', '总大纲'],
    review_master_outline: ['评审全书大纲', '总大纲评审'],
    confirm_master_outline: ['等你确认大纲', '中断点0'],
    next_chapter: ['准备下一章', '章节边界'],
    stage_outline: ['规划本阶段剧情', '阶段细纲'],
    review_stage_outline: ['评审阶段剧情', '细纲评审'],
    confirm_stage_outline: ['等你确认规划', '中断点A'],
    chapter_slice: ['提炼本章要点', '细纲切片'],
    build_context: ['翻阅世界记忆', '检索上下文'],
    write_draft: ['撰写正文', '正文写作'],
    review_draft_outline: ['大纲一致性审读', '成稿大纲评审'],
    review_quality: ['质量审校', '质量评审'],
    merge_reviews: ['汇总裁决', 'merge'],
    user_review_chapter: ['等你过目', '中断点B'],
    event_extract: ['记入世界记忆', '事实抽取'],
    update_characters: ['更新角色小传', '角色卡更新'],
    summary: ['本章摘要', '章摘要'],
    finalize: ['定稿归档', '定稿落库'],
    stage_summary: ['阶段记忆归档', '阶段聚合摘要'],
  },
  // LLM 调用(时间线紧凑行/抽屉)
  llmStages: {
    coauthor: ['世界观构想', '共创'],
    master_outline: ['全书大纲', '总大纲'],
    review_master_outline: ['大纲评审', '总大纲评审'],
    stage_outline: ['阶段剧情规划', '细纲'],
    review_stage_outline: ['规划评审', '细纲评审'],
    chapter_slice: ['本章要点', '切片'],
    draft: ['正文写作', '写作'],
    review_draft_outline: ['一致性审读', '大纲评审'],
    review_quality: ['质量审校', '审校'],
    extract_facts: ['记忆入库', '事实抽取'],
    update_characters: ['角色小传', '角色更新'],
    chapter_summary: ['本章摘要', '摘要'],
    stage_summary: ['阶段归档', '阶段聚合'],
    init_characters: ['角色设计', '角色'],
    embed: ['向量化', 'embedding'],
  },
  verdict: { pass: '通过', revise: '需修改', block: '否决', needs_user: '转交给你' },
  factType: { event: '事件', state: '状态', setting: '场景', relation: '关系' },
}

/* devMode:开发者视图(切回工程术语;可见性不变) */
export function makeT(devMode) {
  const idx = devMode ? 1 : 0
  const t = (entry) => (Array.isArray(entry) ? entry[idx] : entry)
  t.stage = (key) => (COPY.stages[key] ? COPY.stages[key][idx] : key)
  t.llm = (key) => (COPY.llmStages[key] ? COPY.llmStages[key][idx] : key)
  t.verdict = (v) => COPY.verdict[v] || v
  t.factType = (v) => COPY.factType[v] || v
  return t
}
