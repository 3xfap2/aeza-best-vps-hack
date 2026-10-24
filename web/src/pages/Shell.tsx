import { useEffect, useState } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { api, type User } from '../api'
import { LiveProvider, useLive } from '../live'
import { Icon, Wordmark } from '../ui'

const NAV = [
  { to: '/app', end: true, icon: 'grid', label: 'Обзор' },
  { to: '/app/customers', end: false, icon: 'users', label: 'Клиенты' },
  { to: '/app/model', end: false, icon: 'sliders', label: 'Модель' },
  { to: '/app/data', end: false, icon: 'plug', label: 'Данные' },
]

export default function Shell() {
  const [user, setUser] = useState<User | null>(null)
  const nav = useNavigate()
  useEffect(() => { api<User>('/api/v1/auth/me').then(setUser).catch(() => undefined) }, [])

  async function logout() {
    await api('/api/v1/auth/logout', { method: 'POST' }).catch(() => undefined)
    nav('/login', { replace: true })
  }

  if (!user) return <div className="ro boot" aria-busy="true"><Wordmark /></div>
  return (
    <LiveProvider>
      <div className="ro ro-app">
        <a className="ro-sr" href="#main">К содержанию</a>
        <aside className="ro-side" aria-label="Навигация панели">
          <NavLink to="/" className="side__brand"><Wordmark /></NavLink>
          <nav>
            {NAV.map((n) => (
              <NavLink key={n.to} to={n.to} end={n.end}><Icon name={n.icon} />{n.label}</NavLink>
            ))}
          </nav>
          <div className="ro-side__foot">
            <NavLink className="ro-link side__guide" to="/app/guide">Как пользоваться</NavLink>
            <span className="ro-small side__user">{user.displayName}{user.viaSso ? ' · SSO' : ''}</span>
            <a className="ro-link" href="/api/v1/docs" target="_blank" rel="noreferrer">Документация API</a>
            <button className="ro-link side__logout" type="button" onClick={logout}><Icon name="out" />Выйти</button>
          </div>
        </aside>
        <main className="ro-main" id="main">
          <Outlet />
        </main>
        <nav className="tabbar" aria-label="Разделы">
          {[...NAV, { to: "/app/guide", end: false, icon: "plus", label: "Гайд" }].map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end}>
              <Icon name={n.icon} /><span>{n.label}</span>
            </NavLink>
          ))}
        </nav>
      </div>
    </LiveProvider>
  )
}

/** Строка статуса: дата данных, сколько секунд назад пересчитано, живое соединение. */
export function StatusLine({ asOf }: { asOf?: string }) {
  const { connected, computedAt } = useLive()
  const [now, setNow] = useState(Date.now())
  useEffect(() => { const t = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(t) }, [])
  const ago = computedAt ? Math.max(0, Math.round((now - new Date(computedAt).getTime()) / 1000)) : null
  return (
    <p className="ro-status" role="status">
      <span className={connected ? 'ro-live-dot ro-pulse' : 'ro-live-dot is-off'} />
      {connected ? (
        <span>
          {asOf && <>Данные на <b>{new Date(asOf).toLocaleString('ru-RU', { timeZone: 'UTC', day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' })} UTC</b> · </>}
          пересчитано <b>{ago ?? '—'}</b> с назад
        </span>
      ) : <span>нет связи, переподключаемся…</span>}
    </p>
  )
}
