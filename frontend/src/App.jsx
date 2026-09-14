import { createContext, useContext, useEffect, useState } from 'react'
import { Theme, Tooltip, Text, Flex } from '@radix-ui/themes'
import {
  BookOpenIcon, LightningIcon, BookOpenTextIcon, NotebookIcon,
  MagnifyingGlassIcon, GearIcon, SunIcon, MoonIcon, CodeIcon, InfoIcon,
  SignOutIcon,
} from '@phosphor-icons/react'
import { makeT } from './copy.js'
import { getToken, clearToken } from './api.js'
import Landing from './Landing.jsx'
import Login from './components/Login.jsx'
import Library from './components/Library.jsx'
import Workbench from './components/Workbench.jsx'
import Reader from './components/Reader.jsx'
import CodexPage from './components/CodexPage.jsx'
import FactQueue from './components/FactQueue.jsx'
import ConfigPage from './components/ConfigPage.jsx'

/* 全局双模式上下文:暗色 / 开发者视图(术语切换,可见性不变) */
const AppCtx = createContext({})
export const useApp = () => useContext(AppCtx)

export default function App() {
  const [route, setRoute] = useState(window.location.hash || '#/')
  const [tab, setTab] = useState('library')
  const [storyId, setStoryId] = useState(null)
  const [dark, setDark] = useState(() => localStorage.getItem('molan-dark') !== '0')
  const [devMode, setDevMode] = useState(() => localStorage.getItem('molan-dev') === '1')
  // 认证门卫(ADR-0022):无 token 显示登录页;任意 401(molan-unauthorized)踢回
  const [authed, setAuthed] = useState(() => !!getToken())

  useEffect(() => {
    const onHash = () => setRoute(window.location.hash || '#/')
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])
  useEffect(() => {
    const onKick = () => setAuthed(false)
    window.addEventListener('molan-unauthorized', onKick)
    return () => window.removeEventListener('molan-unauthorized', onKick)
  }, [])
  useEffect(() => { localStorage.setItem('molan-dark', dark ? '1' : '0') }, [dark])
  useEffect(() => { localStorage.setItem('molan-dev', devMode ? '1' : '0') }, [devMode])

  const t = makeT(devMode)

  if (authed && route !== '#/app') {
    return <Landing onEnter={() => { window.location.hash = '#/app' }} />
  }
  if (!authed) {
    return (
      <Theme appearance={dark ? 'dark' : 'light'} accentColor="indigo" radius="medium">
        <Login onLogin={() => setAuthed(true)} />
      </Theme>
    )
  }

  const NAV = [
    { key: 'library', label: '书库', icon: <BookOpenIcon size={18} /> },
    { key: 'workbench', label: '工作台', icon: <LightningIcon size={18} /> },
    { key: 'reader', label: '阅读', icon: <BookOpenTextIcon size={18} /> },
    { key: 'codex', label: '设定集', icon: <NotebookIcon size={18} /> },
    { key: 'facts', label: '设定核对', icon: <MagnifyingGlassIcon size={18} /> },
    { key: 'config', label: '配置', icon: <GearIcon size={18} /> },
  ]

  return (
    <AppCtx.Provider value={{ dark, setDark, devMode, setDevMode, t, storyId, setStoryId }}>
      <Theme appearance={dark ? 'dark' : 'light'} accentColor="indigo" radius="medium" scaling="100%">
        <Flex style={{ minHeight: '100dvh' }} gap="0" align="stretch">
          {/* 图标窄导航(56px) */}
          <Flex direction="column" align="center" gap="1" py="4" style={{
            width: 60, flexShrink: 0, borderRight: '1px solid var(--gray-a5)',
            position: 'sticky', top: 0, height: '100dvh',
          }}>
            <Text size="4" weight="bold" mb="4" className="reader-serif"
              style={{ color: 'var(--accent-11)' }}>澜</Text>
            {NAV.map(n => (
              <Tooltip key={n.key} content={n.label} side="right">
                <Flex align="center" justify="center" style={{
                  width: 40, height: 40, borderRadius: 10, cursor: 'pointer',
                  background: tab === n.key ? 'var(--accent-a4)' : 'transparent',
                  color: tab === n.key ? 'var(--accent-11)' : 'var(--gray-11)',
                }} onClick={() => setTab(n.key)}>
                  {n.icon}
                </Flex>
              </Tooltip>
            ))}
            <Flex direction="column" align="center" gap="1" style={{ marginTop: 'auto' }}>
              <Tooltip content={devMode ? '开发者视图:开(工程术语)' : '开发者视图:关(平实语言)'} side="right">
                <Flex align="center" justify="center" style={{
                  width: 40, height: 40, borderRadius: 10, cursor: 'pointer',
                  background: devMode ? 'var(--amber-a4)' : 'transparent',
                  color: devMode ? 'var(--amber-11)' : 'var(--gray-11)',
                }} onClick={() => setDevMode(d => !d)}>
                  <CodeIcon size={18} />
                </Flex>
              </Tooltip>
              <Tooltip content={dark ? '切到浅色' : '切到暗色'} side="right">
                <Flex align="center" justify="center" style={{
                  width: 40, height: 40, borderRadius: 10, cursor: 'pointer',
                  color: 'var(--gray-11)',
                }} onClick={() => setDark(d => !d)}>
                  {dark ? <SunIcon size={18} /> : <MoonIcon size={18} />}
                </Flex>
              </Tooltip>
              <Tooltip content={`墨澜工作台 · ${storyId ? storyId.slice(0, 8) : '未选书'}`} side="right">
                <Flex align="center" justify="center" style={{ width: 40, height: 40, color: 'var(--gray-9)' }}>
                  <InfoIcon size={16} />
                </Flex>
              </Tooltip>
              <Tooltip content="退出登录" side="right">
                <Flex align="center" justify="center" style={{
                  width: 40, height: 40, borderRadius: 10, cursor: 'pointer',
                  color: 'var(--gray-11)',
                }} onClick={() => { clearToken(); setAuthed(false) }}>
                  <SignOutIcon size={18} />
                </Flex>
              </Tooltip>
            </Flex>
          </Flex>

          <Flex p="3" style={{ flex: 1, minWidth: 0, alignItems: 'stretch' }}>
            {tab === 'library' && <Library onOpen={id => { setStoryId(id); setTab('workbench') }} />}
            {tab === 'workbench' && (storyId
              ? <Workbench storyId={storyId} onOpenCodex={() => setTab('codex')} onOpenReader={() => setTab('reader')} />
              : <Text color="gray">先在书库创建或选择一部作品</Text>)}
            {tab === 'reader' && (storyId ? <Reader storyId={storyId} /> : <Text color="gray">未选书</Text>)}
            {tab === 'codex' && (storyId ? <CodexPage storyId={storyId} /> : <Text color="gray">未选书</Text>)}
            {tab === 'facts' && <FactQueue />}
            {tab === 'config' && <ConfigPage storyId={storyId} />}
          </Flex>
        </Flex>
      </Theme>
    </AppCtx.Provider>
  )
}
