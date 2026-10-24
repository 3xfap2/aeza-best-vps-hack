import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, LEVEL_TITLE, type CustomerDetail, type WhatIf } from '../api'
import { useLiveData } from '../live'
import { dateTime, ErrorBox, eur, RiskBadge, Skeleton } from '../ui'
import { StatusLine } from './Shell'

const TERMS: Record<string, string> = {
  hour: 'почасово', week: 'неделя', month: 'месяц', quarter_year: 'квартал', half_year: 'полгода', year: 'год',
}
const STATUS_CLASS: Record<string, string> = {
  active: 'low', suspended: 'mid', prolong_wait: 'high', deleted: 'none', blocked: 'none',
}

function daysText(d: number) {
  if (Math.abs(d) < 1) return d >= 0 ? `через ${Math.max(1, Math.round(d * 24))} ч` : `просрочено на ${Math.max(1, Math.round(-d * 24))} ч`
  const n = Math.round(Math.abs(d))
  return d >= 0 ? `через ${n} дн.` : `просрочено на ${n} дн.`
}

export default function Customer() {
  const { id } = useParams()
  const { data: c, error } = useLiveData(() => api<CustomerDetail>(`/api/v1/customers/${id}`), [id])
  const { data: whatIf } = useLiveData(() => api<WhatIf[]>(`/api/v1/customers/${id}/what-if`), [id])
  const [showTx, setShowTx] = useState(false)
  useEffect(() => { document.title = `${c?.email ?? 'Клиент'} — Радар оттока` }, [c?.email])

  if (error && !c) {
    return error.includes('не найден')
      ? <div className="ro-card state"><b>Клиент #{id} не найден</b><Link className="ro-link" to="/app/customers">К списку клиентов</Link></div>
      : <ErrorBox message={error} />
  }
  if (!c) return <Skeleton h={520} />
  // один признак на нескольких услугах показываем одной строкой: баллы — максимальные, услуги — списком
  const grouped = Object.values(c.contributions.reduce<Record<string, { top: (typeof c.contributions)[number]; services: string[] }>>((acc, x) => {
    const g = acc[x.feature] ??= { top: x, services: [] }
    if (x.points > g.top.points) g.top = x
    g.services.push(x.serviceName)
    return acc
  }, {})).sort((a, b) => b.top.points - a.top.points)
  const maxPoints = Math.max(...grouped.map((g) => g.top.points), 1)

  return (
    <>
      <div className="ro-top">
        <div>
          <Link className="ro-link crumbs" to="/app/customers">← Клиенты</Link>
          <h1 className="ro-mono-title">{c.email}</h1>
          <StatusLine asOf={c.asOf} />
        </div>
      </div>

      <div className="ro-grid">
        <section className="ro-panel ro-sum" aria-label="Оценка риска">
          <span className="ro-label">ID {c.id} · оценка риска</span>
          {c.score != null ? (
            <>
              <div className="cust__score">
                <span className="ro-sum__amount">{c.score}</span>
                <RiskBadge level={c.level} title={c.levelTitle} />
              </div>
              <p className="ro-small">Лояльность — обратная шкала: <b className="ro-mono">{c.loyalty} из 100</b></p>
              <p className="ro-text">Ожидаемая потеря <b className="ro-mono">{eur(c.expectedLoss)}</b> за {c.horizonDays} дней
                из выручки <span className="ro-mono">{eur(c.revenueHorizon)}</span>. LTV под угрозой — <span className="ro-mono">{eur(c.ltvAtRisk)}</span>.</p>
            </>
          ) : (
            <>
              <div className="cust__score"><RiskBadge level={c.level} title={c.levelTitle} /></div>
              <p className="ro-text">{c.note}</p>
            </>
          )}
        </section>

        <section className="ro-card fin" aria-labelledby="fin-t">
          <div className="ro-cardhead"><h2 id="fin-t">Финансы</h2></div>
          <dl>
            <div><dt>Баланс</dt><dd>{eur(c.finances.balance)}</dd></div>
            <div><dt>Бонусы ({c.finances.bonusState === 'unlocked' ? 'доступны' : 'заблокированы'})</dt><dd>{eur(c.finances.bonusBalance)}</dd></div>
            <div><dt>Реферальные, к выводу</dt><dd>{eur(c.finances.withdrawBalance)}</dd></div>
            <div><dt>Пополнено всего</dt><dd>{eur(c.finances.totalReplenished)}</dd></div>
            <div><dt>Доступно для оплаты</dt><dd>{eur(c.finances.availableFunds)}</dd></div>
            <div><dt>Нужно на продления за {c.horizonDays} дн.</dt><dd>{eur(c.finances.renewalNeed)}</dd></div>
          </dl>
        </section>

        {c.contributions.length > 0 && (
          <section className="ro-card why-card" aria-labelledby="why-t">
            <div className="ro-cardhead"><h2 id="why-t">Почему такой риск</h2><span className="ro-small">баллы = вес × сила сигнала</span></div>
            <ul className="contribs">
              {grouped.map(({ top: x, services }) => (
                <li key={x.feature}>
                  <div className="contribs__top">
                    <b>{x.title}</b>
                    <span className="ro-mono">+{x.points.toLocaleString('ru-RU')} · {x.weight} × {x.strength.toLocaleString('ru-RU')}</span>
                  </div>
                  <div className="contribs__bar"><i style={{ width: `${x.points / maxPoints * 100}%` }} /></div>
                  <span className="ro-small">{x.reason}</span>
                  <span className="contribs__svc">{services.length > 1 ? `услуги: ${services.join(', ')}` : services[0]}</span>
                </li>
              ))}
            </ul>
            <p className="ro-small">Score клиента — максимум по его действующим услугам (не больше 100).</p>
          </section>
        )}

        {c.actions.length > 0 && (
          <section className="ro-panel todo" aria-labelledby="todo-t">
            <div className="ro-cardhead"><h2 id="todo-t">Что сделать</h2></div>
            <ol>{c.actions.map((a, i) => <li key={i}>{a}</li>)}</ol>
          </section>
        )}

        {whatIf && whatIf.length > 0 && (
          <section className="ro-card whatif" aria-labelledby="wi-t">
            <div className="ro-cardhead"><h2 id="wi-t">Что будет, если…</h2><span className="ro-small">пересчёт той же моделью на копии данных клиента</span></div>
            <ul>
              {whatIf.map((w) => (
                <li key={w.key}>
                  <b>{w.title}</b>
                  <span className="ro-mono">score {c.score} → <b>{w.score ?? '—'}</b></span>
                  <RiskBadge level={w.level} title={LEVEL_TITLE[w.level]} />
                  <span className="ro-mono whatif__saved">сохраним {eur(w.saved)}</span>
                </li>
              ))}
            </ul>
          </section>
        )}

        {c.history.length > 1 && <HistoryCard history={c.history} />}

        {c.issues.length > 0 && (
          <section className="ro-card issues" aria-labelledby="iss-t" role="note">
            <div className="ro-cardhead"><h2 id="iss-t">Расхождение статуса и дат</h2></div>
            <ul>{c.issues.map((x, i) => <li key={i}>{x}</li>)}</ul>
            <p className="ro-small">На score это не влияет: статус берём как есть (на практике удаление бывает задержано), а расхождение показываем, чтобы его проверили в биллинге.</p>
          </section>
        )}

        <section className="ro-card ro-top5" aria-labelledby="svc-t">
          <div className="ro-cardhead"><h2 id="svc-t">Услуги</h2><span className="ro-mono">{c.services.length}</span></div>
          <div className="table-scroll">
            <table className="ro-table">
              <thead><tr><th>Услуга</th><th>Период</th><th>Статус</th><th>Автопродление</th><th className="r">Цена</th>
                <th>Истекает</th><th className="r">Score</th><th className="r">Потеря</th></tr></thead>
              <tbody>
                {c.services.map((s) => (
                  <tr key={s.id}>
                    <td className="mono">{s.name}{s.qualityIssues.length > 0 && <span className="issue-flag" title={s.qualityIssues.join('; ')}>!</span>}</td>
                    <td>{TERMS[s.paymentTerm] ?? s.paymentTerm}</td>
                    <td><span className={`ro-risk ro-risk--${STATUS_CLASS[s.status] ?? 'none'}`}>{s.statusTitle.toLowerCase()}</span></td>
                    <td>{s.autoProlong ? 'да' : 'нет'}</td>
                    <td className="r mono">{eur(s.price)}</td>
                    <td><span className="mono">{dateTime(s.expiresAt).slice(0, 10)}</span> <span className="ro-small">{daysText(s.daysLeft)}</span></td>
                    <td className="r mono">{s.score == null ? '—' : Math.round(s.score)}</td>
                    <td className="r mono">{s.score == null ? '—' : eur(s.expectedLoss)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="ro-card ro-top5" aria-labelledby="tx-t">
          <div className="ro-cardhead"><h2 id="tx-t">Транзакции</h2>
            <button className="ro-toggle" type="button" aria-expanded={showTx} onClick={() => setShowTx(!showTx)}>
              {showTx ? 'Свернуть' : `Показать (${c.transactions.length})`}</button></div>
          {showTx && (
            <div className="table-scroll">
              <table className="ro-table">
                <thead><tr><th>Дата</th><th>Тип</th><th>Услуга</th><th className="r">Сумма</th><th>Статус</th></tr></thead>
                <tbody>
                  {c.transactions.map((t) => (
                    <tr key={t.id}>
                      <td className="mono">{dateTime(t.createdAt)}</td>
                      <td>{t.type === 'order' ? 'заказ' : 'продление'}</td>
                      <td className="mono">{c.services.find((s) => s.id === t.serviceId)?.name ?? t.serviceId}</td>
                      <td className="r mono">{eur(t.amount)}</td>
                      <td><span className={`ro-risk ro-risk--${t.status === 'performed' ? 'low' : 'high'}`}>{t.status === 'performed' ? 'оплачена' : 'не оплачена'}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
    </>
  )
}


const REASONS: Record<string, string> = {
  startup: 'запуск', schedule: 'плановый пересчёт', settings: 'настройки модели', event: 'событие биллинга', simulate: 'перемотка времени',
  snapshot: 'новый снимок', reset: 'возврат к снимку', 'preset:case': 'данные кейса', 'preset:extended': 'расширенный набор',
}

function HistoryCard({ history }: { history: CustomerDetail['history'] }) {
  const W = 320, H = 70
  const pts = history.map((h, i) => [i / (history.length - 1) * W, H - (h.score ?? 0) / 100 * H] as const)
  const last = history[history.length - 1], first = history[0]
  return (
    <section className="ro-card history" aria-labelledby="hist-t">
      <div className="ro-cardhead"><h2 id="hist-t">История score</h2>
        <span className="ro-small">{first.score ?? '—'} → {last.score ?? '—'} за {history.length - 1} изм.</span></div>
      <svg viewBox={'-4 -4 ' + (W + 8) + ' ' + (H + 8)} className="history__chart" role="img"
        aria-label={'Score менялся от ' + (first.score ?? 'нет') + ' до ' + (last.score ?? 'нет')}>
        <line x1="0" x2={W} y1={H - 0.6 * H} y2={H - 0.6 * H} className="history__thr" />
        <line x1="0" x2={W} y1={H - 0.3 * H} y2={H - 0.3 * H} className="history__thr" />
        <polyline points={pts.map((p) => p.join(',')).join(' ')} className="history__line" />
        {pts.map((p, i) => <circle key={i} cx={p[0]} cy={p[1]} r="3" className="history__dot" />)}
      </svg>
      <ul className="log">
        {history.slice(-5).reverse().map((h, i) => (
          <li key={i}><span className="ro-small ro-mono">{dateTime(h.asOf)} · {REASONS[h.reason] ?? h.reason}</span>
            score {h.score ?? '—'} · {LEVEL_TITLE[h.level].toLowerCase()}</li>
        ))}
      </ul>
    </section>
  )
}
