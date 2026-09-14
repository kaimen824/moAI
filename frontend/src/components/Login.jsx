import { useState } from 'react'
import { Button, Card, Flex, Heading, Text, TextField } from '@radix-ui/themes'
import { api } from '../api.js'

/** 登录页(ADR-0022):管理员开户制,无自助注册。 */
export default function Login({ onLogin }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    setErr('')
    setBusy(true)
    try {
      const me = await api.login(username.trim(), password)
      onLogin(me)
    } catch {
      setErr('用户名或密码错误')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Flex align="center" justify="center" style={{ minHeight: '100dvh' }}>
      <Card size="3" style={{ width: 360 }}>
        <Flex direction="column" gap="4">
          <Heading size="5" className="reader-serif" style={{ color: 'var(--accent-11)' }}>
            墨澜 · 登录
          </Heading>
          <form onSubmit={submit}>
            <Flex direction="column" gap="3">
              <label>
                <Text size="1" weight="bold" mb="1">用户名</Text>
                <TextField.Root
                  value={username} onChange={e => setUsername(e.target.value)}
                  placeholder="用户名" autoFocus required
                />
              </label>
              <label>
                <Text size="1" weight="bold" mb="1">密码</Text>
                <TextField.Root
                  type="password" value={password}
                  onChange={e => setPassword(e.target.value)}
                  placeholder="密码" required
                />
              </label>
              {err && <Text size="1" color="red">{err}</Text>}
              <Button type="submit" disabled={busy || !username || !password}>
                {busy ? '登录中…' : '登录'}
              </Button>
            </Flex>
          </form>
          <Text size="1" color="gray">
            账户由管理员创建;忘记密码请联系管理员重置。
          </Text>
        </Flex>
      </Card>
    </Flex>
  )
}
