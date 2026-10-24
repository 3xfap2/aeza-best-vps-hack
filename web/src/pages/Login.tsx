import { useEffect, useState, type FormEvent } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { api, ApiError } from '../api'
import { Wordmark } from '../ui'

const SSO_ERRORS: Record<string, string> = {
  state: 'Вход через SSO прерван или устарел. Попробуйте ещё раз.',
  token: 'Провайдер SSO вернул неверный ответ. Попробуйте ещё раз или войдите по паролю.',
  access_denied: 'Вход через SSO отменён.',
}

export default function Login() {
  const nav = useNavigate()
  const [params] = useSearchParams()
  const [login, setLogin] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [sso, setSso] = useState<string | null>(null)
  const [aezaOpen, setAezaOpen] = useState(false)
  const [aezaKey, setAezaKey] = useState('')
  const [notice, setNotice] = useState<string | null>(null)
  const next = params.get('next')?.startsWith('/app') ? params.get('next')! : '/app'

  useEffect(() => {
    document.title = 'Вход — Радар оттока'
    const e = params.get('sso_error')
    if (e) setError(SSO_ERRORS[e] ?? 'Не удалось войти через SSO.')
    api<{ enabled: boolean; loginUrl: string | null }>('/api/v1/auth/oidc/config')
      .then((c) => setSso(c.enabled ? c.loginUrl : null)).catch(() => setSso(null))
    // уже вошёл — сразу в панель
    fetch('/api/v1/auth/me', { credentials: 'same-origin' }).then((r) => { if (r.ok) nav(next, { replace: true }) })
  }, [nav, next, params])

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true); setError(null)
    try {
      await api('/api/v1/auth/login', { method: 'POST', json: { login, password } })
      nav(next, { replace: true })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Не удалось войти')
    } finally {
      setBusy(false)
    }
  }

  async function aeza(e: FormEvent) {
    e.preventDefault()
    setBusy(true); setError(null); setNotice(null)
    try {
      const r = await api<{ status: 'ok' | 'pending'; message: string }>('/api/v1/auth/aeza', { method: 'POST', json: { apiKey: aezaKey } })
      setAezaKey('')
      if (r.status === 'ok') nav(next, { replace: true })
      else setNotice(r.message)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Не удалось войти через Aéza')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="ro login">
      <div className="login__card ro-card">
        <Link to="/" className="login__brand" aria-label="На главную"><Wordmark /></Link>
        <h1 className="ro-h3">Вход в панель</h1>
        <form onSubmit={submit} className="form" noValidate>
          <label className="field">
            <span>Логин</span>
            <input autoComplete="username" value={login} onChange={(e) => setLogin(e.target.value)} required autoFocus />
          </label>
          <label className="field">
            <span>Пароль</span>
            <input type="password" autoComplete="current-password" value={password}
              onChange={(e) => setPassword(e.target.value)} required />
          </label>
          {error && <p className="form__error" role="alert">{error}</p>}
          <button className="ro-btn" type="submit" disabled={busy || !login || !password}>
            {busy ? 'Входим…' : 'Войти'}
          </button>
        </form>
        <div className="login__or"><span>или</span></div>
        {sso && <a className="ro-btn ro-btn--quiet" href={sso}>Войти через SSO</a>}
        {!aezaOpen ? (
          <button className="ro-btn ro-btn--quiet" type="button" onClick={() => setAezaOpen(true)}>Войти через аккаунт Aéza</button>
        ) : (
          <form onSubmit={aeza} className="form login__aeza">
            <label className="field">
              <span>API-ключ Aéza</span>
              <input type="password" autoComplete="off" value={aezaKey} onChange={(e) => setAezaKey(e.target.value)} required autoFocus />
            </label>
            <p className="ro-small">Ключ создаётся в личном кабинете Aéza (раздел API). Радар один раз проверяет его и не сохраняет.
              Новому аккаунту доступ подтверждает администратор.</p>
            <button className="ro-btn ro-btn--quiet" type="submit" disabled={busy || aezaKey.trim().length < 8}>
              {busy ? 'Проверяем…' : 'Войти через Aéza'}</button>
          </form>
        )}
        {notice && <p className="ro-small login__notice" role="status">{notice}</p>}
        <p className="ro-small login__hint">Доступ выдаёт администратор Радара. Входя, вы соглашаетесь с <Link className="ro-link" to="/privacy">политикой обработки персональных данных</Link>.</p>
      </div>
    </div>
  )
}
