import { useEffect, useState } from 'react'
import { Badge, Button, Card, Flex, Heading, Text, TextField } from '@radix-ui/themes'
import { BookOpenIcon, PencilSimpleIcon } from '@phosphor-icons/react'
import { api } from '../api.js'

/* 书籍 id -> 稳定渐变封面(竖排书名,东方书脊感) */
function coverStyle(id) {
  let h = 0
  for (const ch of String(id)) h = (h * 31 + ch.codePointAt(0)) % 360
  const h2 = (h + 40) % 360
  return {
    background: `linear-gradient(155deg, hsl(${h} 42% 26%), hsl(${h2} 38% 16%))`,
  }
}

function BookCover({ story, onOpen }) {
  return (
    <Card size="2" variant="classic" className="cover-card anim-in"
      style={{ cursor: 'pointer', padding: 0, overflow: 'hidden', width: '100%' }}
      onClick={onOpen}>
      <Flex gap="3" p="3" align="stretch">
        <Flex align="center" justify="center" style={{
          width: 92, minHeight: 128, borderRadius: '6px 2px 2px 6px', flexShrink: 0,
          boxShadow: 'inset -6px 0 12px rgba(0,0,0,0.35), 0 6px 18px rgba(0,0,0,0.25)',
          borderRight: '3px solid rgba(255,255,255,0.12)',
          ...coverStyle(story.id),
        }}>
          <Text className="cover-title" size="5" weight="bold"
            style={{ color: 'rgba(255,255,255,0.92)' }}>
            {(story.title || '未名').slice(0, 6)}
          </Text>
        </Flex>
        <Flex direction="column" gap="2" py="1" style={{ minWidth: 0 }}>
          <Text size="4" weight="bold" className="reader-serif" truncate>{story.title}</Text>
          <Flex gap="2" align="center">
            <Badge color={story.chapter_count ? 'grass' : 'gray'} variant="soft">
              {story.chapter_count ? `${story.chapter_count} 章` : '未开卷'}
            </Badge>
            <Text size="1" color="gray">
              {new Date(story.created_at).toLocaleDateString('zh-CN')}
            </Text>
          </Flex>
          <Text size="1" color="gray" style={{ lineHeight: 1.7, marginTop: 'auto' }}>
            {story.premise?.slice(0, 60) || (story.chapter_count ? '点开继续写作' : '点开开始创作')}
          </Text>
        </Flex>
      </Flex>
    </Card>
  )
}

export default function Library({ onOpen }) {
  const [stories, setStories] = useState([])
  const [title, setTitle] = useState('')
  const [premise, setPremise] = useState('')

  const refresh = () => api.listStories().then(setStories).catch(() => {})
  useEffect(() => { refresh() }, [])

  const create = async () => {
    if (!title.trim()) return
    const s = await api.createStory(title, premise)
    setTitle(''); setPremise(''); refresh(); onOpen(s.story_id)
  }

  return (
    <Flex direction="column" gap="4" style={{ maxWidth: 860, width: '100%', margin: '0 auto' }}>
      <Card size="3" className="anim-in">
        <Flex direction="column" gap="3">
          <Flex align="center" gap="2">
            <BookOpenIcon size={16} />
            <Heading size="4" className="reader-serif">开一部新书</Heading>
          </Flex>
          <Flex gap="3" wrap="wrap">
            <TextField.Root size="2" placeholder="书名" value={title}
              style={{ width: 200 }} onChange={e => setTitle(e.target.value)} />
            <TextField.Root size="2" placeholder="一句话简介(可选)" value={premise}
              style={{ flex: 1, minWidth: 220 }} onChange={e => setPremise(e.target.value)} />
            <Button size="2" onClick={create} disabled={!title.trim()}>
              <PencilSimpleIcon size={14} weight="bold" /> 创建
            </Button>
          </Flex>
        </Flex>
      </Card>

      <Flex align="center" gap="2">
        <Heading size="4" className="reader-serif">书架</Heading>
        <Badge color="gray" variant="soft">{stories.length}</Badge>
      </Flex>
      <Flex gap="3" wrap="wrap">
        {stories.map(s => (
          <div key={s.id} style={{ flex: '1 1 340px', maxWidth: 420 }}>
            <BookCover story={s} onOpen={() => onOpen(s.id)} />
          </div>
        ))}
      </Flex>
      {!stories.length && <Text size="2" color="gray">书架空空,从上一部作品开始</Text>}
    </Flex>
  )
}
