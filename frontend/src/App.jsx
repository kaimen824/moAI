import { useEffect, useRef, useState } from 'react'
import { api } from './api.js'
import Landing from './Landing.jsx'

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
    <div>
      <div className="panel">
        <h3>新建小说</h3>
        <div className="row">
          <input placeholder="书名" value={title} onChange={e => setTitle(e.target.value)} />
          <input placeholder="一句话简介(可选)" value={premise} onChange={e => setPremise(e.target.value)} style={{ flex: 1 }} />
          <button onClick={create}>创建并开始共创</button>
        </div>
      </div>
      <div className="panel">
        <h3>书库({stories.length})</h3>
        {stories.map(s => (
          <div key={s.id} className="story-item" onClick={() => onOpen(s.id)}>
            <div>{s.title}</div>
            <div className="muted">{s.status}</div>
          </div>
        ))}
        {!stories.length && <div className="muted">暂无,先创建一本</div>}
      </div>
    </div>
  )
}

/* ================= 生成控制台(核心) ================= */
const INTERRUPT_TITLES = {
  confirm_master_outline: '中断点 0 · 确认总大纲',
  confirm_stage_outline: '中断点 A · 确认阶段细纲',
  user_review_chapter: '中断点 B · 章节审阅',
}

/* 节点名 -> 人话 + 流水线步骤(用于进度条) */
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
const PIPELINE = [
  'coauthor', 'gen_master_outline', 'stage_outline', 'write_draft',
  'merge_reviews', 'user_review_chapter', 'finalize',
]
const PIPELINE_LABELS = {
  coauthor: '共创', gen_master_outline: '大纲', stage_outline: '细纲',
  write_draft: '写作', merge_reviews: '评审', user_review_chapter: '审阅', 'finalize': '定稿',
}

/* 节点产出渲染:按 payload 字段类型渲染成卡片内容 */
function PayloadView({ payload }) {
  if (!payload || !Object.keys(payload).length) return null
  const els = []
  const textBlock = (label, v) => (
    <div key={label} style={{ marginBottom: 8 }}>
      <div className="muted" style={{ marginBottom: 4 }}>{label}</div>
      <pre className="pv-pre">{v}</pre>
    </div>
  )
  if (payload.world_settings) els.push(textBlock('世界观设定', payload.world_settings))
  if (payload.master_outline) els.push(textBlock('总大纲', payload.master_outline))
  if (payload.stage_outline) els.push(textBlock('阶段细纲', payload.stage_outline))
  if (payload.chapter_brief) els.push(textBlock('本章要点', payload.chapter_brief))
  if (payload.chapter_summary) els.push(textBlock('章摘要(检索索引)', payload.chapter_summary))
  if (payload.character_drafts?.length) els.push(
    <div key="chars" style={{ marginBottom: 8 }}>
      <div className="muted" style={{ marginBottom: 4 }}>角色卡</div>
      {payload.character_drafts.map((c, i) => (
        <div key={i} className="pv-char"><b>{c.name}</b><span className="muted"> {c.profile}</span></div>
      ))}
    </div>
  )
  if (payload.character_changes?.length) els.push(
    <div key="chchg" style={{ marginBottom: 8 }}>
      <div className="muted" style={{ marginBottom: 4 }}>角色状态更新</div>
      {payload.character_changes.map((u, i) => (
        <div key={i} className="pv-char"><b>{u.name}</b><span className="muted"> +{u.profile_append}</span></div>
      ))}
    </div>
  )
  for (const [rk, label] of [['outline_review', '大纲评审'], ['quality_review', '质量审校']]) {
    const r = payload[rk]
    if (r) els.push(
      <div key={rk} style={{ marginBottom: 8 }}>
        <div className="muted" style={{ marginBottom: 4 }}>{label}</div>
        <div className="pv-review">
          <span className={'badge ' + (r.verdict === 'pass' ? 'ok' : 'pending')}>
            {r.verdict}
          </span>
          {r.scores && <span className="muted" style={{ marginLeft: 8 }}>
            {Object.entries(r.scores).map(([k, v]) => `${k} ${v}`).join(' · ')}
          </span>}
          {r.feedback && <div style={{ marginTop: 4 }}>{r.feedback}</div>}
        </div>
      </div>
    )
  }
  if (payload.merged_verdict) els.push(
    <div key="mv" className="row">
      <span className="muted">汇总裁决:</span>
      <span className={'badge ' + (payload.merged_verdict === 'pass' ? 'ok' : 'pending')}>
        {payload.merged_verdict}
      </span>
    </div>
  )
  if (payload.fact_changes?.facts?.length || payload.fact_changes?.beliefs?.length) {
    const fc = payload.fact_changes
    els.push(
      <div key="facts" style={{ marginBottom: 8 }}>
        <div className="muted" style={{ marginBottom: 4 }}>事实抽取(暂存变更集)</div>
        {(fc.facts || []).map((f, i) => (
          <div key={i} className="pv-char">
            <span className={'badge ' + (f.confidence === 'high' ? 'ok' : 'pending')}>{f.confidence}</span>
            <span style={{ marginLeft: 6 }}>{f.content}</span>
          </div>
        ))}
        {(fc.beliefs || []).map((b, i) => (
          <div key={'b' + i} className="pv-char">
            <span className="badge open">认知</span>
            <span style={{ marginLeft: 6 }}>{b.character}:{b.content}</span>
          </div>
        ))}
      </div>
    )
  }
  if (payload.thread_changes?.length) els.push(
    <div key="th" style={{ marginBottom: 8 }}>
      <div className="muted" style={{ marginBottom: 4 }}>伏笔变更建议</div>
      {payload.thread_changes.map((t, i) => (
        <div key={i} className="pv-char"><span className="badge open">{t.action}</span>
          <span style={{ marginLeft: 6 }}>{t.description}</span></div>
      ))}
    </div>
  )
  if (payload.context_stats) els.push(
    <div key="cs" className="muted">
      检索上下文:在场角色 {payload.context_stats.present} · POV 事实 {payload.context_stats.pov_facts}
      · 认知 {payload.context_stats.beliefs} · 活跃伏笔 {payload.context_stats.threads}
      · 关联实体 {payload.context_stats.expanded_entities}
    </div>
  )
  return <div>{els}</div>
}

function Console({ storyId }) {
  const [running, setRunning] = useState(false)
  const [stages, setStages] = useState([])       // [{node, t}]
  const [draft, setDraft] = useState('')
  const [intr, setIntr] = useState(null)
  const [feedback, setFeedback] = useState('')
  const [picked, setPicked] = useState([])       // 勾选确认的伏笔
  const [msg, setMsg] = useState('')
  const [initialInput, setInitialInput] = useState('')
  const [chapters, setChapters] = useState(1)
  const [existingChapters, setExistingChapters] = useState(0)
  const [elapsed, setElapsed] = useState(0)
  const [tokenCount, setTokenCount] = useState(0)
  const draftRef = useRef(null)

  useEffect(() => { draftRef.current?.scrollTo(0, draftRef.current.scrollHeight) }, [draft])

  // 运行计时器:一秒一跳,证明前端活着
  useEffect(() => {
    if (!running) return
    const t = setInterval(() => setElapsed(e => e + 1), 1000)
    return () => clearInterval(t)
  }, [running])

  const onEvent = (kind, data) => {
    if (kind === 'stage') setStages(s => [...s, { node: data.node, t: Date.now(), payload: data.payload || {} }])
    else if (kind === 'token') { setDraft(d => d + (data.text || '')); setTokenCount(c => c + 1) }
    else if (kind === 'interrupt') { setIntr(data); setPicked((data.thread_changes || []).map((_, i) => i)); setRunning(false) }
    else if (kind === 'done') { setIntr(null); setMsg('本轮目标章节全部完成'); }
    else if (kind === 'error') { setMsg('错误:' + (data.message || '')); setRunning(false) }
  }

  // 会话恢复 + 续写模式识别:已有 active 章节 -> 续写(隐藏共创输入,target 默认 +1)
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const detail = await api.storyDetail(storyId)
        if (!cancelled && detail.chapters?.length) {
          const n = detail.chapters.length
          setExistingChapters(n)
          setChapters(n + 1)
        }
      } catch { /* 详情失败不阻塞 */ }
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
    setElapsed(0); setTokenCount(0)
    try {
      await api.generate(storyId, { target_chapters: chapters, initial_input: initialInput }, onEvent)
    } catch (e) { setMsg('错误:' + e.message) }
    setRunning(false)
  }

  const send = async (action) => {
    if (!intr) return
    setRunning(true); setMsg(''); setElapsed(0)
    if (action === 'revise') { setDraft(''); setTokenCount(0) }
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

  const lastStage = stages.length ? stages[stages.length - 1].node : ''
  const curLabel = running
    ? (STAGE_LABELS[lastStage] || lastStage || '启动中') + '…'
    : (intr ? '等你操作' : (msg || '空闲'))
  // 流水线进度:当前所处步骤(最后一个命中 PIPELINE 的阶段)
  const curPipeIdx = (() => {
    let idx = -1
    for (const s of stages) { const i = PIPELINE.indexOf(s.node); if (i > idx) idx = i }
    return idx
  })()

  const fmt = (sec) => `${String(Math.floor(sec / 60)).padStart(2, '0')}:${String(sec % 60).padStart(2, '0')}`

  return (
    <div>
      <div className="panel">
        <h3>
          生成控制台 · {storyId.slice(0, 8)}
          {existingChapters > 0 && (
            <span className="badge ok" style={{ marginLeft: 10 }}>
              续写模式 · 已有 {existingChapters} 章
            </span>
          )}
        </h3>
        {existingChapters === 0 && (
          <div className="row">
            <textarea rows="2" style={{ flex: 1 }} placeholder="世界观构想(共创访谈起点):基调/核心冲突/角色构想……"
              value={initialInput} onChange={e => setInitialInput(e.target.value)} />
          </div>
        )}
        <div className="row" style={{ marginTop: 8 }}>
          <span className="muted">{existingChapters > 0 ? '生成到第' : '目标章数'}</span>
          <input type="number" min={existingChapters + 1} max={99} value={chapters}
            onChange={e => setChapters(+e.target.value)} style={{ width: 70 }} />
          {existingChapters > 0 && <span className="muted">章</span>}
          <button onClick={start} disabled={running || !!intr}>
            {running ? '生成中…' : existingChapters > 0 ? '继续生成' : '开始生成'}
          </button>
          {msg && <span className="muted">{msg}</span>}
        </div>

        {/* 运行状态条:状态灯 + 当前阶段 + 计时 */}
        {(running || intr) && (
          <div className={'run-status ' + (running ? 'is-running' : 'is-waiting')}>
            <span className="dot" />
            <b>{curLabel}</b>
            <span className="muted" style={{ marginLeft: 'auto' }}>
              已耗时 {fmt(elapsed)}
              {tokenCount > 0 && ` · 已写出 ${draft.length} 字`}
            </span>
          </div>
        )}

        {/* 流水线步骤 */}
        {(running || intr || stages.length > 0) && (
          <div className="pipe">
            {PIPELINE.map((p, i) => (
              <div key={p} className={'pipe-step ' + (i < curPipeIdx ? 'done' : i === curPipeIdx ? 'cur' : '')}>
                <span className="pipe-idx">{i < curPipeIdx ? '✓' : i + 1}</span>
                {PIPELINE_LABELS[p]}
              </div>
            ))}
          </div>
        )}

        {/* 节点产出时间线:每个节点的实际产出都可读 */}
        {stages.length > 0 && (
          <div className="timeline">
            {stages.map((s, i) => (
              <div key={i} className="tl-item">
                <div className="tl-head">
                  <span className="tl-dot" />
                  <b>{STAGE_LABELS[s.node] || s.node}</b>
                  <span className="muted">{new Date(s.t).toLocaleTimeString('zh-CN', { hour12: false })}</span>
                </div>
                <div className="tl-body"><PayloadView payload={s.payload} /></div>
              </div>
            ))}
            {running && (
              <div className="tl-head cur-stage">
                <span className="tl-dot pulsing" />
                <b>模型调用中</b>
                <span className="muted">长文生成需 1-3 分钟,计时器在走即正常</span>
              </div>
            )}
          </div>
        )}
      </div>

      {draft && (
        <div className="panel">
          <h3>正文(实时流式)</h3>
          <div className="draft-stream" ref={draftRef}>{draft}</div>
        </div>
      )}

      {intr && (
        <div className="panel">
          <div className="interrupt-card">
            <h4>{INTERRUPT_TITLES[intr.type] || intr.type}</h4>
            {intr.outline && <pre>{intr.outline}</pre>}
            {intr.stage_outline && <pre>{intr.stage_outline}</pre>}
            {intr.type === 'user_review_chapter' && (
              <>
                {(intr.outline_review?.feedback || intr.quality_review?.feedback) && (
                  <div className="muted" style={{ margin: '8px 0' }}>
                    大纲评审:{intr.outline_review?.verdict} · 质量评审:{intr.quality_review?.verdict}
                    {intr.forced_pass && <span style={{ color: 'var(--err)' }}> · ⚠ 强制通过(已达重写上限)</span>}
                  </div>
                )}
                {(intr.thread_changes || []).length > 0 && (
                  <div style={{ margin: '10px 0' }}>
                    <div className="muted">伏笔变更(人工二次确认,勾选后随定稿生效):</div>
                    {intr.thread_changes.map((t, i) => (
                      <label key={i} className="thread-check">
                        <input type="checkbox" checked={picked.includes(i)}
                          onChange={e => setPicked(p => e.target.checked ? [...p, i] : p.filter(x => x !== i))} />
                        <span>[{t.action}] {t.description}</span>
                      </label>
                    ))}
                  </div>
                )}
              </>
            )}
            <div className="row" style={{ marginTop: 12 }}>
              <button onClick={() => send('confirm')}>
                {intr.type === 'user_review_chapter' ? '确认定稿' : '确认通过'}
              </button>
              <input placeholder="修改意见(选填,填写后点'要求修改'" style={{ flex: 1 }}
                value={feedback} onChange={e => setFeedback(e.target.value)} />
              <button className="warn" onClick={() => send('revise')} disabled={!feedback.trim()}>
                要求修改
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

/* ================= 阅读器 ================= */
function Reader({ storyId }) {
  const [detail, setDetail] = useState(null)
  const [current, setCurrent] = useState(null)
  const refresh = () => api.storyDetail(storyId).then(setDetail).catch(() => {})
  useEffect(() => { refresh() }, [storyId])

  const open = async (no) => {
    const ch = await api.chapter(storyId, no)
    setCurrent(ch); refresh()
  }

  return (
    <div className="reader-layout">
      <div className="panel" style={{ marginBottom: 0 }}>
        <h3>章节</h3>
        {detail?.chapters?.map(c => (
          <div key={c.id} className={'chapter-list-item' + (current?.chapter_no === c.chapter_no ? ' active' : '')}
            onClick={() => open(c.chapter_no)}>
            第 {c.chapter_no} 章
          </div>
        ))}
        {!detail?.chapters?.length && <div className="muted">还没有已定稿章节</div>}
        <h3 style={{ marginTop: 16 }}>伏笔</h3>
        {detail?.plot_threads?.map(t => (
          <div key={t.id} style={{ margin: '6px 0' }}>
            <span className={`badge ${t.status}`}>{t.status}</span> {t.description}
          </div>
        ))}
        <h3 style={{ marginTop: 16 }}>角色</h3>
        {detail?.characters?.map(c => (
          <div key={c.id} style={{ margin: '6px 0' }}><b>{c.name}</b>
            <div className="muted">{(c.profile || '').slice(0, 60)}</div>
          </div>
        ))}
      </div>
      <div className="panel">
        {current ? <div className="chapter-content">{current.content}</div> : <div className="muted">选择左侧章节阅读</div>}
      </div>
    </div>
  )
}

/* ================= 事实抽检 ================= */
function FactQueue() {
  const [facts, setFacts] = useState([])
  const refresh = () => api.pendingFacts().then(setFacts).catch(() => {})
  useEffect(() => { refresh() }, [])
  const review = async (fid, ok) => { await api.reviewFact(fid, ok); refresh() }

  return (
    <div className="panel">
      <h3>低置信事实抽检队列(E3 置信度分层)</h3>
      <table>
        <thead><tr><th>来源</th><th>内容</th><th>章节</th><th>操作</th></tr></thead>
        <tbody>
          {facts.map(f => (
            <tr key={f.id}>
              <td className="muted">{f.story_title}</td>
              <td>{f.content}</td>
              <td className="muted">{f.chapter_established}</td>
              <td>
                <button onClick={() => review(f.id, true)}>确认</button>{' '}
                <button className="ghost" onClick={() => review(f.id, false)}>拒绝</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {!facts.length && <div className="muted">队列为空</div>}
    </div>
  )
}

/* ================= 模型配置 + 用量 ================= */
function Config({ storyId }) {
  const [models, setModels] = useState({})
  const [usage, setUsage] = useState([])
  useEffect(() => { api.models().then(setModels); }, [])
  useEffect(() => { if (storyId) api.usage(storyId).then(setUsage).catch(() => {}) }, [storyId])

  const save = async (role, model) => { await api.setModel(role, model); setModels(m => ({ ...m, [role]: model })) }

  return (
    <div>
      <div className="panel">
        <h3>模型分级路由(ADR-0008 · 优先级:此处覆盖 &gt; 环境变量 &gt; 默认)</h3>
        <table>
          <thead><tr><th>Agent 角色</th><th>当前模型</th><th></th></tr></thead>
          <tbody>
            {Object.entries(models).map(([role, model]) => (
              <tr key={role}>
                <td className="mono">{role}</td>
                <td><input defaultValue={model} style={{ width: 220 }}
                  onBlur={e => e.target.value !== model && save(role, e.target.value)} /></td>
                <td className="muted">失焦保存</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {storyId && (
        <div className="panel">
          <h3>用量(按 Agent · 分级路由实验数据源)</h3>
          <div className="usage-grid">
            {usage.map((u, i) => (
              <div key={i} className="usage-card">
                <b>{u.agent}</b> <span className="muted mono">{u.model}</span>
                <div className="muted">调用 {u.calls} 次 · {u.tin || 0}+{u.tout || 0} tokens</div>
                <div className="muted">{((u.latency || 0) / 1000).toFixed(1)}s 累计</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

/* ================= App ================= */
const TABS = [
  { key: 'library', label: '书库' },
  { key: 'console', label: '生成' },
  { key: 'reader', label: '阅读' },
  { key: 'facts', label: '抽检' },
  { key: 'config', label: '配置' },
]

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
    <div className="app">
      <div className="sidebar">
        <h1><a href="#/" style={{ color: 'inherit', textDecoration: 'none' }}>小说 Agent</a></h1>
        {TABS.map(t => (
          <div key={t.key} className={'nav-item' + (tab === t.key ? ' active' : '')}
            onClick={() => setTab(t.key)}>{t.label}</div>
        ))}
        {storyId && <div className="muted" style={{ margin: '20px 16px' }}>当前书<br />{storyId.slice(0, 12)}…</div>}
      </div>
      <div className="main">
        {tab === 'library' && <Library onOpen={id => { setStoryId(id); setTab('console') }} />}
        {tab === 'console' && (storyId
          ? <Console storyId={storyId} />
          : <div className="muted">先在书库创建/选择一本小说</div>)}
        {tab === 'reader' && (storyId ? <Reader storyId={storyId} /> : <div className="muted">未选书</div>)}
        {tab === 'facts' && <FactQueue />}
        {tab === 'config' && <Config storyId={storyId} />}
      </div>
    </div>
  )
}
