import { useState } from 'react'
import { Dialog, Flex, ScrollArea, Tabs, Text, Badge, Code } from '@radix-ui/themes'
import { useApp } from '../App.jsx'

/* LLM 输入输出 / 节点产出 的全文查看:全宽 Dialog(长文本行宽舒适)。
   透明原则:任何步骤都可下钻到模型层原始输入输出。 */

function LlmDetail({ call, t }) {
  const [tab, setTab] = useState('output')
  return (
    <Flex direction="column" gap="3">
      <Flex gap="2" align="center" wrap="wrap">
        <Badge color="indigo" variant="soft">{t.llm(call.stage)}</Badge>
        <Code size="1">{call.agent}</Code>
        <Code size="1">{call.model}</Code>
        <Text size="1" color="gray">
          {((call.latency_ms || 0) / 1000).toFixed(1)}s · ↑{call.tokens_in || 0} ↓{call.tokens_out || 0} tokens
        </Text>
      </Flex>
      <Tabs.Root value={tab} onValueChange={setTab}>
        <Tabs.List>
          <Tabs.Trigger value="output">模型输出</Tabs.Trigger>
          <Tabs.Trigger value="input">模型输入(完整上下文)</Tabs.Trigger>
        </Tabs.List>
      </Tabs.Root>
      <ScrollArea scrollbars="vertical" style={{ maxHeight: '62vh' }}>
        <Text as="div" size="1" style={{
          whiteSpace: 'pre-wrap', lineHeight: 1.75,
          background: 'var(--gray-a2)', padding: '14px 16px', borderRadius: 8,
          fontFamily: 'var(--code-font-family, monospace)',
        }}>
          {tab === 'output'
            ? (call.output || '(空)')
            : (call.input || []).map((m, i) => `【${m.role}】\n${m.content}`)
              .join('\n\n———\n\n')}
        </Text>
      </ScrollArea>
    </Flex>
  )
}

function PayloadDetail({ payload, t }) {
  const blocks = []
  const push = (label, v) => v && blocks.push([label, v])
  push('世界观设定', payload.world_settings)
  push('总大纲', payload.master_outline)
  push('阶段细纲', payload.stage_outline)
  push('本章要点', payload.chapter_brief)
  push('章摘要', payload.chapter_summary)
  push('阶段聚合摘要', payload.stage_summary)
  if (payload.character_drafts?.length)
    push('角色卡', payload.character_drafts.map(c => `${c.name}:${c.profile}`).join('\n'))
  if (payload.character_changes?.length)
    push('角色状态更新', payload.character_changes.map(u => `${u.name} + ${u.profile_append}`).join('\n'))
  for (const [k, label] of [['outline_review', '一致性审读'], ['quality_review', '质量审校']]) {
    const r = payload[k]
    if (r) push(label, `${t.verdict(r.verdict)} ${r.feedback || ''}`)
  }
  if (payload.fact_changes?.facts?.length)
    push('新记忆', payload.fact_changes.facts.map(f => `[${t.factType(f.type)}] ${f.content}`).join('\n'))
  if (payload.thread_changes?.length)
    push('伏笔变更', payload.thread_changes.map(x => `[${x.action}] ${x.description}`).join('\n'))
  if (payload.context_stats)
    push('检索上下文', `在场角色 ${payload.context_stats.present} · 角色记忆 ${payload.context_stats.pov_facts} · 认知 ${payload.context_stats.beliefs} · 活跃伏笔 ${payload.context_stats.threads}`)
  if (payload.merged_verdict)
    push('汇总裁决', t.verdict(payload.merged_verdict))

  return (
    <Flex direction="column" gap="3">
      {!blocks.length && <Text size="2" color="gray">该步骤无文本产出(状态流转节点)</Text>}
      {blocks.map(([label, v]) => (
        <Flex key={label} direction="column" gap="1">
          <Text size="1" color="gray">{label}</Text>
          <ScrollArea scrollbars="vertical" style={{ maxHeight: '28vh' }}>
            <Text as="div" size="2" className="reader-serif" style={{
              whiteSpace: 'pre-wrap', lineHeight: 1.85,
              background: 'var(--gray-a2)', padding: '12px 14px', borderRadius: 8,
            }}>{v}</Text>
          </ScrollArea>
        </Flex>
      ))}
    </Flex>
  )
}

export default function TraceDrawer({ detail, onClose }) {
  const { t } = useApp()
  if (!detail) return null
  const title = detail.kind === 'llm' ? t.llm(detail.llm.stage) : t.stage(detail.node)
  return (
    <Dialog.Root open onOpenChange={(o) => !o && onClose()}>
      <Dialog.Content maxWidth="80vw" style={{ maxWidth: '80vw' }}>
        <Flex align="center" gap="2" mb="3">
          <Dialog.Title>{title} · 详情</Dialog.Title>
          <Text size="1" color="gray" style={{ marginLeft: 'auto' }}>
            {new Date(detail.t).toLocaleTimeString('zh-CN', { hour12: false })}
          </Text>
        </Flex>
        <Dialog.Description size="1" color="gray" mb="3">
          {detail.kind === 'llm'
            ? '该次模型调用的完整输入上下文与输出——用于核对 AI 究竟"看到了什么、写了什么"'
            : '该步骤的产出摘要'}
        </Dialog.Description>
        {detail.kind === 'llm'
          ? <LlmDetail call={detail.llm} t={t} />
          : <PayloadDetail payload={detail.payload} t={t} />}
      </Dialog.Content>
    </Dialog.Root>
  )
}
