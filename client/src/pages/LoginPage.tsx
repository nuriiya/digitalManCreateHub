import { useState } from 'react'
import { login, setToken } from '../api'

export default function LoginPage({ onLogin }: { onLogin: (username: string) => void }) {
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const submit = async (e?: React.FormEvent) => {
    e?.preventDefault()
    if (busy) return
    setError('')
    setBusy(true)
    try {
      const r = await login(username.trim(), password)
      setToken(r.token)
      onLogin(r.username)
    } catch (err: any) {
      setError(err.message || '登录失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-wrap">
      <form className="login-card" onSubmit={submit}>
        <h1>rag-mvp</h1>
        <div className="login-sub">数字人雏形 · RAG 提取管线控制台</div>
        <label className="dlg-field">
          <span>用户名</span>
          <input autoFocus value={username} onChange={(e) => setUsername(e.target.value)} />
        </label>
        <label className="dlg-field">
          <span>密码</span>
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        {error && <div className="login-err">{error}</div>}
        <button className="btn green login-btn" type="submit" disabled={busy}>
          {busy ? '登录中…' : '登录'}
        </button>
      </form>
    </div>
  )
}
