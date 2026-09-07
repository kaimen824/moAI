import { useEffect, useState } from 'react'
import { Badge, Card, Flex, Heading, ScrollArea, Separator, Text } from '@radix-ui/themes'
import { PathIcon } from '@phosphor-icons/react'
import { api } from '../api.js'
import { useApp } from '../App.jsx'

/* 阅读模式:衬线正文 + 章节目录 + 伏笔侧览 */
export default function Reader({ storyId }) {
  const { t } = useApp()
  const [detail, setDetail] = useState(null)
  const [current, setCurrent] = useState(null)

  const refresh = () => api.storyDetail(storyId).then(setDetail).catch(() => {})
  useEffect(() => { refresh(); setCurrent(null) }, [storyId])   // eslint-disable-line

  const open = async (no) => {
    const ch = await api.chapter(storyId, no)
    setCurrent(ch); refresh()
  }

  return (
    <Flex gap="4" align="stretch" style={{ width: '100%' }}>
      <Card size="2" className="anim-in" style={{ width: 240, flexShrink: 0, height: 'fit-content' }}>
        <Flex direction="column" gap="3">
          <Heading size="3" className="reader-serif">{detail?.story?.title || '…'}</Heading>
          <Flex direction="column" gap="1">
            <Text size="1" color="gray" weight="medium">章节</Text>
            <ScrollArea scrollbars="vertical" style={{ maxHeight: '40vh' }}>
              <Flex direction="column" gap="1">
                {detail?.chapters?.map(c => (
                  <Text key={c.id} size="2" as="div" className="proc-row" style={{
                    cursor: 'pointer', padding: '4px 8px', borderRadius: 6,
                    background: current?.chapter_no === c.chapter_no ? 'var(--accent-a3)' : 'transparent',
                  }} onClick={() => open(c.chapter_no)}>
                    第 {c.chapter_no} 章
                    <Text as="span" size="1" color="gray" style={{ marginLeft: 8 }}>
                      {((c.clen || 0) / 1000).toFixed(1)}k 字
                    </Text>
                  </Text>
                ))}
                {!detail?.chapters?.length && <Text size="2" color="gray">还没有定稿章节</Text>}
              </Flex>
            </ScrollArea>
          </Flex>
          <Separator size="4" />
          <Flex direction="column" gap="1">
            <Flex gap="2" align="center">
              <PathIcon size={13} color="var(--violet-9)" />
              <Text size="1" color="gray" weight="medium">伏笔</Text>
            </Flex>
            {detail?.plot_threads?.map(th => (
              <Flex key={th.id} gap="2" align="baseline">
                <Badge color={th.status === 'open' ? 'violet' : 'grass'}>{th.status === 'open' ? '待收' : '已收'}</Badge>
                <Text size="1">{th.description}</Text>
              </Flex>
            ))}
          </Flex>
        </Flex>
      </Card>
      <Card size="3" className="anim-in" style={{ flex: 1, minWidth: 320 }}>
        {current
          ? <ScrollArea scrollbars="vertical" style={{ maxHeight: 'calc(100dvh - 48px)' }}>
              <Text as="div" size="4" className="reader-serif" style={{
                whiteSpace: 'pre-wrap', lineHeight: 2.2, maxWidth: 720, margin: '0 auto',
                padding: '12px 4px',
              }}>{current.content}</Text>
            </ScrollArea>
          : <Flex align="center" justify="center" style={{ minHeight: 320 }}>
              <Text color="gray">从左侧选择一章,泡杯茶慢慢读</Text>
            </Flex>}
      </Card>
    </Flex>
  )
}
