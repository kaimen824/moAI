import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ReactFlow, ReactFlowProvider, Background, Controls, MiniMap, Panel, MarkerType, useReactFlow,
} from '@xyflow/react'
import { ArrowDown, ArrowRight, CornersOut } from '@phosphor-icons/react'
import NodeCard, { KIND } from './NodeCard.jsx'
import { layoutGraph } from './layout.js'
import { getGraph, getThreads, getRun, getState } from './api.js'

const nodeTypes = { card: NodeCard, term: NodeCard, groupbox: GroupBox }

/* 阶段分组容器:纯背景(不可交互、完全穿透),只画边界与组名 */
function GroupBox({ data }) {
  return (
    <div className="gbox" style={{ width: '100%', height: '100%', borderColor: `${data.accent}44` }}>
      {data.zh && (
        <div className="gbox-title" style={{ color: data.accent }}>
          <span className="gbox-chip" style={{ background: `${data.accent}33`, borderColor: `${data.accent}66` }} />
          {data.zh}
        </div>
      )}
    </div>
  )
}

export default function App() {
  return (
    <ReactFlowProvider>
      <Canvas />
    </ReactFlowProvider>
  )
}

function Canvas() {
  const [graph, setGraph] = useState(null)
  const [threads, setThreads] = useState([])
  const [tid, setTid] = useState(null)
  const [run, setRun] = useState(null)
  const [pos, setPos] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(700)
  const [detail, setDetail] = useState(null)
  const [showDiff, setShowDiff] = useState(false)     // 状态 diff 手动开关(默认不显示)
  const [selNode, setSelNode] = useState(null)
  const [dir, setDir] = useState('TB')
  const [toast, setToast] = useState('')
  const versionRef = useRef(null)
  const { fitView } = useReactFlow()
  const autoFitRef = useRef(true)

  const flash = useCallback((msg) => {
    setToast(msg)
    setTimeout(() => setToast(''), 2500)
  }, [])

  /* ---- 数据轮询:结构(代码)与线程(库) ---- */
  useEffect(() => {
    const t = setInterval(async () => {
      try {
        const g = await getGraph()
        if (g.version && g.version !== versionRef.current) {
          const first = versionRef.current === null
          versionRef.current = g.version
          setGraph(g)
          autoFitRef.current = true
          if (!first) flash(`图结构已更新 → ${g.version}`)
        }
      } catch { /* serve 不在,继续轮询 */ }
    }, 2000)
    return () => clearInterval(t)
  }, [flash])

  useEffect(() => {
    const load = async () => { try { setThreads(await getThreads()) } catch { /* */ } }
    load()
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
  }, [])

  useEffect(() => {
    if (!tid) return
    setRun(null); setDetail(null); setSelNode(null); setPlaying(false)
    let dead = false
    getRun(tid).then((r) => {
      if (dead) return
      setRun(r)
      setPos(Math.max(r.steps.length - 1, 0))
    }).catch(() => { /* */ })
    return () => { dead = true }
  }, [tid])

  /* ---- 播放 ---- */
  useEffect(() => {
    if (!playing || !run) return
    const t = setInterval(() => {
      setPos((p) => {
        if (p >= run.steps.length - 1) { setPlaying(false); return p }
        return p + 1
      })
    }, speed)
    return () => clearInterval(t)
  }, [playing, speed, run])

  /* ---- 当前步 state 详情(仅在用户打开 diff 开关后拉取) ---- */
  useEffect(() => {
    const step = run?.steps[pos]
    if (!step || !showDiff) { setDetail(null); return }
    let dead = false
    getState(tid, step.checkpoint_id)
      .then((d) => { if (!dead) setDetail(d) })
      .catch(() => { if (!dead) setDetail(null) })
    return () => { dead = true }
  }, [tid, run, pos, showDiff])

  /* ---- 重放推导:执行计数 / 边是否走过 / 当前活跃 / 中断 ---- */
  const flow = useMemo(() => {
    if (!run) return null
    const upto = run.steps.slice(0, pos + 1)
    const execCount = {}
    const interruptedEver = new Set()
    const interruptedAt = {}
    const errorNodes = new Set()
    const edgeTakenAt = {}
    upto.forEach((s) => {
      s.executed.forEach((n) => { execCount[n] = (execCount[n] || 0) + 1 })
      if (s.interrupt?.node) { interruptedEver.add(s.interrupt.node); interruptedAt[s.interrupt.node] = s.i }
      if (s.error) s.executed.forEach((n) => errorNodes.add(n))
      s.edges.forEach((to) => {
        if (s.executed.length) s.executed.forEach((src) => { edgeTakenAt[`${src}->${to}`] = s.i })
        else edgeTakenAt[`__start__->${to}`] = s.i       // input 步:START 出边
      })
    })
    const activeSet = new Set(upto[upto.length - 1]?.executed || [])
    const atEnd = pos >= run.steps.length - 1
    return {
      execCount, interruptedEver, interruptedAt, errorNodes, edgeTakenAt, activeSet, atEnd,
    }
  }, [run, pos])

  /* ---- 布局(仅结构/方向变化时重算;dagre 确定性,同输入同输出) ---- */
  const layout = useMemo(
    () => (graph ? layoutGraph(graph, dir) : null),
    [graph, dir],
  )

  /* ---- 画布元素 ---- */
  const nodes = useMemo(() => {
    if (!graph || !layout) return []
    const groupNodes = layout.boxes.filter((b) => b.zh).map((b) => ({
      id: `grp:${b.key}`,
      type: 'groupbox',
      position: { x: b.x, y: b.y },
      data: { zh: b.zh, accent: b.accent },
      style: { width: b.w, height: b.h, pointerEvents: 'none' },
      draggable: false, selectable: false, deletable: false,
      zIndex: 0,
    }))
    const positions = layout.positions
    const cards = graph.nodes.map((n) => {
      let status = null                                    // null = 结构模式(无运行信息)
      if (flow && n.kind !== 'terminal') {
        if (flow.errorNodes.has(n.id)) status = 'error'
        else if (flow.atEnd && run?.stopped_at?.node === n.id) status = 'interrupt'
        else if (flow.interruptedAt[n.id] === pos) status = 'interrupt'
        else if (flow.activeSet.has(n.id)) status = 'active'
        else if (flow.execCount[n.id]) status = 'done'
        else status = 'pending'
      }
      return {
        id: n.id,
        type: n.kind === 'terminal' ? 'term' : 'card',
        position: positions[n.id] || { x: 0, y: 0 },
        zIndex: 1,
        data: {
          id: n.id, label: n.label, kind: n.kind, status, meta: n.meta,
          count: flow?.execCount[n.id] || 0,
          interruptedEver: flow?.interruptedEver.has(n.id) || false,
        },
      }
    })
    return [...groupNodes, ...cards]
  }, [graph, layout, flow, run, pos])

  const edges = useMemo(() => {
    if (!graph) return []
    return graph.edges.map((e) => {
      const key = `${e.source}->${e.target}`
      const dash = e.conditional ? '6 4' : undefined
      const base = {
        id: key, source: e.source, target: e.target,
        type: 'smoothstep', pathOptions: { borderRadius: 14 },
        label: e.label || undefined,
        labelStyle: { fill: '#93a5c0', fontSize: 10.5, fontFamily: 'inherit', fontWeight: 500 },
        labelBgStyle: { fill: '#0d1626', fillOpacity: 0.96, stroke: '#233450', strokeWidth: 1 },
        labelBgPadding: [4, 2], labelBgBorderRadius: 4,
      }
      if (!flow) {                                         // 结构模式:统一中性边
        return {
          ...base,
          style: { stroke: '#46689b', strokeWidth: 1.6, strokeDasharray: dash },
          markerEnd: { type: MarkerType.ArrowClosed, color: '#46689b', width: 15, height: 15 },
        }
      }
      const at = flow.edgeTakenAt[key]
      const now = at !== undefined && at === pos
      const stroke = at === undefined ? '#2a3a52' : now ? '#38bdf8' : '#5d7fae'
      return {
        ...base,
        animated: now,
        style: { stroke, strokeWidth: at !== undefined ? 2.4 : 1.4, strokeDasharray: dash },
        labelStyle: { ...base.labelStyle, fill: now ? '#7dd3fc' : '#93a5c0' },
        markerEnd: { type: MarkerType.ArrowClosed, color: stroke, width: 15, height: 15 },
      }
    })
  }, [graph, flow, pos])

  /* 布局/结构变化后自动 fit;用户一旦手动挪动就不再抢视口 */
  useEffect(() => {
    if (graph && autoFitRef.current) {
      const t = setTimeout(() => fitView({ padding: 0.06, duration: 300 }), 60)
      return () => clearTimeout(t)
    }
  }, [graph, dir, fitView])

  /* ---- 键盘 ←→ 步进,空格播放 ---- */
  useEffect(() => {
    const onKey = (ev) => {
      if (ev.target.tagName === 'INPUT' || ev.target.tagName === 'SELECT') return
      if (ev.key === 'ArrowRight') setPos((p) => Math.min(p + 1, (run?.steps.length || 1) - 1))
      else if (ev.key === 'ArrowLeft') setPos((p) => Math.max(p - 1, 0))
      else if (ev.key === ' ') { ev.preventDefault(); setPlaying((v) => !v) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [run])

  const step = run?.steps[pos]
  const jumpInterrupt = (delta) => {
    if (!run) return
    const idxs = run.steps.map((s, i) => (s.interrupt || s.error ? i : -1)).filter((i) => i >= 0)
    const next = delta > 0 ? idxs.find((i) => i > pos) : [...idxs].reverse().find((i) => i < pos)
    if (next !== undefined) setPos(next)
  }

  const mmColor = (n) => (n.type === 'groupbox'
    ? (n.data.accent || '#1b2940')
    : n.data.status == null
      ? (KIND[n.data.kind]?.c || '#3a4f6e')
      : n.data.status === 'active' ? '#38bdf8'
        : n.data.status === 'interrupt' || n.data.status === 'error' ? '#ef4444'
          : n.data.status === 'done' ? '#1f7a45' : '#3a4f6e')

  return (
    <div className="app">
      <aside className="side">
        <div className="side-title">
          {run ? '回放模式' : '结构模式'}
          {run && (
            <button className="exit-replay" onClick={() => { setTid(null); setRun(null); setShowDiff(false) }}>
              ✕ 退出回放
            </button>
          )}
        </div>
        {!run && <div className="side-note">仅展示图结构(随代码自动更新)。点选下方线程可进入运行回放。</div>}
        <div className="side-sub">运行线程</div>
        {!threads.length && <div className="empty">暂无(serve.py 在跑吗?)</div>}
        {threads.map((t) => (
          <button
            key={t.thread_id}
            className={`thread ${t.thread_id === tid ? 'sel' : ''}`}
            onClick={() => setTid(t.thread_id)}
          >
            <div className="t-id">{t.thread_id.slice(0, 10)}…</div>
            <div className="t-meta">{t.chapters} 章 · {t.steps} 步 · {(t.last_ts || '').slice(0, 16).replace('T', ' ')}</div>
            {t.stopped_at
              ? <div className="t-stop">◉ 停在中断点 {t.stopped_at.node}</div>
              : <div className="t-run">已走完/无挂起中断</div>}
          </button>
        ))}
        <div className="legend">
          <div className="lg-title">图例</div>
          {Object.entries(KIND).filter(([, v]) => v.label).map(([k, v]) => (
            <div key={k} className="lg-row">
              <span className="lg-ic" style={{ color: v.c }}>{v.Icon && <v.Icon size={13} weight="duotone" />}</span>
              {v.label}
            </div>
          ))}
          {run && (
            <>
              <div className="lg-row"><i className="lg-status" style={{ background: '#38bdf8' }} />当前步活跃</div>
              <div className="lg-row"><i className="lg-status" style={{ background: '#1f7a45' }} />已执行</div>
              <div className="lg-row"><i className="lg-status" style={{ background: '#ef4444' }} />中断/异常</div>
            </>
          )}
          <div className="lg-hint">滚轮缩放 · 拖拽平移{run ? ' · ←/→ 步进 · 空格播放 · 点节点看履历' : ''}</div>
        </div>
      </aside>

      <main className="main">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          onNodeClick={(_, n) => { if (n.type !== 'groupbox') setSelNode(n.id === selNode ? null : n.id) }}
          onPaneClick={() => setSelNode(null)}
          onMoveStart={() => { autoFitRef.current = false }}
          minZoom={0.08}
          proOptions={{ hideAttribution: true }}
        >
          <Background variant="dots" gap={26} color="#141d2e" />
          <Controls showInteractive={false} />
          <MiniMap pannable zoomable nodeColor={mmColor} nodeStrokeWidth={0} maskColor="rgba(3, 7, 15, 0.78)" style={{ background: '#0b1220', border: '1px solid var(--border)' }} />
          <Panel position="top-right" className="topbar">
            <button onClick={() => { autoFitRef.current = true; fitView({ padding: 0.06, duration: 300 }) }} title="适应视口">
              <CornersOut size={13} /> 适应
            </button>
            <button className={dir === 'TB' ? 'on' : ''} onClick={() => { setDir('TB'); autoFitRef.current = true }}>
              <ArrowDown size={13} /> 纵向
            </button>
            <button className={dir === 'LR' ? 'on' : ''} onClick={() => { setDir('LR'); autoFitRef.current = true }}>
              <ArrowRight size={13} /> 横向
            </button>
            <span className="ver">结构 {versionRef.current || '…'}</span>
          </Panel>
        </ReactFlow>

        {run && (
          <div className="timebar">
            <div className="tb-row1">
              <button onClick={() => setPos(0)} title="回到开头">⏮</button>
              <button onClick={() => jumpInterrupt(-1)} title="上一个中断/异常">◀◉</button>
              <button onClick={() => setPos((p) => Math.max(p - 1, 0))}>◀</button>
              <button className="play" onClick={() => setPlaying((v) => !v)}>{playing ? '⏸' : '▶'}</button>
              <button onClick={() => setPos((p) => Math.min(p + 1, run.steps.length - 1))}>▶</button>
              <button onClick={() => jumpInterrupt(1)} title="下一个中断/异常">◉▶</button>
              <input
                type="range" min={0} max={run.steps.length - 1} value={pos}
                onChange={(e) => { setPlaying(false); setPos(+e.target.value) }}
                className="scrub"
              />
              <span className="pos">{pos + 1}/{run.steps.length}</span>
              <button
                className={showDiff ? 'on' : ''}
                onClick={() => setShowDiff((v) => !v)}
                title="显示/隐藏当前步骤的状态 diff"
              >
                状态 diff
              </button>
              <select value={speed} onChange={(e) => setSpeed(+e.target.value)} title="播放速度">
                <option value={1400}>慢</option>
                <option value={700}>中</option>
                <option value={300}>快</option>
              </select>
            </div>
            <div className="tb-row2">
              {step ? (
                <>
                  <span className="chip">{step.source}</span>
                  <span className="tb-ts">{(step.ts || '').replace('T', ' ').slice(0, 19)}</span>
                  <span className="tb-exec">执行:{step.executed.join(', ') || '(输入)'}</span>
                  {step.edges.length > 0 && <span className="tb-edge">→ {step.edges.join(', ')}</span>}
                  {step.interrupt && <span className="tb-int">◉ {step.interrupt.node} 中断{step.interrupt.resumed ? '(已恢复)' : '(等待)'}</span>}
                  {step.error && <span className="tb-err">✗ {String(step.error).slice(0, 120)}</span>}
                </>
              ) : '—'}
            </div>
          </div>
        )}

        {selNode && run && <NodeHistory node={selNode} run={run} onClose={() => setSelNode(null)} />}

        {step && showDiff && (
          <div className="detail">
            <div className="d-title">
              步骤 {step.i} 状态
              <span className="d-ck">{step.checkpoint_id.slice(0, 8)}</span>
              <button className="d-close" onClick={() => setShowDiff(false)}>×</button>
            </div>
            {detail?.error
              ? <div className="d-err">{detail.error}</div>
              : detail ? (
                <div className="d-body">
                  {Object.entries(detail.diff).slice(0, 40).map(([k, v]) => (
                    <div key={k} className={`d-row ${v.op}`}>
                      <span className={`d-op ${v.op}`}>{v.op === 'added' ? '+' : v.op === 'removed' ? '−' : '±'}</span>
                      <span className="d-key">{k.replace('branch:to:', '⇢ ')}</span>
                      <span className="d-val">
                        {v.op !== 'removed' && stringify(v.after)}
                        {v.op === 'changed' && <><br /><span className="d-before">原:{stringify(v.before)}</span></>}
                      </span>
                    </div>
                  ))}
                  {!Object.keys(detail.diff).length && <div className="d-empty">本步无状态变化</div>}
                </div>
              )
                : <div className="d-empty">读取中…</div>}
          </div>
        )}

        {toast && <div className="toast">{toast}</div>}
      </main>
    </div>
  )
}

function stringify(v) {
  if (v === null || v === undefined) return '∅'
  const s = typeof v === 'string' ? v : JSON.stringify(v)
  return s.length > 160 ? s.slice(0, 160) + '…' : s
}

function NodeHistory({ node, run, onClose }) {
  const hits = run.steps.filter((s) => s.executed.includes(node)
    || s.interrupt?.node === node)
  return (
    <div className="nhist">
      <div className="d-title">{node}<span className="d-ck">{hits.length} 次执行</span>
        <button className="d-close" onClick={onClose}>×</button></div>
      <div className="nh-body">
        {hits.map((s) => (
          <div key={s.i} className="nh-row">
            <span className="nh-i">#{s.i}</span>
            <span className="nh-ts">{(s.ts || '').slice(5, 16).replace('T', ' ')}</span>
            {s.interrupt?.node === node && <span className="tb-int">◉中断</span>}
            {s.error && <span className="tb-err">✗</span>}
            <span className="nh-upd">{s.updated.filter((c) => !c.startsWith('branch:')).join(', ') || '—'}</span>
          </div>
        ))}
        {!hits.length && <div className="d-empty">本线程未执行过该节点</div>}
      </div>
    </div>
  )
}
