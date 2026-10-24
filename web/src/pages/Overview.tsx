import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, LEVEL_TITLE, type CustomerItem, type CustomerPage, type Level, type Renewal, type Summary, type Timeline } from '../api'
import { useLiveData } from '../live'
import { ErrorBox, eur, eur0, Icon, radarLevel, RiskBadge, Skeleton, useChanged } from '../ui'
import { StatusLine } from './Shell'

async function allCustomers(): Promise<CustomerItem[]> {
  const first = await api<CustomerPage>('/api/v1/customers?pageSize=200&sort=expectedLoss&order=desc')
  const pages = Math.ceil(first.total / first.pageSize)
  const rest = await Promise.all(Array.from({ length: pages - 1 }, (_, i) =>
    api<CustomerPage>(`/api/v1/customers?pageSize=200&sort=expectedLoss&order=desc&page=${i + 2}`)))
  return first.items.concat(...rest.map((p) => p.items))
}

function angleOf(id: number) {
  // стабильный угол точки на радаре — от id клиента
  const x = Math.sin(id * 12.9898) * 43758.5453
  return (x - Math.floor(x)) * 360
}

export default function Overview() {
  const { data, error, loading } = useLiveData(async () => {
    const [summary, timeline, customers, renewals] = await Promise.all([
      api<Summary>('/api/v1/analytics/summary'),
      api<Timeline>('/api/v1/analytics/churn-timeline'),
      allCustomers(),
      api<Renewal[]>('/api/v1/analytics/renewals?days=14'),
    ])
    return { summary, timeline, customers, renewals }
  })
  const [howOpen, setHowOpen] = useState(false)
  const [intro, setIntro] = useState(() => { try { return localStorage.getItem('radar.intro') !== 'hidden' } catch { return true } })
  const hideIntro = () => { setIntro(false); try { localStorage.setItem('radar.intro', 'hidden') } catch { /* приватный режим */ } }
  const top5 = useMemo(() => (data?.customers ?? []).filter((c) => c.score != null).slice(0, 5), [data])
  const changed = useChanged(top5, (c) => `${c.score}|${c.expectedLoss}`)

  useEffect(() => { document.title = 'Обзор — Радар оттока' }, [])
  if (error && !data) return <ErrorBox message={error} onRetry={() => location.reload()} />

  const s = data?.summary
  return (
    <>
      <div className="ro-top">
        <div>
          <h1>Обзор</h1>
          <StatusLine asOf={s?.asOf} />
        </div>
        <div className="ro-top__actions">
          <a className="ro-btn ro-btn--quiet ro-btn--sm" href="/api/v1/customers/export.csv?level=high&level=medium">Экспорт CSV</a>
          <Link className="ro-btn ro-btn--sm" to="/app/model">Настроить модель<Icon name="arrow" /></Link>
        </div>
      </div>

      {intro && (
        <section className="ro-panel intro" aria-labelledby="intro-t">
          <div className="intro__head"><h2 id="intro-t">С чего начать</h2>
            <button className="ro-link" type="button" onClick={hideIntro}>Скрыть</button></div>
          <ol>
            <li><b>Сумма под угрозой</b> — сколько выручки за 30 дней, скорее всего, не продлят.</li>
            <li><b>Топ клиентов</b> внизу — откройте первого: там причина риска и что сделать.</li>
            <li><b>«За что мы теряем деньги»</b> — кампании по причинам и CSV для обзвона.</li>
            <li><b>«Удержание»</b> — кому звонить сегодня, до какого срока и окупится ли звонок.</li>
            <li><b>«Модель»</b> — подвиньте вес признака и посмотрите, как изменится рейтинг.</li>
          </ol>
          <Link className="ro-link" to="/app/guide">Полный гайд: шесть шагов, уровни риска, Telegram-бот →</Link>
        </section>
      )}

      {!s ? <div className="ro-grid"><div className="span12"><Skeleton h={220} /></div><div className="span12"><Skeleton h={360} /></div></div> : (
        <div className="ro-grid" aria-busy={loading}>
          <section className="ro-panel ro-sum" aria-labelledby="sum-t">
            <span className="ro-label" id="sum-t">Ожидаемая потеря за {s.horizonDays} дней</span>
            <div className="ro-sum__amount"><span className="ro-mark" data-draw="done">{eur(s.expectedLoss)}</span></div>
            <p className="ro-sum__paid">
              <span>Реально собираемая: <b className="ro-mono">{eur(s.expectedLossPaid)}</b></span>
              <span className="ro-small">выручка клиента не больше, чем он платит в среднем или держит на счёте — почасовой сервер по прайсу стоит тысячи евро в месяц, но столько клиент не заплатит</span>
            </p>
            <p className="ro-text" style={{ maxWidth: '44em' }}>
              {s.levels.high} клиентов высокого риска и {s.levels.medium} среднего из {s.customersTotal}.
              Сумма — выручка услуг за {s.horizonDays} дней × вероятность непродления, сложенная по всем клиентам.
            </p>
            <div className="ro-sum__row">
              <button className="ro-toggle" type="button" aria-expanded={howOpen} aria-controls="howto"
                onClick={() => setHowOpen(!howOpen)}>Как считается<Icon name="chev" /></button>
              <span className="ro-small">Худший случай: <b className="ro-mono" style={{ color: 'var(--ink)' }}>{eur(s.worstCase)}</b></span>
              <span className="ro-small">На счетах клиентов под риском: <b className="ro-mono" style={{ color: 'var(--ink)' }}>{eur(s.balanceAtRisk)}</b></span>
              <span className="ro-small" title={`LTV = месячная выручка / базовый отток ${s.ltvMonthlyChurn} % в месяц, не больше 36 мес.`}>
                LTV под угрозой: <b className="ro-mono" style={{ color: 'var(--ink)' }}>{eur(s.ltvAtRisk)}</b></span>
            </div>
            <div className={`ro-howto${howOpen ? ' is-open' : ''}`} id="howto"><div><div className="ro-howto__in">
              <code className="ro-mono">потеря = Σ выручка услуги за {s.horizonDays} дн. × score услуги / 100</code>
              <p className="ro-small">{s.explanation}</p>
              <table><thead><tr><th>Уровень</th><th>Клиентов</th><th>Под угрозой</th></tr></thead><tbody>
                {(['high', 'medium', 'low'] as Level[]).map((l) => (
                  <tr key={l}><td><RiskBadge level={l} title={LEVEL_TITLE[l]} /></td><td className="ro-mono">{s.levels[l]}</td><td>{eur(s.levelLoss[l])}</td></tr>
                ))}
              </tbody></table>
              <code className="ro-mono">реально собираемая = потеря клиента × min(1, платёжеспособность / выручка за {s.horizonDays} дн.)</code>
              <p className="ro-small">Платёжеспособность — больше из двух: деньги на счёте или средний платёж (пополнено всего / месяцы с первой услуги) за {s.horizonDays} дней. По этой сумме считаем отдачу от удержания.</p>
              <code className="ro-mono">LTV под угрозой = Σ месячная выручка / {s.ltvMonthlyChurn} % × score / 100</code>
              <p className="ro-small">LTV — выручка за ожидаемый срок жизни клиента при базовом оттоке {s.ltvMonthlyChurn} % в месяц (не больше 36 месяцев): сколько бизнес теряет, если клиент уйдёт насовсем, а не только в этом месяце.</p>
              <p className="ro-small">«Нет оценки» ({s.levels.no_score}) и «Ушёл» ({s.levels.churned}) в сумму не входят. Худший случай — вся выручка клиентов высокого риска.</p>
            </div></div></div>
          </section>

          <section className="ro-card ro-dist" aria-labelledby="dist-t">
            <div className="ro-cardhead"><h2 id="dist-t">Клиенты по уровням</h2><span className="ro-mono">{s.customersTotal}</span></div>
            <div className="ro-dist__bar" role="img" aria-label={`${s.levels.high} высокий, ${s.levels.medium} средний, ${s.levels.low} низкий, ${s.levels.no_score + s.levels.churned} без оценки или ушли`}>
              <span className="h" style={{ flex: s.levels.high }} /><span className="m" style={{ flex: s.levels.medium }} />
              <span className="l" style={{ flex: s.levels.low }} /><span className="n" style={{ flex: s.levels.no_score + s.levels.churned }} />
            </div>
            <table><tbody>
              {(['high', 'medium', 'low', 'no_score', 'churned'] as Level[]).map((l) => (
                <tr key={l}>
                  <td><Link to={`/app/customers?level=${l}`} className="dist__link"><RiskBadge level={l} title={LEVEL_TITLE[l]} /></Link></td>
                  <td className="n">{s.levels[l]}</td>
                  <td className="n" style={{ color: 'var(--ink-2)' }}>{s.customersTotal ? Math.round(s.levels[l] / s.customersTotal * 100) : 0} %</td>
                  <td className="n">{l === 'no_score' || l === 'churned' ? '—' : eur(s.levelLoss[l])}</td>
                </tr>
              ))}
            </tbody></table>
            {s.qualityIssues > 0 && <Link className="ro-small issues-link" to="/app/customers?hasIssues=1">Расхождений статуса и дат: {s.qualityIssues} →</Link>}
          </section>

          <RadarCard customers={data!.customers} total={s.expectedLoss} />
          <ChartCard timeline={data!.timeline} servicesAtRisk={s.servicesAtRisk} />

          <CausesCard summary={s} />
          <RenewalsCard renewals={data!.renewals} />

          <section className="ro-card ro-top5" aria-labelledby="t5-t">
            <div className="ro-cardhead"><h2 id="t5-t">Топ-5 клиентов по сумме под угрозой</h2>
              <Link className="ro-link" to="/app/customers?sort=expectedLoss">Все клиенты<Icon name="chev" /></Link></div>
            <div className="table-scroll">
              <table className="ro-table">
                <thead><tr><th scope="col">Клиент</th><th scope="col">Уровень</th><th scope="col" className="r">Score</th>
                  <th scope="col">Главная причина</th><th scope="col" className="r">Под угрозой</th><th scope="col" className="r">Баланс</th>
                  <th scope="col"><span className="ro-sr">Действие</span></th></tr></thead>
                <tbody>
                  {top5.map((c) => (
                    <tr key={c.id} className={changed.has(c.id) ? 'is-changed' : undefined}>
                      <td><div className="ro-mono">{c.email}</div><div className="ro-small">ID {c.id}</div></td>
                      <td><RiskBadge level={c.level} title={c.levelTitle} /></td>
                      <td className="r mono">{c.score}</td>
                      <td>{c.topReason}</td>
                      <td className="r mono">{eur(c.expectedLoss)}</td>
                      <td className="r mono">{eur(c.balance)}</td>
                      <td className="r"><Link to={`/app/customers/${c.id}`} aria-label={`Открыть ${c.email}`}>Открыть</Link></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </div>
      )}
    </>
  )
}

function RadarCard({ customers, total }: { customers: CustomerItem[]; total: number }) {
  const host = useRef<HTMLDivElement>(null)
  const radar = useRef<ReturnType<typeof window.Ottok.Radar.mount>>()
  const [filter, setFilter] = useState('all')
  const radarData = useMemo(() => {
    const clients = customers.map((c) => ({
      id: `user-${String(c.id).padStart(4, '0')}`, email: c.email, level: radarLevel(c.level), score: c.score,
      threat: c.expectedLoss, reason: c.topReason ?? '', balance: c.balance, angle: angleOf(c.id), jitter: (c.id % 97) / 97,
    }))
    const counts: Record<string, number> = { high: 0, mid: 0, low: 0, none: 0, gone: 0 }
    const sums: Record<string, number> = { high: 0, mid: 0, low: 0, none: 0, gone: 0 }
    clients.forEach((c) => { counts[c.level]++; sums[c.level] += c.threat })
    return { clients, counts, sums, total }
  }, [customers, total])

  useEffect(() => {
    if (!host.current) return
    radar.current = window.Ottok.Radar.mount(host.current, { mode: 'panel', head: false, foot: false, events: false, animate: false, data: radarData })
    radar.current.setFilter(filter)
    return () => radar.current?.destroy()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [radarData])

  const pick = (f: string) => { setFilter(f); radar.current?.setFilter(f) }
  return (
    <section className="ro-card ro-pradar" aria-labelledby="rad-t">
      <div className="ro-cardhead"><h2 id="rad-t">Радар клиентов</h2>
        <div className="ro-radar__filters" role="group" aria-label="Фильтр по уровню">
          {[['all', 'Все'], ['high', 'Высокий'], ['mid', 'Средний'], ['low', 'Низкий']].map(([k, t]) => (
            <button key={k} type="button" aria-pressed={filter === k} onClick={() => pick(k)}>{t}</button>
          ))}
        </div>
      </div>
      <div ref={host} />
      <p className="ro-small">Чем ближе к центру — тем выше риск; размер точки — сумма под угрозой. Наведите или выберите точку клавишей Tab.</p>
    </section>
  )
}

function ChartCard({ timeline, servicesAtRisk }: { timeline: Timeline; servicesAtRisk: number }) {
  const host = useRef<HTMLDivElement>(null)
  const [mode, setMode] = useState<'count' | 'eur'>('count')
  const today = timeline.asOf.slice(0, 10)
  const idx = timeline.points.findIndex((p) => p.date === today)
  const factPts = timeline.points.slice(Math.max(0, idx - 22), idx + 1)
  const fcPts = timeline.points.slice(idx + 1, idx + 8)
  const fact = factPts.map((p) => mode === 'count' ? p.factRecorded + p.factRestored : +(p.factRecordedEur + p.factRestoredEur).toFixed(2))
  const forecast = fcPts.map((p) => mode === 'count' ? +p.forecast.toFixed(1) : p.forecastEur)
  const restored = factPts.map((p) => mode === 'count' ? p.factRestored : +p.factRestoredEur.toFixed(2))
  const labels = factPts.concat(fcPts).map((p, i) => {
    const d = Number(p.date.slice(8)); const isToday = p.date === today
    return isToday ? `${d} ${['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'][Number(p.date.slice(5, 7)) - 1]}` : i % 5 === 0 ? String(d) : ''
  })
  useEffect(() => {
    if (!host.current) return
    const max = Math.max(mode === 'count' ? 2 : 10, ...fact, ...forecast)
    const step = mode === 'count' ? 2 : 10
    const chart = window.Ottok.ForecastChart.mount(host.current, {
      fact, forecast, restored, labels, width: 720, height: 330, max: Math.ceil(max / step) * step, unit: mode,
      alt: mode === 'count'
        ? `Отток по дням, услуг: ${fact.length} дней удалений и ${forecast.length} дней ожидаемых непродлений`
        : `Отток по дням, € выручки: ${fact.length} дней факта и ${forecast.length} дней прогноза`,
    })
    return () => chart.destroy()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [timeline, mode])
  const sum = (a: number[]) => a.reduce((x, y) => x + y, 0)
  const fmt = (v: number) => mode === 'count' ? v.toLocaleString('ru-RU', { maximumFractionDigits: 1 }) : eur0(v)
  return (
    <section className="ro-card ro-pchart" aria-labelledby="ch-t">
      <div className="ro-cardhead"><h2 id="ch-t">Отток по дням</h2>
        <div className="chips" role="group" aria-label="Единицы графика">
          <button type="button" aria-pressed={mode === 'count'} onClick={() => setMode('count')}>Услуги</button>
          <button type="button" aria-pressed={mode === 'eur'} onClick={() => setMode('eur')}>€ выручки</button>
        </div></div>
      <div ref={host} />
      <div className="ro-pchart__stats">
        <div><span className="ro-small">Услуг под риском сейчас</span><b>{servicesAtRisk}</b></div>
        <div><span className="ro-small">{mode === 'count' ? 'Удалено' : 'Ушло'} за {fact.length} дн.</span><b>{fmt(sum(fact))}</b></div>
        <div><span className="ro-small">{mode === 'count' ? 'Ожидается непродлений' : 'Прогноз'} на {forecast.length} дн.</span><b>{fmt(sum(forecast))}</b></div>
      </div>
      <p className="ro-small">{timeline.summary} Тёмные столбцы — удаления из журнала переходов, светлые — удаления из исходного снимка: даты в данных нет, она восстановлена по правилу «срок + 5 дней». Штриховка — прогноз: сумма вероятностей непродления по дням.</p>
    </section>
  )
}

const KIND_TITLE = {
  involuntary: ['Недобровольный отток', 'клиент мог бы остаться, но не прошла оплата — напомнить и помочь оплатить'],
  voluntary: ['Добровольный отток', 'клиент сам выключил автопродление или сервер — узнать причину и предложить вариант'],
} as const

function CausesCard({ summary }: { summary: Summary }) {
  const [rate, setRate] = useState(30)
  const atRisk = summary.causes.reduce((a, g) => a + g.expectedLoss, 0)
  const atRiskPaid = summary.causes.reduce((a, g) => a + g.expectedLossPaid, 0)
  const kinds = (['involuntary', 'voluntary'] as const).map((k) => {
    const items = summary.causes.filter((g) => g.kind === k)
    return { k, items, loss: items.reduce((a, g) => a + g.expectedLoss, 0), count: items.reduce((a, g) => a + g.customers, 0) }
  })
  const max = Math.max(...summary.causes.map((g) => g.expectedLoss), 1)
  return (
    <section className="ro-card causes" aria-labelledby="cause-t">
      <div className="ro-cardhead"><h2 id="cause-t">За что мы теряем деньги</h2>
        <span className="ro-small">клиенты высокого и среднего риска по главной причине</span></div>
      {summary.causes.length === 0 ? <p className="ro-small">Клиентов под риском нет.</p> : (
        <div className="causes__grid">
          {kinds.map(({ k, items, loss, count }) => (
            <div key={k} className="causes__kind">
              <div className="causes__head">
                <b>{KIND_TITLE[k][0]}</b>
                <span className="ro-mono">{eur(loss)} · {count} кл. · {atRisk ? Math.round(loss / atRisk * 100) : 0} %</span>
              </div>
              <p className="ro-small">{KIND_TITLE[k][1]}</p>
              {items.length === 0 ? <p className="ro-small">—</p> : (
                <ul>
                  {items.map((g) => (
                    <li key={g.feature}>
                      <div className="causes__row">
                        <span>{g.title}</span>
                        <span className="ro-mono">{eur(g.expectedLoss)} · {g.customers}</span>
                      </div>
                      <div className={`causes__bar causes__bar--${k}`}><i style={{ width: `${g.expectedLoss / max * 100}%` }} /></div>
                      <div className="causes__links">
                        <Link className="ro-link" to={`/app/customers?cause=${g.feature}&sort=expectedLoss`}>Список</Link>
                        <a className="ro-link" href={`/api/v1/customers/export.csv?cause=${g.feature}&sort=expectedLoss&includeEmail=true`}>CSV для обзвона</a>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ))}
          <div className="causes__roi ro-panel">
            <label htmlFor="roi" className="ro-label">Если удержать</label>
            <div className="causes__roiv"><b className="ro-display">{rate} %</b><span className="ro-small">клиентов под риском</span></div>
            <input id="roi" className="ro-range" type="range" min={0} max={100} step={5} value={rate}
              style={{ ['--p' as string]: `${rate}%` }} onChange={(e) => setRate(Number(e.target.value))} />
            <p className="ro-text">сохраним <b className="ro-mono">{eur(atRiskPaid * rate / 100)}</b> реально собираемой выручки за {summary.horizonDays} дней</p>
            <p className="ro-small">по прайсу — {eur(atRisk * rate / 100)}</p>
            <Link className="ro-btn ro-btn--sm" to="/app/retention">Очередь удержания<Icon name="arrow" /></Link>
            {summary.retention && (
              <div className="causes__ret">
                <span className="ro-label">Работа с клиентами</span>
                <p className="ro-small">Отработано <b>{summary.retention.worked}</b> из {summary.retention.atRisk} под риском · продлили <b>{summary.retention.renewed}</b> · ушли <b>{summary.retention.churned}</b></p>
                {Object.keys(summary.retention.reasons).length > 0 && (
                  <p className="ro-small">Причины ухода: {Object.entries(summary.retention.reasons).map(([r, n]) => `${r} — ${n}`).join(', ')}</p>
                )}
              </div>
            )}
            <p className="ro-small">Ориентир рынка: напоминания об оплате возвращают 20–50 % неуспешных платежей. Расчёт: реально собираемая потеря клиентов под риском × доля удержанных.</p>
          </div>
        </div>
      )}
    </section>
  )
}

const PROBLEM: Record<string, string> = { no_money: 'не хватит денег', no_autoprolong: 'автопродление выключено' }

function RenewalsCard({ renewals }: { renewals: Renewal[] }) {
  const [all, setAll] = useState(false)
  const problems = renewals.filter((r) => r.problem)
  const lost = problems.reduce((a, r) => a + r.price, 0)
  const shown = all ? problems : problems.slice(0, 8)
  const day = (iso: string) => new Date(iso).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', weekday: 'short', timeZone: 'UTC' })
  return (
    <section className="ro-card ro-top5 renewals" aria-labelledby="ren-t">
      <div className="ro-cardhead"><h2 id="ren-t">Календарь продлений · 14 дней</h2>
        <span className="ro-small">предупредить заранее: {problems.length} из {renewals.length} продлений под угрозой, {eur(lost)}</span></div>
      {problems.length === 0 ? <p className="ro-small renewals__empty">Все продления в ближайшие 14 дней пройдут автоматически.</p> : (
        <div className="table-scroll">
          <table className="ro-table">
            <thead><tr><th>Дата</th><th>Клиент</th><th>Услуга</th><th className="r">Цена</th><th className="r">На счёте</th><th>Что помешает</th><th><span className="ro-sr">Действие</span></th></tr></thead>
            <tbody>
              {shown.map((r) => (
                <tr key={r.serviceId}>
                  <td className="mono">{day(r.date)}</td>
                  <td className="mono">{r.email}</td>
                  <td className="mono">{r.service}</td>
                  <td className="r mono">{eur(r.price)}</td>
                  <td className="r mono">{eur(r.funds)}</td>
                  <td><span className={'ro-risk ro-risk--' + (r.problem === 'no_money' ? 'high' : 'mid')}>{PROBLEM[r.problem!]}</span></td>
                  <td className="r"><Link to={'/app/customers/' + r.customerId}>Открыть</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {problems.length > 8 && (
        <div className="pager"><span className="ro-small">Показано {shown.length} из {problems.length}</span>
          <button className="ro-btn ro-btn--quiet ro-btn--sm" type="button" onClick={() => setAll(!all)}>{all ? 'Свернуть' : 'Показать все'}</button></div>
      )}
    </section>
  )
}
