import { useEffect, useState } from 'react'
import {
  Card, Code, DataList, Flex, Heading, Text, TextField,
} from '@radix-ui/themes'
import { GearIcon } from '@phosphor-icons/react'
import { api } from '../api.js'

/* 模型分级路由 + 用量 */
export default function ConfigPage({ storyId }) {
  const [models, setModels] = useState({})
  const [usage, setUsage] = useState([])
  useEffect(() => { api.models().then(setModels).catch(() => {}) }, [])
  useEffect(() => { if (storyId) api.usage(storyId).then(setUsage).catch(() => {}) }, [storyId])
  const save = async (role, model) => { await api.setModel(role, model); setModels(m => ({ ...m, [role]: model })) }

  return (
    <Flex direction="column" gap="4" style={{ maxWidth: 820, width: '100%', margin: '0 auto' }}>
      <Card size="3" className="anim-in">
        <Flex direction="column" gap="3">
          <Flex align="center" gap="2">
            <GearIcon size={16} />
            <Heading size="4" className="reader-serif">模型分工</Heading>
          </Flex>
          <Text size="1" color="gray">优先级:此处覆盖 &gt; 环境变量 &gt; 默认(规划与写作用强模型,记忆与摘要用轻模型)</Text>
          <DataList.Root size="2">
            {Object.entries(models).map(([role, model]) => (
              <DataList.Item key={role} align="center">
                <DataList.Label minWidth="110px"><Code size="1">{role}</Code></DataList.Label>
                <DataList.Value>
                  <TextField.Root size="1" defaultValue={model} style={{ width: 230 }}
                    onBlur={e => e.target.value !== model && save(role, e.target.value)} />
                </DataList.Value>
              </DataList.Item>
            ))}
          </DataList.Root>
        </Flex>
      </Card>
      {storyId && (
        <Card size="3" className="anim-in">
          <Flex direction="column" gap="3">
            <Heading size="4" className="reader-serif">用量(按分工)</Heading>
            <Flex gap="3" wrap="wrap">
              {usage.map((u, i) => (
                <Card key={i} size="2" variant="surface" className="cover-card" style={{ width: 190 }}>
                  <Flex direction="column" gap="1">
                    <Text size="2" weight="bold">{u.agent}</Text>
                    <Code size="1">{u.model}</Code>
                    <Text size="1" color="gray">调用 {u.calls} 次 · {(u.tin || 0) + (u.tout || 0)} tokens</Text>
                    <Text size="1" color="gray">{((u.latency || 0) / 1000).toFixed(1)}s 累计</Text>
                  </Flex>
                </Card>
              ))}
              {!usage.length && <Text size="2" color="gray">暂无调用</Text>}
            </Flex>
          </Flex>
        </Card>
      )}
    </Flex>
  )
}
