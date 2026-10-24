import { useEffect, useState, type FormEvent } from 'react'
import { ApiError, api, can, ROLE_TITLE, type FeedbackItem, type Member, type Role } from '../api'
import { useLiveData } from '../live'
import { dateTime, ErrorBox, eur, Icon, Skeleton, shortDate, useToast } from '../ui'
import { StatusLine, useUser } from './Shell'

const ROLES: Role[] = ['admin', 'marketer', 'viewer']
const RIGHTS: [string, Role][] = [
  ['Смотреть обзор, клиентов, очередь удержания, выгружать CSV', 'viewer'],
  ['Отмечать работу с клиентами: позвонили, написали, продлил', 'marketer'],
  ['Менять веса, пороги и горизонт модели', 'marketer'],
  ['Загружать данные, события биллинга, перематывать время', 'admin'],
  ['Добавлять сотрудников и менять роли', 'admin'],
]

export default function Team() {
  const me = useUser()
  const isAdmin = can(me, 'admin')
  const [tick, setTick] = useState(0)
  const toast = useToast()
  const { data, error } = useLiveData(() => api<Member[]>('/api/v1/users'), [tick])
  useEffect(() => { document.title = 'Команда — Радар оттока' }, [])

  async function call(fn: () => Promise<unknown>, ok: string) {
    try { await fn(); toast.show(ok); setTick((t) => t + 1) } catch (e) { toast.show(e instanceof ApiError ? e.message : 'Не получилось', 'error') }
  }
  const setRole = (m: Member, role: Role) =>
    call(() => api(`/api/v1/users/${m.id}`, { method: 'PATCH', json: { role } }), `${m.displayName}: роль «${ROLE_TITLE[role]}»`)
  const remove = (m: Member) => {
    if (!window.confirm(`Удалить ${m.displayName} (${m.login})? Его сессии завершатся.`)) return
    call(() => api(`/api/v1/users/${m.id}`, { method: 'DELETE' }), `${m.login} удалён`)
  }

  if (error && !data) return <ErrorBox message={error} onRetry={() => setTick((t) => t + 1)} />
  const total = (data ?? []).reduce((a, m) => ({ actions: a.actions + m.actions, renewed: a.renewed + m.renewed, saved: a.saved + m.saved }),
    { actions: 0, renewed: 0, saved: 0 })

  return (
    <>
      <div className="ro-top"><div><h1>Команда</h1><StatusLine /></div></div>
      {!data ? <Skeleton h={320} /> : (
        <div className="ro-grid">
          <section className="ro-card ro-top5" aria-labelledby="team-t">
            <div className="ro-cardhead"><h2 id="team-t">Сотрудники</h2>
              <span className="ro-small">за 7 дней: {total.actions} отметок, продлили {total.renewed}, сохранено {eur(total.saved)}</span></div>
            <div className="table-scroll">
              <table className="ro-table">
                <thead><tr><th>Сотрудник</th><th>Роль</th><th className="r">Отметок</th><th className="r">Клиентов</th>
                  <th className="r">Продлили</th><th className="r">Сохранено</th><th>С нами с</th><th><span className="ro-sr">Действия</span></th></tr></thead>
                <tbody>
                  {data.map((m) => {
                    const self = m.id === me?.id
                    return (
                      <tr key={m.id}>
                        <td><b>{m.displayName}</b>{self && <span className="ro-small"> · это вы</span>}
                          <div className="ro-small ro-mono">{m.login}{m.viaSso ? ' · SSO' : ''}{m.viaAeza ? ' · аккаунт Aéza' : ''}</div></td>
                        <td>{isAdmin && !self
                          ? <select className={`team__role${m.role === 'pending' ? ' is-pending' : ''}`} aria-label={`Роль ${m.login}`} value={m.role} onChange={(e) => setRole(m, e.target.value as Role)}>
                            {m.role === 'pending' && <option value="pending" disabled>Ждёт подтверждения — выдать роль</option>}
                            {ROLES.map((r) => <option key={r} value={r}>{ROLE_TITLE[r]}</option>)}</select>
                          : <span className={`work-tag team__tag--${m.role}`}>{ROLE_TITLE[m.role]}</span>}</td>
                        <td className="r mono">{m.actions}</td>
                        <td className="r mono">{m.customers}</td>
                        <td className="r mono">{m.renewed}</td>
                        <td className="r mono">{eur(m.saved)}</td>
                        <td className="mono">{shortDate(m.createdAt)}</td>
                        <td className="r">{isAdmin && !self &&
                          <button type="button" className="ro-btn ro-btn--sm ro-btn--quiet" onClick={() => remove(m)}>Удалить</button>}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </section>

          {isAdmin && <AddMember onDone={(login) => { toast.show(`${login} добавлен`); setTick((t) => t + 1) }} />}
          {isAdmin && <FeedbackList />}

          <section className={`ro-card team__rights${isAdmin ? '' : ' span12'}`} aria-labelledby="rights-t">
            <div className="ro-cardhead"><h2 id="rights-t">Что может каждая роль</h2></div>
            <div className="table-scroll">
              <table className="ro-table">
                <thead><tr><th>Действие</th>{ROLES.map((r) => <th key={r} className="c">{ROLE_TITLE[r]}</th>)}</tr></thead>
                <tbody>
                  {RIGHTS.map(([what, min]) => (
                    <tr key={what}><td>{what}</td>
                      {ROLES.map((r) => {
                        const ok = ROLES.indexOf(r) <= ROLES.indexOf(min)
                        return <td key={r} className={`c team__yes${ok ? '' : ' is-no'}`}><Icon name={ok ? 'yes' : 'no'} />
                          <span className="ro-sr">{ok ? 'да' : 'нет'}</span></td>
                      })}</tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="ro-small team__note">Права проверяет сервер: кнопку можно скрыть, но запрос без нужной роли всё равно получит отказ (403).
              Вход через SSO компании по умолчанию получает роль «Маркетолог».</p>
          </section>
        </div>
      )}
      {toast.node}
    </>
  )
}

function AddMember({ onDone }: { onDone: (login: string) => void }) {
  const [form, setForm] = useState({ login: '', displayName: '', password: '', role: 'marketer' as Role })
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  async function submit(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError(null)
    try {
      await api('/api/v1/users', { method: 'POST', json: form })
      onDone(form.login); setForm({ login: '', displayName: '', password: '', role: 'marketer' })
    } catch (err) { setError(err instanceof ApiError ? err.message : 'Не удалось добавить') } finally { setBusy(false) }
  }
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value })
  return (
    <section className="ro-card team__add" aria-labelledby="add-t">
      <div className="ro-cardhead"><h2 id="add-t">Добавить сотрудника</h2></div>
      <form className="form" onSubmit={submit}>
        <label className="field"><span>Имя</span><input required maxLength={100} value={form.displayName} onChange={set('displayName')} placeholder="Анна, маркетинг" /></label>
        <label className="field"><span>Логин</span><input required minLength={3} maxLength={64} pattern="[A-Za-z0-9._@\-]+" value={form.login} onChange={set('login')} placeholder="anna" autoComplete="off" /></label>
        <label className="field"><span>Пароль — не короче 10 символов</span><input required type="password" minLength={10} value={form.password} onChange={set('password')} autoComplete="new-password" /></label>
        <label className="field"><span>Роль</span>
          <select value={form.role} onChange={set('role')}>{ROLES.map((r) => <option key={r} value={r}>{ROLE_TITLE[r]}</option>)}</select></label>
        {error && <p className="form__error" role="alert">{error}</p>}
        <button className="ro-btn ro-btn--sm" type="submit" disabled={busy}>Добавить</button>
      </form>
    </section>
  )
}

const KIND_TITLE: Record<FeedbackItem['kind'], string> = { idea: 'идея', problem: 'проблема', other: 'другое' }

/** Отзывы сотрудников о Радаре (кнопка «Обратная связь» в меню). Видит администратор. */
function FeedbackList() {
  const [tick, setTick] = useState(0)
  const { data } = useLiveData(() => api<FeedbackItem[]>('/api/v1/feedback?limit=50'), [tick])
  async function toggle(f: FeedbackItem) {
    await api(`/api/v1/feedback/${f.id}`, { method: 'PATCH', json: { status: f.status === 'new' ? 'done' : 'new' } }).catch(() => undefined)
    setTick((t) => t + 1)
  }
  const fresh = (data ?? []).filter((f) => f.status === 'new').length
  return (
    <section className="ro-card ro-top5 span12" aria-labelledby="fb-list-t">
      <div className="ro-cardhead"><h2 id="fb-list-t">Отзывы о Радаре</h2>
        <span className="ro-small">{data ? `новых: ${fresh}` : ''}</span></div>
      {!data ? <Skeleton h={80} /> : data.length === 0
        ? <p className="ro-small fb-list__empty">Пока пусто. Сотрудники пишут через «Обратная связь» в меню. Отзывы можно передавать в вашу CRM — см. README, раздел «Подключить CRM».</p>
        : (
          <ul className="fb-list">
            {data.map((f) => (
              <li key={f.id} className={f.status === 'done' ? 'is-done' : undefined}>
                <div className="fb-list__head">
                  <span className={`work-tag${f.kind === 'problem' ? ' work-tag--churned' : f.kind === 'idea' ? ' work-tag--renewed' : ''}`}>{KIND_TITLE[f.kind]}</span>
                  <b>{f.authorName}</b><span className="ro-small ro-mono">{dateTime(f.createdAt)}{f.page ? ` · ${f.page}` : ''}</span>
                  <button className="ro-btn ro-btn--sm ro-btn--quiet" type="button" onClick={() => toggle(f)}>
                    {f.status === 'new' ? 'Разобрано' : 'Вернуть в новые'}</button>
                </div>
                <p>{f.text}</p>
              </li>
            ))}
          </ul>
        )}
    </section>
  )
}
