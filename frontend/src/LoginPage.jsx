import { useState } from 'react'
import { api } from './api.js'
import './login.css'

/** 登录独立页(#/login):暗夜编辑风,与落地页同一设计语言。
    认证模型 ADR-0022(管理员开户制);会话续期 ADR-0029(双 token)。 */
export default function LoginPage({ onLogin }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    setErr('')
    setBusy(true)
    try {
      await api.login(username.trim(), password)
      onLogin()
    } catch (e) {
      // 区分凭证错误与服务不可达:后端没启动/重启中不是"密码错"(误导排查)
      setErr(String(e.message || '').startsWith('401')
        ? '用户名或密码错误'
        : '无法连接服务,请确认后端已启动(' + String(e.message || '').slice(0, 60) + ')')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="loginpage">
      <nav className="lp-nav lp-wrap">
        <a className="lp-logo" href="#/">墨<em>澜</em></a>
        <a className="lp-back" href="#/">← 返回落地页</a>
      </nav>

      <main className="lp-main lp-wrap">
        <div className="lp-copy">
          <h1>回到你的<br /><em>长篇</em></h1>
          <p>事实库、伏笔台账与三个确认关口,都停在你离开的地方。落印,续写。</p>
        </div>

        <form className="lp-panel" onSubmit={submit}>
          <h2>登 录</h2>
          <label className="lp-field">
            <span>用户名</span>
            <input className="lp-input" value={username}
              onChange={e => setUsername(e.target.value)}
              placeholder="用户名" autoFocus required />
          </label>
          <label className="lp-field">
            <span>密码</span>
            <input className="lp-input" type="password" value={password}
              onChange={e => setPassword(e.target.value)}
              placeholder="密码" required />
          </label>
          {err && <p className="lp-error">{err}</p>}
          <button className="lp-submit" disabled={busy || !username || !password}>
            {busy ? '登录中…' : '登录'}
          </button>
          <div className="lp-hint">账户由管理员创建;忘记密码请联系管理员重置。</div>
        </form>
      </main>

      <footer className="lp-footer lp-wrap">
        <div>墨澜 MoLan · 多 Agent 小说合写系统</div>
        <div>LangGraph · FastAPI · React</div>
      </footer>
    </div>
  )
}
