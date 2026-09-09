import { useEffect, useState } from 'react'
import {
  Badge, Button, Card, Flex, Heading, Table, Text,
} from '@radix-ui/themes'
import {
  CheckIcon, GitMergeIcon, MagnifyingGlassIcon, ProhibitIcon, XCircleIcon,
} from '@phosphor-icons/react'
import { api } from '../api.js'
import { useApp } from '../App.jsx'

/* 设定核对:低置信记忆 + 实体合并提案的人工抽检队列 */
export default function FactQueue() {
  const { t } = useApp()
  const [facts, setFacts] = useState([])
  const [proposals, setProposals] = useState([])
  const refresh = () => {
    api.pendingFacts().then(setFacts).catch(() => {})
    api.entityProposals().then(setProposals).catch(() => {})
  }
  useEffect(() => { refresh() }, [])
  const review = async (fid, ok) => { await api.reviewFact(fid, ok); refresh() }
  const reviewEntity = async (pid, action) => {
    await api.reviewEntityProposal(pid, action); refresh()
  }

  return (
    <Card size="3" className="anim-in" style={{ maxWidth: 900, width: '100%', margin: '0 auto' }}>
      <Flex direction="column" gap="3">
        <Flex align="center" gap="2">
          <MagnifyingGlassIcon size={16} />
          <Heading size="4" className="reader-serif">设定核对</Heading>
          <Badge color={facts.length ? 'amber' : 'gray'} variant="soft">{facts.length}</Badge>
          <Text size="1" color="gray" ml="2">AI 不太确定的记忆,由你一锤定音</Text>
        </Flex>
        <Table.Root variant="surface">
          <Table.Header>
            <Table.Row>
              <Table.ColumnHeaderCell>来源</Table.ColumnHeaderCell>
              <Table.ColumnHeaderCell>记忆内容</Table.ColumnHeaderCell>
              <Table.ColumnHeaderCell>章</Table.ColumnHeaderCell>
              <Table.ColumnHeaderCell>操作</Table.ColumnHeaderCell>
            </Table.Row>
          </Table.Header>
          <Table.Body>
            {facts.map(f => (
              <Table.Row key={f.id} className="proc-enter">
                <Table.RowHeaderCell>{f.story_title}</Table.RowHeaderCell>
                <Table.Cell><Text size="2">{f.content}</Text></Table.Cell>
                <Table.Cell>{f.chapter_established}</Table.Cell>
                <Table.Cell>
                  <Flex gap="2">
                    <Button size="1" color="grass" variant="soft" onClick={() => review(f.id, true)}>
                      <CheckIcon size={12} /> 属实
                    </Button>
                    <Button size="1" color="red" variant="soft" onClick={() => review(f.id, false)}>
                      <XCircleIcon size={12} /> 删除
                    </Button>
                  </Flex>
                </Table.Cell>
              </Table.Row>
            ))}
          </Table.Body>
        </Table.Root>
        {!facts.length && <Text size="2" color="gray">队列空空如也——AI 对目前的记忆都很有把握</Text>}

        <Flex align="center" gap="2" mt="4">
          <GitMergeIcon size={16} />
          <Heading size="4" className="reader-serif">同一实体?</Heading>
          <Badge color={proposals.length ? 'amber' : 'gray'} variant="soft">{proposals.length}</Badge>
          <Text size="1" color="gray" ml="2">AI 分不清两个名字是不是同一个,合并后记忆会归一到一条</Text>
        </Flex>
        <Table.Root variant="surface">
          <Table.Header>
            <Table.Row>
              <Table.ColumnHeaderCell>来源</Table.ColumnHeaderCell>
              <Table.ColumnHeaderCell>候选 → 疑似同一</Table.ColumnHeaderCell>
              <Table.ColumnHeaderCell>章</Table.ColumnHeaderCell>
              <Table.ColumnHeaderCell>操作</Table.ColumnHeaderCell>
            </Table.Row>
          </Table.Header>
          <Table.Body>
            {proposals.map(p => (
              <Table.Row key={p.id} className="proc-enter">
                <Table.RowHeaderCell>{p.story_title}</Table.RowHeaderCell>
                <Table.Cell>
                  <Flex direction="column" gap="1">
                    <Text size="2">
                      <Text weight="bold">{p.candidate_name}</Text>
                      <span style={{ color: 'var(--gray-9)' }}> → </span>
                      <Text weight="bold" className="reader-serif">{p.target_label || p.candidate_label}</Text>
                      {p.similarity != null
                        && <Badge size="1" color="gray" variant="soft" style={{ marginLeft: 6 }}>
                          相似 {(p.similarity * 100).toFixed(0)}%
                        </Badge>}
                    </Text>
                    {p.evidence && <Text size="1" color="gray">{p.evidence}</Text>}
                  </Flex>
                </Table.Cell>
                <Table.Cell>{p.chapter_no}</Table.Cell>
                <Table.Cell>
                  <Flex gap="2" wrap="wrap">
                    <Button size="1" color="grass" variant="soft"
                      onClick={() => reviewEntity(p.id, 'merge')}>
                      <GitMergeIcon size={12} /> 同一个
                    </Button>
                    <Button size="1" color="amber" variant="soft"
                      onClick={() => reviewEntity(p.id, 'new')}>
                      <CheckIcon size={12} /> 两个都留
                    </Button>
                    <Button size="1" color="gray" variant="soft"
                      onClick={() => reviewEntity(p.id, 'ignore')}>
                      <ProhibitIcon size={12} /> 不再问
                    </Button>
                  </Flex>
                </Table.Cell>
              </Table.Row>
            ))}
          </Table.Body>
        </Table.Root>
        {!proposals.length
          && <Text size="2" color="gray">没有待裁决的实体——图谱干净</Text>}
      </Flex>
    </Card>
  )
}
