import { useEffect, useRef, useState } from 'react'
import {
  Badge, Button, Card, Checkbox, Flex, Heading, ScrollArea, Spinner,
  Tabs, Text, TextArea, TextField, Tooltip,
} from '@radix-ui/themes'
import {
  CheckCircleIcon, PaperPlaneTiltIcon, PencilSimpleIcon,
  PlayIcon, StarIcon, StopIcon, XCircleIcon,
} from '@phosphor-icons/react'
import { api } from '../api.js'
import { useApp } from '../App.jsx'
import { TEMPLATES } from '../templates.js'
import ChapterSidebar from './ChapterSidebar.jsx'
import ProcessPanel from './ProcessPanel.jsx'

/* 题材标签:多选 token */
function TemplateChips({ selected, onToggle }) {
  return (
    <Flex gap="2" wrap="wrap" align="center">
      <Text size="1" color="gray">题材:</Text>
      {TEMPLATES.map(t => {
        const on = selected.includes(t.tag)
        return (
          <Tooltip key={t.tag} content={t.title}>
            <Text as="span" size="1" weight="medium" style={{
              cursor: 'pointer', padding: '4px 12px', borderRadius: 999,
              background: on ? 'var(--accent-9)' : 'var(--gray-a3)',
              color: on ? 'white' : 'var(--gray-11)',
              border: `1px solid ${on ? 'var(--accent-9)' : 'var(--gray-a5)'}`,
              userSelect: 'none', transition: 'background .15s',
            }} onClick={() => onToggle(t.tag)}>
              #{t.tag}
            </Text>
          </Tooltip>
        )
      })}
    </Flex>
  )
}

/* 中断卡:三类确认点,术语按模式翻译;评审意见完整展示(透明原则) */
function InterruptCard({ intr, t, onSend, busy }) {
  const [feedback, setFeedback] = useState('')
  const [picked, setPicked] = useState(
    (intr.thread_changes || []).map((_, i) => i))
  const [outlineOpen, setOutlineOpen] = useState(false)
  const title = t(COPY_TITLES[intr.type] || [intr.type, intr.type])
  const isChapter = intr.type === 'user_review_chapter'
  const outlineText = intr.outline || intr.stage_outline || ''

  const send = (action) => onSend({
    action,
    feedback: action === 'revise' ? feedback : '',
    threads: (intr.thread_changes || []).filter((_, i) => picked.includes(i)),
  })

  return (
    <Card size="3" className="intr-card" style={{ position: 'relative', borderColor: 'var(--amber-a7)', background: 'var(--amber-a2)' }}>
      <Flex direction="column" gap="3">
        <Flex align="center" gap="2">
          <StarIcon size={16} weight="fill" color="#e0af68" />
          <Heading size="4" color="amber">{title}</Heading>
          {intr.regen_count > 1 && <Badge color="gray" variant="soft">第 {intr.regen_count} 次规划</Badge>}
        </Flex>

        {intr.escalation && (
          <Flex align="center" gap="2" p="3" style={{
            borderRadius: 8, background: 'var(--red-a3)', border: '1px solid var(--red-a7)',
          }}>
            <XCircleIcon size={15} color="var(--red-11)" weight="bold" />
            <Text size="2" color="red" weight="medium">
              {t.escalation}:{intr.escalation}
            </Text>
          </Flex>
        )}

        {outlineText && (
          <Flex direction="column" gap="1">
            <ScrollArea scrollbars="vertical" style={{ maxHeight: outlineOpen ? '50vh' : '7.5em' }}>
              <Text as="div" size="2" className="reader-serif"
                style={{ whiteSpace: 'pre-wrap', lineHeight: 1.9 }}>{outlineText}</Text>
            </ScrollArea>
            <Button size="1" variant="ghost" onClick={() => setOutlineOpen(o => !o)}
              style={{ alignSelf: 'flex-start' }}>
              {outlineOpen ? '收起' : '展开全文'}
            </Button>
          </Flex>
        )}

        {intr.review && (
          <Flex direction="column" gap="1" p="3" style={{ borderRadius: 8, background: 'var(--gray-a3)' }}>
            <Flex gap="2" align="center" wrap="wrap">
              <Text size="1" color="gray">评审意见</Text>
              <Badge color={intr.review.verdict === 'pass' ? 'grass'
                : intr.review.verdict === 'block' ? 'red' : 'amber'}>
                {t.verdict(intr.review.verdict)}
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

        {isChapter && (
          <Flex direction="column" gap="2">
            {(intr.outline_review?.feedback || intr.quality_review?.feedback) && (
              <Flex direction="column" gap="1">
                <Text size="1" color="gray">两位审读 AI 的意见:</Text>
                {intr.outline_review?.feedback && (
                  <Text size="2" style={{ whiteSpace: 'pre-wrap' }}>
                    <b>一致性审读</b>({t.verdict(intr.outline_review.verdict)}):{intr.outline_review.feedback}
                  </Text>
                )}
                {intr.quality_review?.feedback && (
                  <Text size="2" style={{ whiteSpace: 'pre-wrap' }}>
                    <b>质量审校</b>({t.verdict(intr.quality_review.verdict)}):{intr.quality_review.feedback}
                  </Text>
                )}
              </Flex>
            )}
            {intr.rewrite_exhausted && (
              <Text size="2" color="red" weight="medium">⚠ {t.needsUser}</Text>
            )}
            {(intr.thread_changes || []).length > 0 && (
              <Flex direction="column" gap="1">
                <Text size="1" color="gray">本章埋设/推进的伏笔(勾选后随定稿登记):</Text>
                {intr.thread_changes.map((th, i) => (
                  <Text key={i} as="label" size="2">
                    <Checkbox checked={picked.includes(i)}
                      onCheckedChange={c => setPicked(p => c ? [...p, i] : p.filter(x => x !== i))} />
                    {'  '}[{th.action}] {th.description}
                  </Text>
                ))}
              </Flex>
            )}
          </Flex>
        )}

        <Flex gap="3" align="center" wrap="wrap" style={{
          position: 'sticky', bottom: 0, paddingTop: 8,
        }}>
          <Button color="grass" onClick={() => send('confirm')} disabled={busy}>
            <CheckCircleIcon size={14} weight="bold" />
            {isChapter ? t.confirmChapter : t.confirm}
          </Button>
          <TextField.Root size="2" style={{ flex: 1, minWidth: 200 }}
            placeholder="你的修改意见(填写后点右侧按钮)"
            value={feedback} onChange={e => setFeedback(e.target.value)} />
          <Button color="amber" variant="soft" onClick={() => send('revise')} disabled={!feedback.trim() || busy}>
            <PencilSimpleIcon size={14} /> {isChapter ? t.reviseChapter : t.revise}
          </Button>
        </Flex>
      </Flex>
    </Card>
  )
}

const COPY_TITLES = {
  confirm_master_outline: ['故事骨架已就绪', '中断点 0 · 确认总大纲'],
  confirm_stage_outline: ['本阶段剧情规划', '中断点 A · 确认阶段细纲'],
  user_review_chapter: ['AI 写完了一章,请你过目', '中断点 B · 章节审阅'],
}

/* ================= 创作工作台(三栏) ================= */
export default function Workbench({ storyId, onOpenCodex, onOpenReader }) {
  const { t } = useApp()
  const [running, setRunning] = useState(false)
  const [entries, setEntries] = useState([])      // 时间线条目 {kind:'stage'|'llm', ...}
  const [drafts, setDrafts] = useState([])        // [{chapter_no, round, text}]
  const [viewIdx, setViewIdx] = useState(-1)
  const [intr, setIntr] = useState(null)
  const [msg, setMsg] = useState('')
  const [initialInput, setInitialInput] = useState('')
  const [chapters, setChapters] = useState(1)
  const [detail, setDetail] = useState(null)      // {story, chapters, characters, plot_threads}
  const [existingChapters, setExistingChapters] = useState(0)
  const [elapsed, setElapsed] = useState(0)
  const [directive, setDirective] = useState('')
  const [directiveOpen, setDirectiveOpen] = useState(false)
  const [directiveMsg, setDirectiveMsg] = useState('')
  const [tags, setTags] = useState([])
  const [stopping, setStopping] = useState(false)
  const [rightOpen, setRightOpen] = useState(true)
  const curChapterRef = useRef(null)
  const draftRef = useRef(null)

  const latestDraft = drafts.length ? drafts[drafts.length - 1] : null
  const generatingNext = viewIdx < 0 && !!latestDraft && !latestDraft.text && drafts.length > 1
  const curDraft = viewIdx >= 0 ? drafts[viewIdx]
    : (generatingNext ? drafts[drafts.length - 2] : latestDraft)
  const curText = curDraft?.text || ''

  const refreshDetail = () => api.storyDetail(storyId).then(d => {
    setDetail(d)
    if (d.chapters?.length) setExistingChapters(d.chapters.length)
  }).catch(() => {})

  useEffect(() => { refreshDetail(); setChapters(1); setExistingChapters(0)
    api.storyDetail(storyId).then(d => {
      if (d.chapters?.length) { setExistingChapters(d.chapters.length); setChapters(d.chapters.length + 1) }
    }).catch(() => {}) }, [storyId])   // eslint-disable-line

  useEffect(() => {
    draftRef.current?.scrollIntoView({ block: 'end', behavior: 'smooth' })
  }, [curText])

  useEffect(() => {
    if (!running) return
    const timer = setInterval(() => setElapsed(e => e + 1), 1000)
    return () => clearInterval(timer)
  }, [running])

  const pushEntry = (e) => setEntries(es => {
    const last = es[es.length - 1]
    // 去重:SSE 与历史回放可能交叠,同 trace_id/节点+时间戳跳过
    if (last && last.kind === e.kind && last.t === e.t) return es
    return [...es, e]
  })

  const onEvent = (kind, data) => {
    if (kind === 'stage') {
      if (data.payload?.chapter_no != null) curChapterRef.current = data.payload.chapter_no
      pushEntry({ kind: 'stage', node: data.node, t: Date.now(),
        payload: data.payload || {}, ch: curChapterRef.current })
    }
    else if (kind === 'agent_call') {
      pushEntry({ kind: 'llm', t: Date.now(), llm: data, ch: curChapterRef.current })
    }
    else if (kind === 'draft_start') {
      curChapterRef.current = data.chapter_no
      pushEntry({ kind: 'draft_start', t: Date.now(), data, ch: data.chapter_no })
      setDrafts(ds => {
        const last = ds[ds.length - 1]
        if (last && last.chapter_no !== data.chapter_no) {
          return [{ chapter_no: data.chapter_no, round: data.round || 1, text: '' }]
        }
        return [...ds, { chapter_no: data.chapter_no, round: data.round || 1, text: '' }]
      })
      setViewIdx(-1)
    }
    else if (kind === 'token') setDrafts(ds => {
      if (!ds.length) return [{ chapter_no: curChapterRef.current, round: 1, text: data.text || '' }]
      const cur = ds[ds.length - 1]
      const next = [...ds]
      next[next.length - 1] = { ...cur, text: cur.text + (data.text || '') }
      return next
    })
    else if (kind === 'interrupt') { setIntr(data); setRunning(false); refreshDetail() }
    else if (kind === 'done') { setIntr(null); setMsg('本轮目标章节全部完成'); refreshDetail() }
    else if (kind === 'stopped') { setMsg(t.stopped); setRunning(false) }
    else if (kind === 'error') { setMsg('出错了:' + (data.message || '')); setRunning(false) }
    if (kind === 'stopped' || kind === 'error' || kind === 'interrupt') setStopping(false)
  }

  useEffect(() => {
    let cancelled = false
    ;(async () => {
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
    setRunning(true); setEntries([]); setDrafts([]); setViewIdx(-1)
    setIntr(null); setMsg(''); setElapsed(0); setStopping(false)
    curChapterRef.current = null
    try {
      await api.generate(storyId, { target_chapters: chapters, initial_input: initialInput, tags }, onEvent)
    } catch (e) { setMsg('出错了:' + e.message) }
    setRunning(false)
  }

  const send = async (payload) => {
    if (!intr) return
    setRunning(true); setMsg(''); setElapsed(0)
    setIntr(null)
    try {
      await api.resume(storyId, payload, onEvent)
    } catch (e) { setMsg('出错了:' + e.message) }
    setRunning(false)
  }

  const stopRun = async () => {
    try {
      await api.stop(storyId)
      setStopping(true)
      setMsg('已请求暂停,当前步骤收尾后停止(进度保留)')
    } catch (e) { setMsg('暂停失败:' + e.message) }
  }

  const sendDirective = async () => {
    if (!directive.trim()) return
    try {
      const r = await api.directive(storyId, directive)
      setDirectiveMsg(`已记录(${r.pending} 条待生效),下一章生成时生效`)
      setDirective('')
    } catch (e) { setDirectiveMsg('提交失败:' + e.message) }
  }

  const fmt = (sec) => `${String(Math.floor(sec / 60)).padStart(2, '0')}:${String(sec % 60).padStart(2, '0')}`
  const storyTitle = detail?.story?.title || ''

  return (
    <Flex gap="3" align="stretch" style={{ width: '100%', minHeight: 'calc(100dvh - 24px)' }}>
      {/* 左栏:章节目录 + 设定入口 */}
      <ChapterSidebar detail={detail} storyId={storyId}
        onOpenCodex={onOpenCodex} onOpenReader={onOpenReader} />

      {/* 中栏:工具条 + 开卷/正文/中断 */}
      <Flex direction="column" gap="3" style={{ flex: 1, minWidth: 0 }}>
        {/* 顶部工具条 */}
        <Card size="2" style={{ flexShrink: 0 }}>
          <Flex gap="3" align="center" wrap="wrap">
            <Text size="3" weight="bold" className="reader-serif">{storyTitle}</Text>
            {existingChapters > 0 && <Badge color="grass" variant="soft">已定稿 {existingChapters} 章</Badge>}
            {running && (
              <Flex align="center" gap="2">
                <Spinner size="1" />
                <Text size="1" color="gray">
                  第{curChapterRef.current || existingChapters + 1}章 · {fmt(elapsed)}
                  {generatingNext ? ` · 第${latestDraft.round}稿重写中`
                    : curText.length > 0 ? ` · 第${curDraft?.round || 1}稿 ${curText.length} 字` : ''}
                </Text>
              </Flex>
            )}
            {!running && !intr && msg && <Text size="1" color="gray">{msg}</Text>}

            <Flex gap="2" align="center" style={{ marginLeft: 'auto' }}>
              <Tooltip content={t.directive}>
                <Button size="2" variant={directiveOpen ? 'solid' : 'soft'} onClick={() => setDirectiveOpen(o => !o)}>
                  <PaperPlaneTiltIcon size={14} />
                  指示{directiveOpen ? '' : '…'}
                </Button>
              </Tooltip>
              <Text size="2" color="gray">写到第</Text>
              <TextField.Root size="2" type="number" style={{ width: 70 }}
                value={chapters} min={existingChapters + 1}
                onChange={e => setChapters(+e.target.value)} />
              <Text size="2" color="gray">章</Text>
              <Button onClick={start} disabled={running || !!intr}>
                {running ? <Spinner size="1" /> : <PlayIcon size={14} weight="bold" />}
                {running ? '写作中' : existingChapters > 0 ? t.continue : t.generate}
              </Button>
              {running && (
                <Button color="red" variant="soft" onClick={stopRun} disabled={stopping}>
                  <StopIcon size={14} weight="bold" />
                  {stopping ? '暂停中…' : t.interruptRun}
                </Button>
              )}
            </Flex>
          </Flex>
          {directiveOpen && (
            <Flex gap="2" align="center" wrap="wrap" mt="2">
              <TextField.Root size="2" style={{ flex: 1, minWidth: 240 }}
                placeholder="随时告诉 AI 你的想法(下一章加入新角色 / 节奏加快 / 回收伏笔)…"
                value={directive} onChange={e => setDirective(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && sendDirective()} />
              <Button size="2" variant="surface" onClick={sendDirective} disabled={!directive.trim()}>
                提交指示
              </Button>
              {directiveMsg && <Text size="1" color="gray">{directiveMsg}</Text>}
            </Flex>
          )}
        </Card>

        {/* 中断卡:出现时优先占据中栏(等待用户) */}
        {intr && <InterruptCard intr={intr} t={t} onSend={send} busy={running} />}

        {/* 开卷(新书首次):构想输入 */}
        {!intr && existingChapters === 0 && !curText && !running && (
          <Card size="3">
            <Flex direction="column" gap="3">
              <Flex align="center" gap="2">
                <Heading size="4" className="reader-serif">开卷</Heading>
                <Text size="1" color="gray">一段构想,AI 替你长成一部书</Text>
              </Flex>
              <div style={{ border: '1px solid var(--gray-a6)', borderRadius: 8 }}>
                <TextArea size="3" rows="4" variant="soft" style={{ boxShadow: 'none' }}
                  placeholder="书名想好了,故事呢?写下一句话构想(可选):基调 / 核心冲突 / 主角……"
                  value={initialInput} onChange={e => setInitialInput(e.target.value)} />
                <Flex gap="2" wrap="wrap" p="2" style={{ borderTop: '1px solid var(--gray-a4)' }}>
                  <TemplateChips selected={tags}
                    onToggle={tg => setTags(x => x.includes(tg) ? x.filter(i => i !== tg) : [...x, tg])} />
                </Flex>
              </div>
              <Button size="3" onClick={start} style={{ alignSelf: 'flex-start' }}>
                <PlayIcon size={15} weight="bold" /> {t.generate}
              </Button>
            </Flex>
          </Card>
        )}

        {/* 正文(多稿版本化,衬线,独立滚动) */}
        {drafts.length > 0 && curDraft && (
          <Card size="3" style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
            <Flex direction="column" gap="2" style={{ flex: 1, minHeight: 0 }}>
              {generatingNext && (
                <Flex align="center" gap="2" px="3" py="2" style={{
                  borderRadius: 8, background: 'var(--accent-a3)', flexShrink: 0,
                }}>
                  <Spinner size="1" />
                  <Text size="2" weight="medium">
                    按审读意见重写中(第{latestDraft.round}稿)——新稿产出前暂显示上一稿,内容未丢失
                  </Text>
                </Flex>
              )}
              <Flex align="center" gap="2" wrap="wrap" style={{ flexShrink: 0 }}>
                {drafts.length > 1 && (
                  <Flex gap="1" align="center">
                    {drafts.map((d, i) => (
                      <Text as="span" key={i} size="1" weight="medium" style={{
                        cursor: 'pointer', padding: '3px 10px', borderRadius: 999,
                        background: (viewIdx >= 0 ? viewIdx : drafts.length - 1) === i
                          ? 'var(--accent-9)' : 'var(--gray-a3)',
                        color: (viewIdx >= 0 ? viewIdx : drafts.length - 1) === i
                          ? 'white' : 'var(--gray-11)',
                      }} onClick={() => setViewIdx(i)}>
                        第{d.round}稿{d.round > 1 ? '(修订)' : ''}
                      </Text>
                    ))}
                    {viewIdx >= 0 && viewIdx < drafts.length - 1 && (
                      <Button size="1" variant="soft" onClick={() => setViewIdx(-1)}>回到最新稿</Button>
                    )}
                  </Flex>
                )}
                <Badge color="gray" variant="soft" style={{ marginLeft: 'auto' }}>
                  {drafts.length > 1 ? `第${curDraft.round}稿 · ` : ''}{curText.length} 字
                </Badge>
              </Flex>
              <ScrollArea scrollbars="vertical" style={{ flex: 1, minHeight: 0 }} type="hover">
                <Text key={`${viewIdx}-${drafts.length}`} as="div" size="4" ref={draftRef}
                  className="reader-serif draft-swap"
                  style={{
                    whiteSpace: 'pre-wrap', lineHeight: 2.1, maxWidth: 760,
                    margin: '0 auto', padding: '8px 4px',
                  }}>{curText}</Text>
              </ScrollArea>
            </Flex>
          </Card>
        )}
      </Flex>

      {/* 右栏:AI 工作过程——钉在视口(sticky),流式输出时状态始终可见 */}
      {rightOpen ? (
        <Flex direction="column" className="panel-anim" style={{
          width: 336, flexShrink: 0,
          position: 'sticky', top: 12,
          height: 'calc(100dvh - 24px)',
          alignSelf: 'flex-start',
        }}>
          <ProcessPanel entries={entries} running={running} storyId={storyId}
            onCollapse={() => setRightOpen(false)} />
        </Flex>
      ) : (
        <Tooltip content={`展开${t.processPanel}`} side="left">
          <Flex align="center" justify="center" style={{
            width: 36, flexShrink: 0, cursor: 'pointer',
            borderLeft: '1px solid var(--gray-a5)', color: 'var(--gray-11)',
          }} onClick={() => setRightOpen(true)}>
            <Text size="1" style={{ writingMode: 'vertical-rl' }}>{t.processPanel}</Text>
          </Flex>
        </Tooltip>
      )}
    </Flex>
  )
}
