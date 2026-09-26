import { memo } from 'react'
import { Handle, Position } from '@xyflow/react'
import {
  Article, ArrowsSplit, BookOpenText, Chats, Crosshair, Database, Eye,
  FastForward, Fingerprint, FloppyDisk, HandTap, Lightbulb, ListBullets,
  MagicWand, MagnifyingGlass, PaintBrush, PenNib, PencilSimple, Scissors,
  Stack, TextAa, TrafficSignal, UserFocus, Users,
} from '@phosphor-icons/react'

export const KIND = {
  interrupt: { c: '#ef4444', label: '人工中断', Icon: HandTap },
  review: { c: '#3b82f6', label: '评审', Icon: Eye },
  agent: { c: '#22c55e', label: 'Agent', Icon: PencilSimple },
  route: { c: '#eab308', label: '路由', Icon: ArrowsSplit },
  terminal: { c: '#64748b', label: '', Icon: null },
}

/* meta.icon(Phosphor 组件名)→ 组件;kind 兜底 */
const ICONS = {
  Article, ArrowsSplit, BookOpenText, Chats, Crosshair, Database, Eye,
  FastForward, Fingerprint, FloppyDisk, HandTap, Lightbulb, ListBullets,
  MagicWand, MagnifyingGlass, PaintBrush, PenNib, PencilSimple, Scissors,
  Stack, TextAa, TrafficSignal, UserFocus, Users,
}

const STATUS = {
  pending: { border: '#2c3c58', bg: '#0d1626', text: '#64748b' },
  done: { border: '#1f7a45', bg: '#0a2018', text: '#7fe0ae' },
  active: { border: '#38bdf8', bg: '#072a44', text: '#bae6fd' },
  interrupt: { border: '#f87171', bg: '#3f0d0d', text: '#fecaca' },
  error: { border: '#dc2626', bg: '#3f0d0d', text: '#fecaca' },
}

function NodeCardInner({ data, selected }) {
  const kind = KIND[data.kind] || KIND.agent
  const Icon = (data.meta?.icon && ICONS[data.meta.icon]) || kind.Icon
  const title = data.meta?.zh || data.label || data.id
  // status 为 null = 结构模式:仅按节点类型着色,不带任何运行信息
  const st = data.status == null
    ? { border: kind.c, bg: '#0d1626', text: '#c3cfe0' }
    : (STATUS[data.status] || STATUS.pending)

  if (data.kind === 'terminal') {
    return (
      <div className={`nc term ${selected ? 'nc-sel' : ''}`} style={{ borderColor: '#3d5a80' }}>
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
      title={data.meta?.desc ? `${data.meta.desc}(${data.id})` : data.id}
    >
      <Handle type="target" position={Position.Top} className="hd" />
      <div className="nc-main">
        <span className="nc-icon" style={{ color: kind.c }}>
          {Icon && <Icon size={15} weight="duotone" />}
        </span>
        <span className="nc-title">{title}</span>
        {data.status != null && data.count > 1 && <span className="nc-count">×{data.count}</span>}
        {data.status != null && data.interruptedEver && <span className="nc-mark" title="发生过中断">◉</span>}
      </div>
      <div className="nc-sub">
        <span className="nc-id">{data.id}</span>
        <span className="nc-kind" style={{ color: kind.c }}>{kind.label}</span>
      </div>
      {data.status === 'interrupt' && <div className="nc-flag">暂停于此</div>}
      {data.status === 'error' && <div className="nc-flag err">异常</div>}
      {data.status === 'active' && <div className="nc-flag act">执行中</div>}
      <Handle type="source" position={Position.Bottom} className="hd" />
    </div>
  )
}

export default memo(NodeCardInner)
