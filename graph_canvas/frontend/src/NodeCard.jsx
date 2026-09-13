import { memo } from 'react'
import { Handle, Position } from '@xyflow/react'

export const KIND = {
  interrupt: { c: '#ef4444', label: '人工中断' },
  review: { c: '#3b82f6', label: '评审' },
  agent: { c: '#22c55e', label: 'Agent' },
  route: { c: '#eab308', label: '路由' },
  terminal: { c: '#64748b', label: '' },
}

const STATUS = {
  pending: { border: '#263449', bg: '#0d1626', text: '#5b6b82' },
  done: { border: '#1f7a45', bg: '#0a2018', text: '#7fe0ae' },
  active: { border: '#38bdf8', bg: '#072a44', text: '#bae6fd' },
  interrupt: { border: '#f87171', bg: '#3f0d0d', text: '#fecaca' },
  error: { border: '#dc2626', bg: '#3f0d0d', text: '#fecaca' },
}

function NodeCardInner({ data, selected }) {
  const kind = KIND[data.kind] || KIND.agent
  // status 为 null = 结构模式:仅按节点类型着色,不带任何运行信息
  const st = data.status == null
    ? { border: kind.c, bg: '#0d1626', text: '#b6c2d4' }
    : (STATUS[data.status] || STATUS.pending)

  if (data.kind === 'terminal') {
    return (
      <div className={`nc term ${selected ? 'nc-sel' : ''}`} style={{ borderColor: '#33507a' }}>
        <Handle type="target" position={Position.Top} className="hd" />
        <span>{data.label}</span>
        <Handle type="source" position={Position.Bottom} className="hd" />
      </div>
    )
  }

  return (
    <div
      className={`nc ${selected ? 'nc-sel' : ''} ${data.status === 'active' ? 'nc-run' : ''} ${data.status === 'interrupt' ? 'nc-int' : ''}`}
      style={{ borderColor: st.border, background: st.bg, color: st.text }}
      title={data.id}
    >
      <Handle type="target" position={Position.Top} className="hd" />
      <div className="nc-head">
        <span className="nc-dot" style={{ background: kind.c }} />
        <span className="nc-name">{data.id}</span>
        {data.status != null && data.count > 1 && <span className="nc-count">×{data.count}</span>}
        {data.status != null && data.interruptedEver && <span className="nc-mark" title="发生过中断">◉</span>}
      </div>
      <div className="nc-kind" style={{ color: kind.c }}>
        {kind.label}
        {data.status === 'interrupt' && <b className="nc-pause"> · 暂停于此</b>}
      </div>
      <Handle type="source" position={Position.Bottom} className="hd" />
    </div>
  )
}

export default memo(NodeCardInner)
