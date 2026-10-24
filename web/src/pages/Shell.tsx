import { createContext, useContext, useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { api, ROLE_TITLE, type User } from '../api'
import { LiveProvider, useLive } from '../live'
import { Icon, Wordmark } from '../ui'

const NAV = [
  { to: '/app', end: true, icon: 'grid', label: 'Обзор' },
  { to: '/app/customers', end: false, icon: 'users', label: 'Клиенты' },
  { to: '/app/retention', end: false, icon: 'phone', label: 'Удержание' },
  { to: '/app/ask', end: false, icon: 'chat', label: 'Спросить Радар' },
  { to: '/app/model', end: false, icon: 'sliders', label: 'Модель' },
  { to: '/app/data', end: false, icon: 'plug', label: 'Данные' },
  { to: '/app/team', end: false, icon: 'team', label: 'Команда' },
]
// на телефоне внизу — четыре главных раздела, остальное в «Ещё»
const TABS = NAV.filter((n) => ['/app', '/app/customers', '/app/retention', '/app/model'].includes(n.to))
const MORE = [...NAV.filter((n) => !TABS.includes(n)), { to: '/app/guide', end: false, icon: 'book', label: 'Как пользоваться' }]

const UserContext = createContext<User | null>(null)
/** Текущий пользователь панели (роль решает, какие кнопки доступны). */
export const useUser = () => useContext(UserContext)

export default function Shell() {
  const [user, setUser] = useState<User | null>(null)
  const [more, setMore] = useState(false)
  const nav = useNavigate()
  const { pathname } = useLocation()
  useEffect(() => { api<User>('/api/v1/auth/me').then(setUser).catch(() => undefined) }, [])
  useEffect(() => setMore(false), [pathname])

  async function logout() {
    await api('/api/v1/auth/logout', { method: 'POST' }).catch(() => undefined)
    nav('/login', { replace: true })
  }

  if (!user) return <div className="ro boot" aria-busy="true"><Wordmark /></div>
  return (
    <UserContext.Provider value={user}>
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
          <div className="ro-side__foot side__foot">
            <NavLink to="/app/guide"><Icon name="book" />Как пользоваться</NavLink>
            <a href="/api/v1/docs" target="_blank" rel="noreferrer"><Icon name="code" />Документация API</a>
            <div className="side__user">
              <b>{user.displayName}</b>
              <span className="ro-small">роль: {ROLE_TITLE[user.role].toLowerCase()}{user.viaSso ? ' · вход через SSO' : ''}</span>
            </div>
            <button type="button" onClick={logout}><Icon name="out" />Выйти</button>
          </div>
        </aside>
        <main className="ro-main" id="main">
          <Outlet />
        </main>
        <nav className="tabbar" aria-label="Разделы">
          {TABS.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end}>
              <Icon name={n.icon} /><span>{n.label}</span>
            </NavLink>
          ))}
          <button type="button" aria-expanded={more} aria-controls="more-menu" onClick={() => setMore(!more)}
            className={MORE.some((n) => pathname.startsWith(n.to)) ? 'active' : undefined}>
            <Icon name="more" /><span>Ещё</span>
          </button>
        </nav>
        {more && (
          <div className="more" id="more-menu">
            {MORE.map((n) => <NavLink key={n.to} to={n.to}><Icon name={n.icon} />{n.label}</NavLink>)}
            <a href="/api/v1/docs" target="_blank" rel="noreferrer"><Icon name="code" />Документация API</a>
            <div className="more__user"><b>{user.displayName}</b><span className="ro-small">роль: {ROLE_TITLE[user.role].toLowerCase()}</span></div>
            <button type="button" onClick={logout}><Icon name="out" />Выйти</button>
          </div>
        )}
      </div>
    </LiveProvider>
    </UserContext.Provider>
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
