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

function Console({ storyId }) {
  const [running, setRunning] = useState(false)
  const [stages, setStages] = useState([])
  const [draft, setDraft] = useState('')
  const [intr, setIntr] = useState(null)
  const [feedback, setFeedback] = useState('')
  const [picked, setPicked] = useState([])      // 勾选确认的伏笔
  const [msg, setMsg] = useState('')
  const [initialInput, setInitialInput] = useState('')
  const [chapters, setChapters] = useState(1)
  const draftRef = useRef(null)

  useEffect(() => { draftRef.current?.scrollTo(0, draftRef.current.scrollHeight) }, [draft])

  const onEvent = (kind, data) => {
    if (kind === 'stage') setStages(s => [...s, data.node])
    else if (kind === 'token') setDraft(d => d + (data.text || ''))
    else if (kind === 'interrupt') { setIntr(data); setPicked((data.thread_changes || []).map((_, i) => i)) }
    else if (kind === 'done') { setIntr(null); setMsg('本轮目标章节全部完成'); }
    else if (kind === 'error') { setMsg('错误:' + (data.message || '')); setRunning(false) }
  }
  const finish = () => setRunning(false)

  const start = async () => {
    setRunning(true); setStages([]); setDraft(''); setIntr(null); setMsg('')
    try {
      await api.generate(storyId, { target_chapters: chapters, initial_input: initialInput }, onEvent)
    } catch (e) { setMsg('错误:' + e.message) }
    finish()
  }

  const send = async (action) => {
    if (!intr) return
    setRunning(true); setMsg('')
    const payload = {
      action,
      feedback: action === 'revise' ? feedback : '',
      threads: (intr.thread_changes || []).filter((_, i) => picked.includes(i)),
    }
    setIntr(null); setFeedback('')
    if (action === 'revise') setDraft('')
    try {
      await api.resume(storyId, payload, onEvent)
    } catch (e) { setMsg('错误:' + e.message) }
    finish()
  }

  return (
    <div>
      <div className="panel">
        <h3>生成控制台 · {storyId.slice(0, 8)}</h3>
        <div className="row">
          <textarea rows="2" style={{ flex: 1 }} placeholder="世界观构想(共创访谈起点):基调/核心冲突/角色构想……"
            value={initialInput} onChange={e => setInitialInput(e.target.value)} />
        </div>
        <div className="row" style={{ marginTop: 8 }}>
          <span className="muted">目标章数</span>
          <input type="number" min="1" max="20" value={chapters}
            onChange={e => setChapters(+e.target.value)} style={{ width: 70 }} />
          <button onClick={start} disabled={running || !!intr}>
            {running ? '生成中…' : '开始生成'}
          </button>
          {msg && <span className="muted">{msg}</span>}
        </div>
        <div className="stage-log" style={{ marginTop: 10 }}>
          {stages.slice(-8).map((s, i, a) => (
            <div key={i} className={i === a.length - 1 ? 'cur' : ''}>▸ {s}</div>
          ))}
        </div>
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
