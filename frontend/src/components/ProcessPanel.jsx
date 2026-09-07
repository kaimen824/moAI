import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Badge, Card, Flex, ScrollArea, Spinner, Text, Tooltip,
} from '@radix-ui/themes'
import {
  CaretDownIcon, CaretRightIcon, ActivityIcon,
} from '@phosphor-icons/react'
import { useApp } from '../App.jsx'
import TraceDrawer from './TraceDrawer.jsx'

/* verdict 色点(紧凑行右侧的状态标识) */
function VerdictDot({ verdict }) {
  const color = verdict === 'pass' ? 'var(--grass-9)'
    : verdict === 'block' ? 'var(--red-9)' : 'var(--amber-9)'
  return <span style={{ width: 7, height: 7, borderRadius: 999, background: color, flexShrink: 0 }} />
}

/* LLM 紧凑行:一行列出;点击全宽抽屉看输入输出(透明到模型层) */
function LlmRow({ entry, t, onOpen }) {
  const c = entry.llm
  const verdict = c.stage?.startsWith('review') ? entry.lastVerdict : undefined
  return (
    <Flex className="proc-row proc-enter" gap="2" align="center" px="2" py="1"
      style={{ borderRadius: 6, cursor: 'pointer' }} onClick={() => onOpen(entry)}>
      <Text size="1" weight="bold" color="accent" style={{ flexShrink: 0 }}>⚙</Text>
      <Text size="1" weight="medium" style={{ flexShrink: 0 }}>{t.llm(c.stage)}</Text>
      <Text size="1" color="gray" truncate style={{ minWidth: 0 }}>
        {c.model} · {((c.latency_ms || 0) / 1000).toFixed(0)}s · ↑{(c.tokens_in || 0)} ↓{(c.tokens_out || 0)}
      </Text>
      <VerdictDot verdict={verdict} />
      <CaretRightIcon size={11} color="var(--gray-9)" style={{ flexShrink: 0 }} />
    </Flex>
  )
}

/* stage 紧凑行:节点名+时间;点击抽屉看产出摘要 */
function StageRow({ entry, t, onOpen }) {
  const p = entry.payload || {}
  const verdict = p.outline_review?.verdict || p.quality_review?.verdict
    || p.outline_verdict?.verdict || p.merged_verdict
  return (
    <Flex className="proc-row proc-enter" gap="2" align="center" px="2" py="1"
      style={{ borderRadius: 6, cursor: 'pointer' }} onClick={() => onOpen(entry)}>
      <Text size="1" color="gray" style={{ flexShrink: 0 }}>·</Text>
      <Text size="1" truncate style={{ minWidth: 0 }}>{t.stage(entry.node)}</Text>
      {verdict && <Badge size="1" variant="soft"
        color={verdict === 'pass' ? 'grass' : verdict === 'needs_user' ? 'red' : 'amber'}>
        {t.verdict(verdict)}
      </Badge>}
      <Text size="1" color="gray" style={{ marginLeft: 'auto', flexShrink: 0, fontSize: 10 }}>
        {new Date(entry.t).toLocaleTimeString('zh-CN', { hour12: false })}
      </Text>
    </Flex>
  )
}

/* ================= 右栏:AI 工作过程(透明原则:每步实时可见) ================= */
export default function ProcessPanel({ entries, running, onCollapse }) {
  const { t } = useApp()
  const [detail, setDetail] = useState(null)
  const [filter, setFilter] = useState('all')
  const [collapsed, setCollapsed] = useState([])     // 已折叠的章组 key
  const scrollerRef = useRef(null)
  const stickRef = useRef(true)

  /* 按章分组;章内保留时序 */
  const groups = useMemo(() => {
    const gs = []
    let cur = null
    let lastVerdict
    for (const raw of entries) {
      const gkey = raw.ch != null ? `第${raw.ch}章` : '开卷'
      if (!cur || cur.key !== gkey) {
        cur = { key: gkey, ch: raw.ch, items: [] }
        gs.push(cur)
      }
      let item = { ...raw }
      if (item.kind === 'stage') {
        const v = item.payload?.outline_review?.verdict || item.payload?.quality_review?.verdict
          || item.payload?.outline_verdict?.verdict || item.payload?.merged_verdict
        if (v) lastVerdict = v
        item.lastVerdict = lastVerdict
      } else {
        item.lastVerdict = lastVerdict
      }
      cur.items.push(item)
    }
    return gs
  }, [entries])

  /* 当前组(最后一组)默认展开;贴底自动滚动 */
  const lastKey = groups.length ? groups[groups.length - 1].key : ''
  const isCollapsed = (key) => key !== lastKey && collapsed.includes(key)

  useEffect(() => {
    const el = scrollerRef.current
    if (el && stickRef.current) el.scrollTop = el.scrollHeight
  }, [entries])
  const onScroll = (e) => {
    const el = e.currentTarget
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40
  }

  const filtered = (items) => filter === 'all' ? items
    : filter === 'review' ? items.filter(e => e.kind === 'llm'
      ? e.llm.stage?.includes('review') : /review|merge/.test(e.node || ''))
    : items.filter(e => e.kind === 'llm' ? e.llm.stage === 'draft'
      : /write_draft|draft_start/.test(e.node || ''))

  return (
    <Card size="2" className="anim-in" style={{ height: '100%', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
      <Flex direction="column" gap="2" style={{ flex: 1, minHeight: 0 }}>
        <Flex align="center" gap="2" style={{ flexShrink: 0 }}>
          <ActivityIcon size={14} color="var(--accent-11)" weight="bold" />
          <Text size="2" weight="bold">{t.processPanel}</Text>
          {running && (
            <Tooltip content="AI 正在工作中">
              <span className="pulse-dot" style={{
                width: 8, height: 8, borderRadius: 999, background: 'var(--grass-9)',
                marginLeft: 4,
              }} />
            </Tooltip>
          )}
          <Tooltip content="收起面板" side="left">
            <Text size="1" color="gray" style={{ marginLeft: 'auto', cursor: 'pointer' }}
              onClick={onCollapse}>«</Text>
          </Tooltip>
        </Flex>

        <Flex gap="1" style={{ flexShrink: 0 }}>
          {[['all', '全部'], ['review', '审读'], ['draft', '正文']].map(([k, label]) => (
            <Text key={k} as="span" size="1" style={{
              cursor: 'pointer', padding: '2px 10px', borderRadius: 999,
              background: filter === k ? 'var(--accent-a4)' : 'transparent',
              color: filter === k ? 'var(--accent-11)' : 'var(--gray-11)',
              border: `1px solid ${filter === k ? 'var(--accent-a6)' : 'var(--gray-a5)'}`,
            }} onClick={() => setFilter(k)}>{label}</Text>
          ))}
        </Flex>

        <ScrollArea scrollbars="vertical" style={{ flex: 1, minHeight: 0 }} type="hover">
          <Flex direction="column" gap="1" ref={scrollerRef} onScroll={onScroll}
            style={{ paddingRight: 2 }}>
            {groups.map(g => {
              const open = !isCollapsed(g.key)
              const items = filtered(g.items)
              return (
                <Flex key={g.key} direction="column" gap="1" className="proc-enter">
                  <Flex gap="1" align="center" px="1" py="1" style={{
                    cursor: 'pointer', borderRadius: 6, background: 'var(--gray-a3)',
                  }} onClick={() => setCollapsed(cs =>
                    g.key === lastKey ? cs : (cs.includes(g.key) ? cs.filter(x => x !== g.key) : [...cs, g.key]))}>
                    {open ? <CaretDownIcon size={11} /> : <CaretRightIcon size={11} />}
                    <Text size="1" weight="bold">{g.key}</Text>
                    <Text size="1" color="gray">{g.items.length} 步</Text>
                    {g.key === lastKey && running && <Spinner size="1" />}
                  </Flex>
                  {open && items.map((e, i) => e.kind === 'llm'
                    ? <LlmRow key={i} entry={e} t={t} onOpen={setDetail} />
                    : <StageRow key={i} entry={e} t={t} onOpen={setDetail} />)}
                  {open && !items.length && (
                    <Text size="1" color="gray" px="2">(此过滤下无条目)</Text>
                  )}
                </Flex>
              )
            })}
            {!entries.length && (
              <Text size="1" color="gray" py="4">
                开始写作后,AI 的每一步都会实时显示在这里——
                规划、检索记忆、写作、审读,点开任意一步可查看模型的完整输入与输出。
              </Text>
            )}
            {running && (
              <Flex align="center" gap="2" px="2" py="2">
                <Spinner size="1" />
                <Text size="1" color="gray">模型调用中(长文一步可达 1-3 分钟,计时在走即正常)</Text>
              </Flex>
            )}
          </Flex>
        </ScrollArea>
      </Flex>
      <TraceDrawer detail={detail} onClose={() => setDetail(null)} />
    </Card>
  )
}
