import { useEffect, useState } from 'react'
import {
  Badge, Card, Flex, Heading, ScrollArea, Separator, Tabs, Text,
} from '@radix-ui/themes'
import { GraphIcon, NotebookIcon, PathIcon, PersonIcon } from '@phosphor-icons/react'
import { api } from '../api.js'
import { useApp } from '../App.jsx'

const TYPE_ZH = {
  character: '角色', faction: '势力', location: '地点',
  item: '物品', technique: '功法', concept: '概念',
}

/* 设定集(Codex):角色小传 / 实体图谱 / 伏笔台账 / 世界记忆(当前有效,排除被推翻) */
export default function CodexPage({ storyId }) {
  const { t } = useApp()
  const [data, setData] = useState(null)
  const [tab, setTab] = useState('characters')

  useEffect(() => { api.codex(storyId).then(setData).catch(() => {}) }, [storyId])

  if (!data) return <Text color="gray">设定集加载中…</Text>

  const factsByType = {}
  for (const f of data.facts || []) {
    (factsByType[f.type] = factsByType[f.type] || []).push(f)
  }

  return (
    <Card size="3" className="anim-in" style={{ width: '100%', maxWidth: 960, margin: '0 auto' }}>
      <Flex direction="column" gap="3">
        <Flex align="center" gap="2">
          <NotebookIcon size={16} color="var(--accent-11)" />
          <Heading size="4" className="reader-serif">设定集</Heading>
          <Text size="1" color="gray">AI 记住的每一件事——写作时它们都会被想起</Text>
        </Flex>

        <Tabs.Root value={tab} onValueChange={setTab}>
          <Tabs.List>
            <Tabs.Trigger value="characters">角色({data.characters?.length || 0})</Tabs.Trigger>
            <Tabs.Trigger value="entities">实体图谱({data.entities?.length || 0})</Tabs.Trigger>
            <Tabs.Trigger value="threads">伏笔({data.plot_threads?.length || 0})</Tabs.Trigger>
            <Tabs.Trigger value="memory">世界记忆({data.facts?.length || 0})</Tabs.Trigger>
            <Tabs.Trigger value="outline">总大纲</Tabs.Trigger>
          </Tabs.List>
        </Tabs.Root>

        <ScrollArea scrollbars="vertical" style={{ maxHeight: 'calc(100dvh - 220px)' }}>
          {tab === 'characters' && (
            <Flex direction="column" gap="2">
              {data.characters?.map(c => (
                <Card key={c.id} size="2" variant="surface" className="proc-enter">
                  <Flex gap="3" align="baseline">
                    <PersonIcon size={15} color="var(--accent-11)" />
                    <Text size="3" weight="bold" className="reader-serif">{c.name}</Text>
                  </Flex>
                  <Text as="div" size="2" color="gray" mt="2"
                    style={{ whiteSpace: 'pre-wrap', lineHeight: 1.9 }}>{c.profile}</Text>
                </Card>
              ))}
              {!data.characters?.length && <Text size="2" color="gray">暂无角色</Text>}
            </Flex>
          )}

          {tab === 'entities' && (
            <Flex direction="column" gap="2">
              {data.entities?.map(e => {
                const rels = (data.entity_links || []).filter(
                  l => l.from_name === e.name || l.to_name === e.name)
                return (
                  <Card key={e.id} size="2" variant="surface" className="proc-enter">
                    <Flex gap="2" align="center" wrap="wrap">
                      <GraphIcon size={14} color="var(--accent-11)" />
                      <Text size="3" weight="bold" className="reader-serif">{e.name}</Text>
                      <Badge color="gray" variant="soft">{TYPE_ZH[e.type] || e.type}</Badge>
                      {e.chapter_no
                        && <Text size="1" color="gray">第 {e.chapter_no} 章登场</Text>}
                    </Flex>
                    {e.content
                      && <Text as="div" size="2" color="gray" mt="1"
                        style={{ whiteSpace: 'pre-wrap', lineHeight: 1.8 }}>{e.content}</Text>}
                    {rels.length > 0 && (
                      <Flex direction="column" gap="1" mt="2">
                        <Text size="1" weight="bold" color="gray">关联</Text>
                        {rels.map((l, i) => (
                          <Text key={i} size="1" color="gray" px="1">
                            {l.from_name === e.name ? `→ ${l.to_name}` : `← ${l.from_name}`}
                            <Badge size="1" color="violet" variant="soft"
                              style={{ marginLeft: 6 }}>{l.relation}</Badge>
                          </Text>
                        ))}
                      </Flex>
                    )}
                  </Card>
                )
              })}
              {!data.entities?.length
                && <Text size="2" color="gray">暂无实体——从共创设定与正文中逐渐沉淀</Text>}
            </Flex>
          )}

          {tab === 'threads' && (
            <Flex direction="column" gap="2">
              {data.plot_threads?.map(x => (
                <Card key={x.id} size="2" variant="surface" className="proc-enter">
                  <Flex gap="2" align="center" wrap="wrap">
                    <PathIcon size={13} color="var(--violet-9)" />
                    <Badge color={x.status === 'open' ? 'violet' : 'grass'}
                      variant="soft">{x.status === 'open' ? '待收' : '已收'}</Badge>
                    <Text size="1" color="gray">
                      第 {x.planted_chapter} 章埋设
                      {x.resolved_chapter ? ` · 第 ${x.resolved_chapter} 章回收` : ''}
                    </Text>
                  </Flex>
                  <Text as="div" size="2" mt="1">{x.description}</Text>
                </Card>
              ))}
              {!data.plot_threads?.length && <Text size="2" color="gray">暂无伏笔</Text>}
            </Flex>
          )}

          {tab === 'memory' && (
            <Flex direction="column" gap="3">
              {Object.entries(factsByType).map(([type, items]) => (
                <Flex key={type} direction="column" gap="1">
                  <Flex gap="2" align="center">
                    <Text size="2" weight="bold">{t.factType(type)}</Text>
                    <Badge color="gray" variant="soft">{items.length}</Badge>
                  </Flex>
                  <Separator size="4" />
                  {items.map((f, i) => (
                    <Flex key={i} gap="2" align="baseline" px="1">
                      <Text size="1" color="gray" style={{ flexShrink: 0 }}>ch{f.chapter_established}</Text>
                      <Text size="2">{f.content}</Text>
                      {f.confidence === 'low' && <Badge size="1" color="amber" variant="soft">待核</Badge>}
                    </Flex>
                  ))}
                </Flex>
              ))}
              {!data.facts?.length && <Text size="2" color="gray">暂无记忆——从第一章开始累积</Text>}
            </Flex>
          )}

          {tab === 'outline' && (
            data.outline
              ? <Text as="div" size="2" className="reader-serif"
                  style={{ whiteSpace: 'pre-wrap', lineHeight: 1.95 }}>{data.outline}</Text>
              : <Text size="2" color="gray">尚未确认总大纲</Text>
          )}
        </ScrollArea>
      </Flex>
    </Card>
  )
}
