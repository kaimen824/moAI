import { useState } from 'react'
import { Card, Flex, ScrollArea, Separator, Text, Badge, Tooltip } from '@radix-ui/themes'
import {
  CheckCircleIcon, BookOpenTextIcon, DownloadSimpleIcon, NotebookIcon, PathIcon,
} from '@phosphor-icons/react'
import { api } from '../api.js'
import { useApp } from '../App.jsx'

/* 左栏:章节目录(定稿✓)+ 设定集/阅读/导出入口 + 伏笔概览 */
export default function ChapterSidebar({ detail, storyId, onOpenCodex, onOpenReader }) {
  const { t } = useApp()
  const [exporting, setExporting] = useState(false)
  const chapters = detail?.chapters || []
  const threads = detail?.plot_threads || []
  const openThreads = threads.filter(x => x.status === 'open').length

  const onExport = async () => {
    if (exporting) return
    setExporting(true)
    try { await api.exportTxt(storyId, detail?.story?.title) }
    catch (e) { console.warn('[export] 导出失败', e) }
    finally { setExporting(false) }
  }

  return (
    <Card size="2" className="panel-anim" style={{
      width: 216, flexShrink: 0, height: 'fit-content',
      maxHeight: 'calc(100dvh - 24px)', overflow: 'hidden',
      display: 'flex', flexDirection: 'column',
    }}>
      <Flex direction="column" gap="3" style={{ minHeight: 0 }}>
        <Flex direction="column" gap="1">
          <Text size="1" color="gray" weight="medium">目录</Text>
          <ScrollArea scrollbars="vertical" style={{ maxHeight: '34vh' }}>
            <Flex direction="column" gap="1">
              {chapters.map(c => (
                <Flex key={c.id} gap="2" align="center" px="2" py="1" className="proc-row"
                  style={{ borderRadius: 6, cursor: 'pointer' }}
                  onClick={onOpenReader}>
                  <CheckCircleIcon size={13} color="var(--grass-9)" weight="fill" />
                  <Text size="2">第 {c.chapter_no} 章</Text>
                  <Text size="1" color="gray" style={{ marginLeft: 'auto' }}>
                    {((c.clen || 0) / 1000).toFixed(1)}k
                  </Text>
                </Flex>
              ))}
              {!chapters.length && <Text size="1" color="gray">尚未定稿章节</Text>}
            </Flex>
          </ScrollArea>
        </Flex>

        <Separator size="4" />

        <Flex direction="column" gap="2">
          <Tooltip content="角色小传 · 伏笔台账 · 世界记忆" side="right">
            <Flex gap="2" align="center" px="2" py="2" className="proc-row"
              style={{ borderRadius: 8, cursor: 'pointer' }} onClick={onOpenCodex}>
              <NotebookIcon size={15} color="var(--accent-11)" />
              <Text size="2" weight="medium">设定集</Text>
              {!!(detail?.characters?.length) && (
                <Badge color="indigo" variant="soft">{detail.characters.length}</Badge>
              )}
            </Flex>
          </Tooltip>
          <Tooltip content="切换到阅读模式" side="right">
            <Flex gap="2" align="center" px="2" py="2" className="proc-row"
              style={{ borderRadius: 8, cursor: 'pointer' }} onClick={onOpenReader}>
              <BookOpenTextIcon size={15} />
              <Text size="2" weight="medium">阅读模式</Text>
            </Flex>
          </Tooltip>
          <Tooltip content={chapters.length ? '全本章节合并为一个 txt' : '尚无定稿章节'} side="right">
            <Flex gap="2" align="center" px="2" py="2" className="proc-row"
              style={{
                borderRadius: 8,
                cursor: chapters.length ? 'pointer' : 'not-allowed',
                opacity: chapters.length ? 1 : 0.5,
              }}
              onClick={chapters.length ? onExport : undefined}>
              <DownloadSimpleIcon size={15} color={exporting ? 'var(--accent-9)' : undefined} />
              <Text size="2" weight="medium">导出 TXT</Text>
            </Flex>
          </Tooltip>
        </Flex>

        <Separator size="4" />

        <Flex direction="column" gap="1" style={{ minHeight: 0 }}>
          <Flex gap="2" align="center">
            <PathIcon size={13} color="var(--violet-9)" />
            <Text size="1" color="gray" weight="medium">伏笔</Text>
            <Badge size="1" color={openThreads ? 'violet' : 'gray'} variant="soft">
              {openThreads} 待收
            </Badge>
          </Flex>
          <ScrollArea scrollbars="vertical" style={{ maxHeight: '22vh' }}>
            <Flex direction="column" gap="1">
              {threads.map(x => (
                <Text key={x.id} size="1" color={x.status === 'open' ? 'var(--gray-12)' : 'var(--gray-9)'}
                  style={{ lineHeight: 1.6 }}>
                  {x.status !== 'open' && '✓ '}
                  {x.description?.slice(0, 40)}
                </Text>
              ))}
              {!threads.length && <Text size="1" color="gray">暂无伏笔登记</Text>}
            </Flex>
          </ScrollArea>
        </Flex>
      </Flex>
    </Card>
  )
}
