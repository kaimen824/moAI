import { useEffect, useRef, useState } from 'react'
import { Badge, Button, Card, Flex, Spinner, Text, TextArea } from '@radix-ui/themes'
import { PaperPlaneTiltIcon } from '@phosphor-icons/react'
import { api } from '../api'

// 工具名 -> 展示标签(P0 工具表,与 chat_service.ChatToolbox.registry 对应)
const TOOL_LABEL = {
  query_book_detail: '查询书籍概览',
  query_chapter: '查询章节',
  query_codex: '查询设定集',
  query_usage: '查询用量',
  query_run_status: '查询生成状态',
  record_directive: '记录指令',
  stop_run: '请求停止生成',
}

function ToolCard({ name, observation }) {
  const label = TOOL_LABEL[name] || name
  return (
    <Flex align="center" gap="2" px="2" py="1"
      style={{ background: 'var(--gray-a2)', borderRadius: 6, alignSelf: 'flex-start', maxWidth: '90%' }}>
      <Badge color="indigo" variant="soft" size="1">{label}</Badge>
      {observation == null
        ? <Spinner size="1" />
        : <Text size="1" color="gray" style={{
            maxWidth: 420, overflow: 'hidden', textOverflow: 'ellipsis',
            whiteSpace: 'nowrap', display: 'inline-block' }}>{observation}</Text>}
    </Flex>
  )
}

export default function ChatDock({ storyId }) {
  const [messages, setMessages] = useState([])   // {role, content, name, observation, pending}
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const bottomRef = useRef(null)

  useEffect(() => {
    // 会话还原:历史里 role=tool 渲染为工具卡,assistant(含思考文本)为消息
    api.chatHistory(storyId).then(r => {
      setMessages(r.messages.map(m => m.role === 'tool'
        ? { role: 'tool', name: m.meta?.name, observation: m.content }
        : { role: m.role, content: m.content }))
    }).catch(() => {})
  }, [storyId])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: 'end' })
  }, [messages])

  const send = () => {
    const text = input.trim()
    if (!text || busy) return
    setInput('')
    setBusy(true)
    setMessages(ms => [...ms, { role: 'user', content: text }])
    const onEvent = (kind, data) => setMessages(ms => {
      const next = [...ms]
      if (kind === 'step') {
        if (data.content) next.push({ role: 'assistant', content: data.content })
      } else if (kind === 'tool_call') {
        next.push({ role: 'tool', id: data.id, name: data.name, observation: null })
      } else if (kind === 'tool_result') {
        const i = next.findLastIndex(m => m.role === 'tool' && m.id === data.id)
        if (i >= 0) next[i] = { ...next[i], observation: data.observation }
      } else if (kind === 'reply') {
        const last = next[next.length - 1]
        if (last?.role !== 'assistant' || last.content !== data.content) {
          next.push({ role: 'assistant', content: data.content })
        }
      } else if (kind === 'error') {
        next.push({ role: 'assistant', content: `⚠ ${data.message}` })
      }
      return next
    })
    api.chatSend(storyId, text, onEvent).catch(e => {
      setMessages(ms => [...ms, { role: 'assistant', content: `⚠ 连接失败:${e.message}` }])
    }).finally(() => setBusy(false))
  }

  return (
    <Card size="1">
      <Flex direction="column" gap="2">
        <Flex align="center" gap="2">
          <Text size="1" weight="medium" color="gray">创作对话</Text>
          <Text size="1" color="gray">查询书籍 / 记录指令 / 请求暂停(确认操作仍由你在界面上完成)</Text>
        </Flex>
        <Flex direction="column" gap="2" style={{
          maxHeight: 380, overflowY: 'auto', padding: '4px 2px' }}>
          {messages.length === 0 && (
            <Text size="1" color="gray">问进度、查设定、下指令——比如「现在写到哪了」「下一章把节奏放慢」</Text>
          )}
          {messages.map((m, i) => m.role === 'user' ? (
            <Flex key={i} justify="end">
              <Text size="2" px="3" py="2" style={{
                background: 'var(--accent-a3)', borderRadius: 10,
                maxWidth: '80%', whiteSpace: 'pre-wrap' }}>{m.content}</Text>
            </Flex>
          ) : m.role === 'tool' ? (
            <ToolCard key={i} name={m.name} observation={m.observation} />
          ) : (
            <Text key={i} size="2" style={{
              whiteSpace: 'pre-wrap', alignSelf: 'flex-start',
              maxWidth: '90%', color: 'var(--gray-12)' }}>{m.content}</Text>
          ))}
          <div ref={bottomRef} />
        </Flex>
        <Flex gap="2" align="end">
          <TextArea size="1" rows={2} variant="soft" style={{ flex: 1, boxShadow: 'none' }}
            placeholder="下达命令…(Enter 发送,Shift+Enter 换行)"
            value={input} disabled={busy}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() }
            }} />
          <Button size="2" onClick={send} disabled={busy || !input.trim()}>
            {busy ? <Spinner size="1" /> : <PaperPlaneTiltIcon size={14} />}
          </Button>
        </Flex>
      </Flex>
    </Card>
  )
}
