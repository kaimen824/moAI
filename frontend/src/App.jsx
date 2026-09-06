import { useEffect, useRef, useState } from 'react'
import {
  Badge, Button, Card, Checkbox, Code, DataList, Flex, Heading, ScrollArea,
  Separator, Spinner, Table, Tabs, Text, TextArea, TextField, Tooltip,
} from '@radix-ui/themes'
import {
  BookOpenIcon, CheckCircleIcon, CheckIcon, XCircleIcon,
  GearIcon, LightningIcon, ListIcon, MagnifyingGlassIcon, PencilSimpleIcon,
  PlayIcon, BookOpenTextIcon, PaperPlaneTiltIcon, StarIcon,
} from '@phosphor-icons/react'
import { api } from './api.js'
import Landing from './Landing.jsx'
import { TEMPLATES } from './templates.js'

/* 模板芯片:点选填入构想 */
function TemplateChips({ onPick }) {
  return (
    <Flex gap="2" wrap="wrap" align="center">
      <Text size="1" color="gray">找灵感:</Text>
      {TEMPLATES.map(t => (
        <Tooltip key={t.tag} content={t.title}>
          <Text as="span" size="1" weight="medium" style={{
            cursor: 'pointer', padding: '4px 12px', borderRadius: 999,
            background: 'var(--accent-a3)', color: 'var(--accent-11)',
            border: '1px solid var(--accent-a5)', userSelect: 'none',
            transition: 'background .15s',
          }}
          onMouseEnter={e => e.currentTarget.style.background = 'var(--accent-a5)'}
          onMouseLeave={e => e.currentTarget.style.background = 'var(--accent-a3)'}
          onClick={() => onPick(t.tag)}>
            #{t.tag}
          </Text>
        </Tooltip>
      ))}
    </Flex>
  )
}

/* ================= 书库 ================= */
function Library({ onOpen }) {
  const [stories, setStories] = useState([])
  const [title, setTitle] = useState('')
  const [premise, setPremise] = useState('')

  const refresh = () => api.listStories().then(setStories).catch(() => {})
  useEffect(() => { refresh() }, [])

  const create = async () => {
    if (!title.trim()) return
    const s = await api.createStory(title, premise)
    setTitle(''); setPremise(''); refresh(); onOpen(s.story_id)
  }

  return (
    <Flex direction="column" gap="4" style={{ maxWidth: 720 }}>
      <Card size="3">
        <Flex direction="column" gap="3">
          <Heading size="4">新建小说</Heading>
          <Flex gap="3" wrap="wrap">
            <TextField.Root size="2" placeholder="书名" value={title}
              style={{ width: 200 }} onChange={e => setTitle(e.target.value)} />
            <TextField.Root size="2" placeholder="一句话简介(可选)" value={premise}
              style={{ flex: 1, minWidth: 220 }} onChange={e => setPremise(e.target.value)} />
            <Button size="2" onClick={create} disabled={!title.trim()}>
              <PencilSimpleIcon size={14} weight="bold" /> 创建并开始共创
            </Button>
          </Flex>
        </Flex>
      </Card>

      <Card size="3">
        <Flex direction="column" gap="3">
          <Flex align="center" gap="2">
            <BookOpenIcon size={16} />
            <Heading size="4">书库</Heading>
            <Badge color="gray" variant="soft">{stories.length}</Badge>
          </Flex>
          {stories.map(s => (
            <Card key={s.id} size="2" variant="surface" style={{ cursor: 'pointer' }}
              onClick={() => onOpen(s.id)}>
              <Flex align="center" justify="between">
                <Text size="3" weight="medium">{s.title}</Text>
                <Badge color={s.status === 'active' ? 'grass' : 'gray'} variant="soft">{s.status}</Badge>
              </Flex>
            </Card>
          ))}
          {!stories.length && <Text size="2" color="gray">暂无,先创建一本</Text>}
        </Flex>
      </Card>
    </Flex>
  )
}

/* ================= 生成控制台(核心) ================= */
const STAGE_LABELS = {
  coauthor: '共创世界观', init_characters: '设计角色', persist_characters: '角色入库',
  gen_master_outline: '撰写总大纲', review_master_outline: '评审总大纲',
  confirm_master_outline: '等你确认大纲', next_chapter: '准备章节',
  stage_outline: '展开阶段细纲', review_stage_outline: '评审细纲',
  confirm_stage_outline: '等你确认细纲', chapter_slice: '切分本章要点',
  build_context: '检索记忆上下文', write_draft: '撰写正文',
  review_draft_outline: '大纲一致性评审', review_quality: '质量审校',
  merge_reviews: '汇总裁决', user_review_chapter: '等你审阅章节',
  event_extract: '抽取事实入库', update_characters: '更新角色卡',
  summary: '生成章摘要', finalize: '定稿落库',
}
const PIPELINE = ['coauthor', 'gen_master_outline', 'stage_outline', 'write_draft',
  'merge_reviews', 'user_review_chapter', 'finalize']
const PIPELINE_LABELS = { coauthor: '共创', gen_master_outline: '大纲', stage_outline: '细纲',
  write_draft: '写作', merge_reviews: '评审', user_review_chapter: '审阅', finalize: '定稿' }

function PayloadView({ payload }) {
  if (!payload || !Object.keys(payload).length) return null
  const els = []
  const textBlock = (label, v) => (
    <Flex key={label} direction="column" gap="1" style={{ marginBottom: 10 }}>
      <Text size="1" color="gray">{label}</Text>
      <Text as="div" size="2" style={{ whiteSpace: 'pre-wrap', lineHeight: 1.8 }}>{v}</Text>
    </Flex>
  )
  if (payload.world_settings) els.push(textBlock('世界观设定', payload.world_settings))
  if (payload.master_outline) els.push(textBlock('总大纲', payload.master_outline))
  if (payload.stage_outline) els.push(textBlock('阶段细纲', payload.stage_outline))
  if (payload.chapter_brief) els.push(textBlock('本章要点', payload.chapter_brief))
  if (payload.chapter_summary) els.push(textBlock('章摘要(检索索引)', payload.chapter_summary))
  if (payload.character_drafts?.length) els.push(
    <Flex key="chars" direction="column" gap="1" style={{ marginBottom: 10 }}>
      <Text size="1" color="gray">角色卡</Text>
      {payload.character_drafts.map((c, i) => (
        <Flex key={i} gap="2" align="baseline">
          <Text size="2" weight="bold">{c.name}</Text>
          <Text size="2" color="gray">{c.profile}</Text>
        </Flex>
      ))}
    </Flex>
  )
  if (payload.character_changes?.length) els.push(
    <Flex key="chchg" direction="column" gap="1" style={{ marginBottom: 10 }}>
      <Text size="1" color="gray">角色状态更新</Text>
      {payload.character_changes.map((u, i) => (
        <Flex key={i} gap="2" align="baseline">
          <Text size="2" weight="bold">{u.name}</Text>
          <Text size="2" color="gray">+ {u.profile_append}</Text>
        </Flex>
      ))}
    </Flex>
  )
  for (const [rk, label] of [['outline_review', '大纲评审'], ['quality_review', '质量审校']]) {
    const r = payload[rk]
    if (r) els.push(
      <Flex key={rk} direction="column" gap="1" style={{ marginBottom: 10 }}>
        <Text size="1" color="gray">{label}</Text>
        <Flex gap="2" align="center" wrap="wrap">
          <Badge color={r.verdict === 'pass' ? 'grass' : r.verdict === 'block' ? 'red' : 'amber'}>
            {r.verdict}
          </Badge>
          {r.scores && <Text size="1" color="gray">
            {Object.entries(r.scores).map(([k, v]) => `${k} ${v}`).join(' · ')}
          </Text>}
        </Flex>
        {r.feedback && <Text size="2">{r.feedback}</Text>}
      </Flex>
    )
  }
  if (payload.merged_verdict) els.push(
    <Flex key="mv" gap="2" align="center">
      <Text size="1" color="gray">汇总裁决</Text>
      <Badge color={payload.merged_verdict === 'pass' ? 'grass'
        : payload.merged_verdict === 'forced_pass' ? 'red' : 'amber'}>
        {payload.merged_verdict}
      </Badge>
    </Flex>
  )
  if (payload.fact_changes?.facts?.length || payload.fact_changes?.beliefs?.length) {
    const fc = payload.fact_changes
    els.push(
      <Flex key="facts" direction="column" gap="1" style={{ marginBottom: 10 }}>
        <Text size="1" color="gray">事实抽取(暂存变更集)</Text>
        {(fc.facts || []).map((f, i) => (
          <Flex key={i} gap="2" align="baseline">
            <Badge color={f.confidence === 'high' ? 'grass' : 'amber'}>{f.confidence}</Badge>
            <Text size="2">{f.content}</Text>
          </Flex>
        ))}
        {(fc.beliefs || []).map((b, i) => (
          <Flex key={'b' + i} gap="2" align="baseline">
            <Badge color="blue">认知</Badge>
            <Text size="2">{b.character}:{b.content}</Text>
          </Flex>
        ))}
      </Flex>
    )
  }
  if (payload.thread_changes?.length) els.push(
    <Flex key="th" direction="column" gap="1" style={{ marginBottom: 10 }}>
      <Text size="1" color="gray">伏笔变更建议</Text>
      {payload.thread_changes.map((t, i) => (
        <Flex key={i} gap="2" align="baseline">
          <Badge color="violet">{t.action}</Badge>
          <Text size="2">{t.description}</Text>
        </Flex>
      ))}
    </Flex>
  )
  if (payload.context_stats?.user_directives > 0) els.push(
    <Flex key="ud" direction="column" gap="1" style={{ marginBottom: 10 }}>
      <Text size="1" color="gray">用户指示(本章生效)</Text>
      {(payload.user_directives || []).map((d, i) => (
        <Flex key={i} gap="2" align="baseline">
          <StarIcon size={13} weight="fill" color="#e0af68" />
          <Text size="2">{d}</Text>
        </Flex>
      ))}
    </Flex>
  )
  if (payload.context_stats) els.push(
    <Text key="cs" size="1" color="gray">
      检索上下文:在场角色 {payload.context_stats.present} · POV 事实 {payload.context_stats.pov_facts}
      · 认知 {payload.context_stats.beliefs} · 活跃伏笔 {payload.context_stats.threads}
      · 关联实体 {payload.context_stats.expanded_entities}
      {payload.context_stats.user_directives > 0 && ` · 用户指示 ${payload.context_stats.user_directives} 条`}
    </Text>
  )
  return <Flex direction="column">{els}</Flex>
}

const INTERRUPT_TITLES = {
  confirm_master_outline: '中断点 0 · 确认总大纲',
  confirm_stage_outline: '中断点 A · 确认阶段细纲',
  user_review_chapter: '中断点 B · 章节审阅',
}

function Console({ storyId }) {
  const [running, setRunning] = useState(false)
  const [stages, setStages] = useState([])
  const [draft, setDraft] = useState('')
  const [intr, setIntr] = useState(null)
  const [feedback, setFeedback] = useState('')
  const [picked, setPicked] = useState([])
  const [msg, setMsg] = useState('')
  const [initialInput, setInitialInput] = useState('')
  const [chapters, setChapters] = useState(1)
  const [existingChapters, setExistingChapters] = useState(0)
  const [elapsed, setElapsed] = useState(0)
  const [directive, setDirective] = useState('')
  const [directiveMsg, setDirectiveMsg] = useState('')
  const draftRef = useRef(null)

  useEffect(() => {
    draftRef.current?.scrollIntoView({ block: 'end', behavior: 'smooth' })
  }, [draft])

  useEffect(() => {
    if (!running) return
    const t = setInterval(() => setElapsed(e => e + 1), 1000)
    return () => clearInterval(t)
  }, [running])

  const onEvent = (kind, data) => {
    if (kind === 'stage') setStages(s => [...s, { node: data.node, t: Date.now(), payload: data.payload || {} }])
    else if (kind === 'token') setDraft(d => d + (data.text || ''))
    else if (kind === 'interrupt') { setIntr(data); setPicked((data.thread_changes || []).map((_, i) => i)); setRunning(false) }
    else if (kind === 'done') { setIntr(null); setMsg('本轮目标章节全部完成') }
    else if (kind === 'error') { setMsg('错误:' + (data.message || '')); setRunning(false) }
  }

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const detail = await api.storyDetail(storyId)
        if (!cancelled && detail.chapters?.length) {
          setExistingChapters(detail.chapters.length)
          setChapters(detail.chapters.length + 1)
        }
      } catch { /* 忽略 */ }
      let rs
      try { rs = await api.runState(storyId) } catch { return }
      if (cancelled || !rs || rs.status === 'idle') return
      for (const e of rs.events) onEvent(e.kind, e.data)
      if (rs.status === 'running') {
        setRunning(true); setElapsed(0)
        api.attach(storyId, onEvent).catch(() => {})
      }
    })()
    return () => { cancelled = true }
  }, [storyId])   // eslint-disable-line

  const start = async () => {
    setRunning(true); setStages([]); setDraft(''); setIntr(null); setMsg('')
    setElapsed(0)
    try {
      await api.generate(storyId, { target_chapters: chapters, initial_input: initialInput }, onEvent)
    } catch (e) { setMsg('错误:' + e.message) }
    setRunning(false)
  }

  const send = async (action) => {
    if (!intr) return
    setRunning(true); setMsg(''); setElapsed(0)
    if (action === 'revise') setDraft('')
    const payload = {
      action,
      feedback: action === 'revise' ? feedback : '',
      threads: (intr.thread_changes || []).filter((_, i) => picked.includes(i)),
    }
    setIntr(null); setFeedback('')
    try {
      await api.resume(storyId, payload, onEvent)
    } catch (e) { setMsg('错误:' + e.message) }
    setRunning(false)
  }

  const sendDirective = async () => {
    if (!directive.trim()) return
    try {
      const r = await api.directive(storyId, directive)
      setDirectiveMsg(`已记录(${r.pending} 条待生效),下一章生成时由主控消费`)
      setDirective('')
    } catch (e) { setDirectiveMsg('提交失败:' + e.message) }
  }

  const lastStage = stages.length ? stages[stages.length - 1].node : ''
  const curLabel = running
    ? (STAGE_LABELS[lastStage] || lastStage || '启动中') + '…'
    : (intr ? '等你操作' : (msg || '空闲'))
  const curPipeIdx = (() => {
    let idx = -1
    for (const s of stages) { const i = PIPELINE.indexOf(s.node); if (i > idx) idx = i }
    return idx
  })()
  const fmt = (sec) => `${String(Math.floor(sec / 60)).padStart(2, '0')}:${String(sec % 60).padStart(2, '0')}`

  return (
    <Flex direction="column" gap="4" style={{ width: '100%', minHeight: 'calc(100dvh - 56px)' }}>
      {/* 指令通道 */}
      <Card size="2">
        <Flex gap="3" align="center" wrap="wrap">
          <TextField.Root size="2" style={{ flex: 1, minWidth: 260 }}
            placeholder="随时告诉主控你的想法(下一章加入新角色 / 节奏加快 / 回收伏笔)…"
            value={directive} onChange={e => setDirective(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && sendDirective()} />
          <Button size="2" variant="surface" onClick={sendDirective} disabled={!directive.trim()}>
            <PaperPlaneTiltIcon size={14} /> 提交指示
          </Button>
        </Flex>
        {directiveMsg && <Text size="1" color="gray" style={{ marginTop: 6 }}>{directiveMsg}</Text>}
      </Card>

      <Card size="3">
        <Flex direction="column" gap="3">
          <Flex align="center" gap="2">
            <LightningIcon size={16} weight="fill" color="#e0af68" />
            <Heading size="4">生成控制台</Heading>
            <Code size="1">{storyId.slice(0, 8)}</Code>
            {existingChapters > 0 && <Badge color="grass" variant="soft">续写模式 · 已有 {existingChapters} 章</Badge>}
          </Flex>

          {existingChapters === 0 && (
            <Flex direction="column" gap="2">
              <TextArea size="2" rows="3" placeholder="世界观构想(共创起点):基调 / 核心冲突 / 角色构想……"
                value={initialInput} onChange={e => setInitialInput(e.target.value)} />
              <TemplateChips onPick={t => setInitialInput(t)} />
            </Flex>
          )}

          <Flex gap="3" align="center" wrap="wrap">
            <Text size="2" color="gray">{existingChapters > 0 ? '生成到第' : '目标章数'}</Text>
            <TextField.Root size="2" type="number" style={{ width: 76 }}
              value={chapters} min={existingChapters + 1}
              onChange={e => setChapters(+e.target.value)} />
            {existingChapters > 0 && <Text size="2" color="gray">章</Text>}
            <Button onClick={start} disabled={running || !!intr}>
              {running ? <Spinner size="1" /> : <PlayIcon size={14} weight="bold" />}
              {running ? '生成中' : existingChapters > 0 ? '继续生成' : '开始生成'}
            </Button>
            {msg && <Text size="1" color="gray">{msg}</Text>}
          </Flex>

          {/* 运行状态 */}
          {(running || intr) && (
            <Flex align="center" gap="2" px="3" py="2"
              style={{
                borderRadius: 8,
                background: running ? 'var(--grass-a3)' : 'var(--amber-a3)',
              }}>
              {running
                ? <Spinner size="1" />
                : <StarIcon size={14} weight="fill" color="#e0af68" />}
              <Text size="2" weight="medium">{curLabel}</Text>
              <Text size="1" color="gray" style={{ marginLeft: 'auto' }}>
                已耗时 {fmt(elapsed)}{draft.length > 0 && ` · 已写出 ${draft.length} 字`}
              </Text>
            </Flex>
          )}

          {/* 流水线 */}
          {(running || intr || stages.length > 0) && (
            <Flex gap="2" wrap="wrap">
              {PIPELINE.map((p, i) => (
                <Flex key={p} align="center" gap="1" px="2" py="1"
                  style={{
                    borderRadius: 999,
                    border: `1px solid ${i === curPipeIdx ? 'var(--accent-a7)' : 'var(--gray-a5)'}`,
                    background: i === curPipeIdx ? 'var(--accent-a3)' : i < curPipeIdx ? 'var(--grass-a2)' : 'transparent',
                  }}>
                  {i < curPipeIdx
                    ? <CheckIcon size={12} weight="bold" color="var(--grass-11)" />
                    : <Text size="1">{i + 1}</Text>}
                  <Text size="1" weight={i === curPipeIdx ? 'bold' : 'regular'}>{PIPELINE_LABELS[p]}</Text>
                </Flex>
              ))}
            </Flex>
          )}
        </Flex>
      </Card>

      {/* 正文流 */}
      {draft && (
        <Card size="3">
          <Flex direction="column" gap="2">
            <Flex align="center" gap="2">
              <BookOpenTextIcon size={16} />
              <Heading size="4">正文(实时流式)</Heading>
              <Badge color="gray" variant="soft">{draft.length} 字</Badge>
            </Flex>
            <Text as="div" size="3" ref={draftRef}
              style={{ whiteSpace: 'pre-wrap', lineHeight: 2.1, minHeight: '48vh' }}>{draft}</Text>
          </Flex>
        </Card>
      )}

      {/* 中断卡 */}
      {intr && (
        <Card size="3" style={{ borderColor: 'var(--amber-a7)', background: 'var(--amber-a2)' }}>
          <Flex direction="column" gap="3">
            <Flex align="center" gap="2">
              <StarIcon size={16} weight="fill" color="#e0af68" />
              <Heading size="4" color="amber">{INTERRUPT_TITLES[intr.type] || intr.type}</Heading>
            </Flex>

            {(intr.outline || intr.stage_outline) && (
              <ScrollArea scrollbars="vertical" style={{ maxHeight: '55vh' }}>
                <Text as="div" size="2" style={{ whiteSpace: 'pre-wrap', lineHeight: 1.9 }}>
                  {intr.outline || intr.stage_outline}
                </Text>
              </ScrollArea>
            )}

            {intr.review && (
              <Flex direction="column" gap="1" p="3" style={{
                borderRadius: 8, background: 'var(--gray-a3)',
              }}>
                <Flex gap="2" align="center" wrap="wrap">
                  <Text size="1" color="gray">评审 Agent 意见</Text>
                  <Badge color={intr.review.verdict === 'pass' ? 'grass'
                    : intr.review.verdict === 'block' ? 'red' : 'amber'}>
                    {intr.review.verdict}
                  </Badge>
                  {intr.review.scores && <Text size="1" color="gray">
                    {Object.entries(intr.review.scores).map(([k, v]) => `${k} ${v}/10`).join(' · ')}
                  </Text>}
                </Flex>
                {intr.review.feedback && (
                  <Text size="2" style={{ whiteSpace: 'pre-wrap' }}>{intr.review.feedback}</Text>
                )}
              </Flex>
            )}

            {intr.type === 'user_review_chapter' && (
              <Flex direction="column" gap="2">
                {(intr.outline_review?.feedback || intr.quality_review?.feedback) && (
                  <Text size="1" color="gray">
                    大纲评审 {intr.outline_review?.verdict} · 质量审校 {intr.quality_review?.verdict}
                    {intr.forced_pass && <Text size="1" color="red" weight="bold"> · ⚠ 强制通过(已达重写上限)</Text>}
                  </Text>
                )}
                {(intr.thread_changes || []).length > 0 && (
                  <Flex direction="column" gap="1">
                    <Text size="1" color="gray">伏笔变更(勾选后随定稿生效):</Text>
                    {intr.thread_changes.map((t, i) => (
                      <Text key={i} as="label" size="2">
                        <Checkbox checked={picked.includes(i)}
                          onCheckedChange={c => setPicked(p => c ? [...p, i] : p.filter(x => x !== i))} />
                        {'  '}[{t.action}] {t.description}
                      </Text>
                    ))}
                  </Flex>
                )}
              </Flex>
            )}

            <Flex gap="3" align="center" wrap="wrap">
              <Button color="grass" onClick={() => send('confirm')}>
                <CheckCircleIcon size={14} weight="bold" />
                {intr.type === 'user_review_chapter' ? '确认定稿' : '确认通过'}
              </Button>
              <TextField.Root size="2" style={{ flex: 1, minWidth: 200 }}
                placeholder="修改意见(填写后点「要求修改」)"
                value={feedback} onChange={e => setFeedback(e.target.value)} />
              <Button color="amber" variant="soft" onClick={() => send('revise')} disabled={!feedback.trim()}>
                <PencilSimpleIcon size={14} /> 要求修改
              </Button>
            </Flex>
          </Flex>
        </Card>
      )}

      {/* 节点产出时间线 */}
      {stages.length > 0 && (
        <Card size="3">
          <Flex direction="column" gap="3">
            <Flex align="center" gap="2">
              <ListIcon size={16} />
              <Heading size="4">节点产出</Heading>
            </Flex>
            {stages.map((s, i) => (
              <Card key={i} size="2" variant="surface">
                <Flex direction="column" gap="2">
                  <Flex align="center" gap="2">
                    <Text size="2" weight="bold">{STAGE_LABELS[s.node] || s.node}</Text>
                    <Text size="1" color="gray" style={{ marginLeft: 'auto' }}>
                      {new Date(s.t).toLocaleTimeString('zh-CN', { hour12: false })}
                    </Text>
                  </Flex>
                  <PayloadView payload={s.payload} />
                </Flex>
              </Card>
            ))}
            {running && (
              <Flex align="center" gap="2">
                <Spinner size="1" />
                <Text size="2" color="gray">模型调用中(长文生成 1-3 分钟,计时器在走即正常)</Text>
              </Flex>
            )}
          </Flex>
        </Card>
      )}
    </Flex>
  )
}

/* ================= 阅读器 ================= */
function Reader({ storyId }) {
  const [detail, setDetail] = useState(null)
  const [current, setCurrent] = useState(null)
  const refresh = () => api.storyDetail(storyId).then(setDetail).catch(() => {})
  useEffect(() => { refresh() }, [storyId])   // eslint-disable-line
  const open = async (no) => {
    const ch = await api.chapter(storyId, no)
    setCurrent(ch); refresh()
  }

  return (
    <Flex gap="4" align="start" style={{ flexWrap: 'wrap' }}>
      <Card size="2" style={{ width: 260, flexShrink: 0 }}>
        <Flex direction="column" gap="3">
          <Heading size="3">章节</Heading>
          {detail?.chapters?.map(c => (
            <Text key={c.id} size="2" as="div" style={{
              cursor: 'pointer', padding: '4px 8px', borderRadius: 6,
              background: current?.chapter_no === c.chapter_no ? 'var(--accent-a3)' : 'transparent',
            }} onClick={() => open(c.chapter_no)}>
              第 {c.chapter_no} 章
            </Text>
          ))}
          {!detail?.chapters?.length && <Text size="2" color="gray">还没有已定稿章节</Text>}
          <Separator size="4" />
          <Heading size="3">伏笔</Heading>
          {detail?.plot_threads?.map(t => (
            <Flex key={t.id} gap="2" align="baseline">
              <Badge color={t.status === 'open' ? 'violet' : 'grass'}>{t.status}</Badge>
              <Text size="1">{t.description}</Text>
            </Flex>
          ))}
          <Separator size="4" />
          <Heading size="3">角色</Heading>
          <ScrollArea scrollbars="vertical" style={{ maxHeight: 260 }}>
            <Flex direction="column" gap="2">
              {detail?.characters?.map(c => (
                <Flex key={c.id} direction="column">
                  <Text size="2" weight="bold">{c.name}</Text>
                  <Text size="1" color="gray">{(c.profile || '').slice(0, 80)}</Text>
                </Flex>
              ))}
            </Flex>
          </ScrollArea>
        </Flex>
      </Card>
      <Card size="3" style={{ flex: 1, minWidth: 320 }}>
        {current
          ? <ScrollArea scrollbars="vertical" style={{ maxHeight: 640 }}>
              <Text as="div" size="3" style={{ whiteSpace: 'pre-wrap', lineHeight: 2.1, maxWidth: 720 }}>
                {current.content}
              </Text>
            </ScrollArea>
          : <Text color="gray">选择左侧章节阅读</Text>}
      </Card>
    </Flex>
  )
}

/* ================= 事实抽检 ================= */
function FactQueue() {
  const [facts, setFacts] = useState([])
  const refresh = () => api.pendingFacts().then(setFacts).catch(() => {})
  useEffect(() => { refresh() }, [])
  const review = async (fid, ok) => { await api.reviewFact(fid, ok); refresh() }

  return (
    <Card size="3" style={{ maxWidth: 900 }}>
      <Flex direction="column" gap="3">
        <Flex align="center" gap="2">
          <MagnifyingGlassIcon size={16} />
          <Heading size="4">低置信事实抽检队列</Heading>
          <Badge color={facts.length ? 'amber' : 'gray'} variant="soft">{facts.length}</Badge>
        </Flex>
        <Table.Root variant="surface">
          <Table.Header>
            <Table.Row>
              <Table.ColumnHeaderCell>来源</Table.ColumnHeaderCell>
              <Table.ColumnHeaderCell>内容</Table.ColumnHeaderCell>
              <Table.ColumnHeaderCell>章</Table.ColumnHeaderCell>
              <Table.ColumnHeaderCell>操作</Table.ColumnHeaderCell>
            </Table.Row>
          </Table.Header>
          <Table.Body>
            {facts.map(f => (
              <Table.Row key={f.id}>
                <Table.RowHeaderCell>{f.story_title}</Table.RowHeaderCell>
                <Table.Cell><Text size="2">{f.content}</Text></Table.Cell>
                <Table.Cell>{f.chapter_established}</Table.Cell>
                <Table.Cell>
                  <Flex gap="2">
                    <Button size="1" color="grass" variant="soft" onClick={() => review(f.id, true)}>
                      <CheckIcon size={12} /> 确认
                    </Button>
                    <Button size="1" color="red" variant="soft" onClick={() => review(f.id, false)}>
                      <XCircleIcon size={12} /> 拒绝
                    </Button>
                  </Flex>
                </Table.Cell>
              </Table.Row>
            ))}
          </Table.Body>
        </Table.Root>
        {!facts.length && <Text size="2" color="gray">队列为空</Text>}
      </Flex>
    </Card>
  )
}

/* ================= 模型配置 + 用量 ================= */
function Config({ storyId }) {
  const [models, setModels] = useState({})
  const [usage, setUsage] = useState([])
  useEffect(() => { api.models().then(setModels).catch(() => {}) }, [])
  useEffect(() => { if (storyId) api.usage(storyId).then(setUsage).catch(() => {}) }, [storyId])
  const save = async (role, model) => { await api.setModel(role, model); setModels(m => ({ ...m, [role]: model })) }

  return (
    <Flex direction="column" gap="4" style={{ maxWidth: 820 }}>
      <Card size="3">
        <Flex direction="column" gap="3">
          <Flex align="center" gap="2">
            <GearIcon size={16} />
            <Heading size="4">模型分级路由</Heading>
          </Flex>
          <Text size="1" color="gray">优先级:此处覆盖 &gt; 环境变量 &gt; 默认(强=glm-5 · 中/便宜=deepseek-v3 · embedding=qwen3.7)</Text>
          <DataList.Root size="2">
            {Object.entries(models).map(([role, model]) => (
              <DataList.Item key={role} align="center">
                <DataList.Label minWidth="110px">
                  <Code size="1">{role}</Code>
                </DataList.Label>
                <DataList.Value>
                  <TextField.Root size="1" defaultValue={model} style={{ width: 230 }}
                    onBlur={e => e.target.value !== model && save(role, e.target.value)} />
                </DataList.Value>
              </DataList.Item>
            ))}
          </DataList.Root>
        </Flex>
      </Card>
      {storyId && (
        <Card size="3">
          <Flex direction="column" gap="3">
            <Heading size="4">用量(按 Agent)</Heading>
            <Flex gap="3" wrap="wrap">
              {usage.map((u, i) => (
                <Card key={i} size="2" variant="surface" style={{ width: 190 }}>
                  <Flex direction="column" gap="1">
                    <Text size="2" weight="bold">{u.agent}</Text>
                    <Code size="1">{u.model}</Code>
                    <Text size="1" color="gray">
                      调用 {u.calls} 次 · {(u.tin || 0) + (u.tout || 0)} tokens
                    </Text>
                    <Text size="1" color="gray">{((u.latency || 0) / 1000).toFixed(1)}s 累计</Text>
                  </Flex>
                </Card>
              ))}
              {!usage.length && <Text size="2" color="gray">暂无调用</Text>}
            </Flex>
          </Flex>
        </Card>
      )}
    </Flex>
  )
}

/* ================= App ================= */
export default function App() {
  const [route, setRoute] = useState(window.location.hash || '#/')
  const [tab, setTab] = useState('library')
  const [storyId, setStoryId] = useState(null)

  useEffect(() => {
    const onHash = () => setRoute(window.location.hash || '#/')
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  if (route !== '#/app') {
    return <Landing onEnter={() => { window.location.hash = '#/app' }} />
  }

  return (
    <Flex style={{ minHeight: '100dvh' }} gap="0" align="stretch">
      <Flex direction="column" gap="1" p="4" style={{
        width: 208, flexShrink: 0, borderRight: '1px solid var(--gray-a5)',
        position: 'sticky', top: 0, height: '100dvh',
      }}>
        <Heading size="4" style={{ marginBottom: 16 }}>
          <a href="#/" style={{ color: 'inherit', textDecoration: 'none' }}>墨澜工作台</a>
        </Heading>
        {[
          { key: 'library', label: '书库', icon: <BookOpenIcon size={15} /> },
          { key: 'console', label: '生成', icon: <LightningIcon size={15} /> },
          { key: 'reader', label: '阅读', icon: <BookOpenTextIcon size={15} /> },
          { key: 'facts', label: '抽检', icon: <MagnifyingGlassIcon size={15} /> },
          { key: 'config', label: '配置', icon: <GearIcon size={15} /> },
        ].map(t => (
          <Flex key={t.key} align="center" gap="2" px="3" py="2" style={{
            borderRadius: 8, cursor: 'pointer', textDecoration: 'none',
            background: tab === t.key ? 'var(--accent-a3)' : 'transparent',
            color: tab === t.key ? 'var(--accent-11)' : 'var(--gray-11)',
          }} onClick={() => setTab(t.key)}>
            {t.icon}<Text size="2" weight={tab === t.key ? 'medium' : 'regular'}>{t.label}</Text>
          </Flex>
        ))}
        {storyId && (
          <Text size="1" color="gray" style={{ marginTop: 24 }}>
            当前书<br /><Code size="1">{storyId.slice(0, 14)}…</Code>
          </Text>
        )}
      </Flex>
      <Flex p="3" style={{ flex: 1, minWidth: 0, alignItems: 'flex-start' }}>
        {tab === 'library' && <Library onOpen={id => { setStoryId(id); setTab('console') }} />}
        {tab === 'console' && (storyId
          ? <Console storyId={storyId} />
          : <Text color="gray">先在书库创建/选择一本小说</Text>)}
        {tab === 'reader' && (storyId ? <Reader storyId={storyId} /> : <Text color="gray">未选书</Text>)}
        {tab === 'facts' && <FactQueue />}
        {tab === 'config' && <Config storyId={storyId} />}
      </Flex>
    </Flex>
  )
}
