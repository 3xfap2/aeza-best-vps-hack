import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, CAUSE_TITLE, LEVEL_TITLE, type CustomerPage, type Level } from '../api'
import { useLiveData } from '../live'
import { Empty, ErrorBox, eur, RiskBadge, ScoreBar, Skeleton, useChanged } from '../ui'
import { StatusLine } from './Shell'

const LEVELS: Level[] = ['high', 'medium', 'low', 'no_score', 'churned']
const COLUMNS: { key: string; title: string; right?: boolean }[] = [
  { key: 'id', title: 'ID', right: true },
  { key: 'email', title: 'Email' },
  { key: 'balance', title: 'Баланс', right: true },
  { key: 'totalReplenished', title: 'Пополнено всего', right: true },
  { key: 'score', title: 'Score', right: true },
]

export default function Customers() {
  const [params, setParams] = useSearchParams()
  const sort = params.get('sort') ?? 'score'
  const order = (params.get('order') as 'asc' | 'desc') ?? 'desc'
  const levels = params.getAll('level') as Level[]
  const page = Number(params.get('page') ?? 1)
  const hasIssues = params.get('hasIssues') === '1'
  const cause = params.get('cause')
  const [q, setQ] = useState(params.get('q') ?? '')

  const query = new URLSearchParams({ sort, order, page: String(page), pageSize: '25' })
  levels.forEach((l) => query.append('level', l))
  if (params.get('q')) query.set('q', params.get('q')!)
  if (hasIssues) query.set('hasIssues', 'true')
  if (cause) query.set('cause', cause)
  const qs = query.toString()

  const { data, error, loading } = useLiveData(() => api<CustomerPage>(`/api/v1/customers?${qs}`), [qs])
  const changed = useChanged(data?.items, (c) => `${c.score}|${c.level}`)

  useEffect(() => { document.title = 'Клиенты — Радар оттока' }, [])
  // поиск — с небольшой задержкой, чтобы не дёргать API на каждую букву
  useEffect(() => {
    const t = window.setTimeout(() => update({ q: q || null, page: null }), 300)
    return () => window.clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q])

  function update(patch: Record<string, string | null>, multi?: { level: Level[] }) {
    const next = new URLSearchParams(params)
    for (const [k, v] of Object.entries(patch)) { if (v == null) next.delete(k); else next.set(k, v) }
    if (multi) { next.delete('level'); multi.level.forEach((l) => next.append('level', l)) }
    if (next.toString() !== params.toString()) setParams(next, { replace: true })
  }

  function sortBy(key: string) {
    const nextOrder = sort === key ? (order === 'asc' ? 'desc' : 'asc') : key === 'email' || key === 'id' ? 'asc' : 'desc'
    update({ sort: key, order: nextOrder, page: null })
  }

  function toggleLevel(l: Level | 'all') {
    if (l === 'all') return update({ page: null }, { level: [] })
    update({ page: null }, { level: levels.includes(l) ? levels.filter((x) => x !== l) : [...levels, l] })
  }

  const exportUrl = `/api/v1/customers/export.csv?${new URLSearchParams([...query].filter(([k]) => k !== 'page' && k !== 'pageSize'))}`
  const pages = data ? Math.max(1, Math.ceil(data.total / data.pageSize)) : 1

  return (
    <>
      <div className="ro-top">
        <div><h1>Клиенты</h1><StatusLine asOf={data?.asOf} /></div>
        <div className="ro-top__actions"><a className="ro-btn ro-btn--quiet ro-btn--sm" href={exportUrl}>Экспорт CSV</a></div>
      </div>

      <div className="filters ro-card">
        <label className="field field--search">
          <span className="ro-sr">Поиск по email или ID</span>
          <input type="search" placeholder="Поиск по email или ID" value={q} onChange={(e) => setQ(e.target.value)} />
        </label>
        <div className="chips" role="group" aria-label="Уровень риска">
          <button type="button" aria-pressed={levels.length === 0} onClick={() => toggleLevel('all')}>Все</button>
          {LEVELS.map((l) => (
            <button key={l} type="button" aria-pressed={levels.includes(l)} onClick={() => toggleLevel(l)}>{LEVEL_TITLE[l]}</button>
          ))}
        </div>
        {cause && (
          <button type="button" className="cause-chip" onClick={() => update({ cause: null, page: null })}
            aria-label="Снять фильтр по причине">Причина: {CAUSE_TITLE[cause] ?? cause} ×</button>
        )}
        <label className="check">
          <input type="checkbox" checked={hasIssues} onChange={(e) => update({ hasIssues: e.target.checked ? '1' : null, page: null })} />
          Только с расхождениями статуса и дат
        </label>
      </div>

      {error && !data ? <ErrorBox message={error} /> : !data ? <Skeleton h={480} /> : data.items.length === 0 ? (
        <Empty>
          Нет клиентов под этим фильтром.{' '}
          <button className="ro-link" type="button" onClick={() => { setQ(''); setParams(new URLSearchParams(), { replace: true }) }}>Сбросить фильтры</button>
        </Empty>
      ) : (
        <section className="ro-card ro-top5" aria-busy={loading}>
          <div className="table-scroll">
            <table className="ro-table customers">
              <caption className="ro-sr">Клиенты, отсортированные по {sort}, {order === 'asc' ? 'по возрастанию' : 'по убыванию'}</caption>
              <thead>
                <tr>
                  {COLUMNS.map((c) => (
                    <th key={c.key} scope="col" className={c.right ? 'r' : undefined}
                      aria-sort={sort === c.key ? (order === 'asc' ? 'ascending' : 'descending') : 'none'}>
                      <button type="button" className={`sort${sort === c.key ? ' is-active' : ''}`} onClick={() => sortBy(c.key)}>
                        {c.title}<span aria-hidden="true">{sort === c.key ? (order === 'asc' ? ' ↑' : ' ↓') : ' ↕'}</span>
                      </button>
                    </th>
                  ))}
                  <th scope="col">Уровень</th>
                  <th scope="col">Главная причина</th>
                  <th scope="col" className="r" aria-sort={sort === 'expectedLoss' ? (order === 'asc' ? 'ascending' : 'descending') : 'none'}>
                    <button type="button" className={`sort${sort === 'expectedLoss' ? ' is-active' : ''}`} onClick={() => sortBy('expectedLoss')}>
                      Потеря, €<span aria-hidden="true">{sort === 'expectedLoss' ? (order === 'asc' ? ' ↑' : ' ↓') : ' ↕'}</span>
                    </button>
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((c) => (
                  <tr key={c.id} className={changed.has(c.id) ? 'is-changed' : undefined}>
                    <td className="r mono">{c.id}</td>
                    <td><Link className="ro-mono row-link" to={`/app/customers/${c.id}`}>{c.email}</Link>
                      {c.qualityIssues > 0 && <span className="issue-flag" title="Статус услуги расходится с датами">!</span>}</td>
                    <td className="r mono">{eur(c.balance)}</td>
                    <td className="r mono">{eur(c.totalReplenished)}</td>
                    <td className="r"><ScoreBar score={c.score} /></td>
                    <td><RiskBadge level={c.level} title={c.levelTitle} /></td>
                    <td className="why">{c.topReason}</td>
                    <td className="r mono">{c.score == null ? '—' : eur(c.expectedLoss)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="pager">
            <span className="ro-small">{(page - 1) * data.pageSize + 1}–{Math.min(page * data.pageSize, data.total)} из {data.total}</span>
            <div>
              <button className="ro-btn ro-btn--quiet ro-btn--sm" disabled={page <= 1} onClick={() => update({ page: String(page - 1) })}>Назад</button>
              <button className="ro-btn ro-btn--quiet ro-btn--sm" disabled={page >= pages} onClick={() => update({ page: String(page + 1) })}>Дальше</button>
            </div>
          </div>
        </section>
      )}
    </>
  )
}
