import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, LEVEL_TITLE, type CustomerItem, type CustomerPage, type Level, type Summary, type Timeline } from '../api'
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
    const [summary, timeline, customers] = await Promise.all([
      api<Summary>('/api/v1/analytics/summary'),
      api<Timeline>('/api/v1/analytics/churn-timeline'),
      allCustomers(),
    ])
    return { summary, timeline, customers }
  })
  const [howOpen, setHowOpen] = useState(false)
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

      {!s ? <div className="ro-grid"><div className="span12"><Skeleton h={220} /></div><div className="span12"><Skeleton h={360} /></div></div> : (
        <div className="ro-grid" aria-busy={loading}>
          <section className="ro-panel ro-sum" aria-labelledby="sum-t">
            <span className="ro-label" id="sum-t">Ожидаемая потеря за {s.horizonDays} дней</span>
            <div className="ro-sum__amount"><span className="ro-mark" data-draw="done">{eur(s.expectedLoss)}</span></div>
            <p className="ro-text" style={{ maxWidth: '44em' }}>
              {s.levels.high} клиентов высокого риска и {s.levels.medium} среднего из {s.customersTotal}.
              Сумма — выручка услуг за {s.horizonDays} дней × вероятность непродления, сложенная по всем клиентам.
            </p>
            <div className="ro-sum__row">
              <button className="ro-toggle" type="button" aria-expanded={howOpen} aria-controls="howto"
                onClick={() => setHowOpen(!howOpen)}>Как считается<Icon name="chev" /></button>
              <span className="ro-small">Худший случай: <b className="ro-mono" style={{ color: 'var(--ink)' }}>{eur(s.worstCase)}</b></span>
              <span className="ro-small">На счетах клиентов под риском: <b className="ro-mono" style={{ color: 'var(--ink)' }}>{eur(s.balanceAtRisk)}</b></span>
            </div>
            <div className={`ro-howto${howOpen ? ' is-open' : ''}`} id="howto"><div><div className="ro-howto__in">
              <code className="ro-mono">потеря = Σ выручка услуги за {s.horizonDays} дн. × score услуги / 100</code>
              <p className="ro-small">{s.explanation}</p>
              <table><thead><tr><th>Уровень</th><th>Клиентов</th><th>Под угрозой</th></tr></thead><tbody>
                {(['high', 'medium', 'low'] as Level[]).map((l) => (
                  <tr key={l}><td><RiskBadge level={l} title={LEVEL_TITLE[l]} /></td><td className="ro-mono">{s.levels[l]}</td><td>{eur(s.levelLoss[l])}</td></tr>
                ))}
              </tbody></table>
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
            {s.qualityIssues > 0 && <Link className="ro-small issues-link" to="/app/customers?hasIssues=1">Противоречий в данных: {s.qualityIssues} →</Link>}
          </section>

          <RadarCard customers={data!.customers} total={s.expectedLoss} />
          <ChartCard timeline={data!.timeline} />

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

function ChartCard({ timeline }: { timeline: Timeline }) {
  const host = useRef<HTMLDivElement>(null)
  const today = timeline.asOf.slice(0, 10)
  const idx = timeline.points.findIndex((p) => p.date === today)
  const factPts = timeline.points.slice(Math.max(0, idx - 22), idx + 1)
  const fcPts = timeline.points.slice(idx + 1, idx + 8)
  const fact = factPts.map((p) => +(p.factRecordedEur + p.factRestoredEur).toFixed(2))
  const forecast = fcPts.map((p) => p.forecastEur)
  const labels = factPts.concat(fcPts).map((p, i) => {
    const d = Number(p.date.slice(8)); const isToday = p.date === today
    return isToday ? `${d} ${['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'][Number(p.date.slice(5, 7)) - 1]}` : i % 5 === 0 ? String(d) : ''
  })
  useEffect(() => {
    if (!host.current) return
    const max = Math.max(10, ...fact, ...forecast)
    const chart = window.Ottok.ForecastChart.mount(host.current, {
      fact, forecast, labels, width: 720, height: 330, max: Math.ceil(max / 10) * 10,
      alt: `Отток по дням, € выручки: ${fact.length} дней факта и ${forecast.length} дней прогноза`,
    })
    return () => chart.destroy()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [timeline])
  const sum = (a: number[]) => a.reduce((x, y) => x + y, 0)
  return (
    <section className="ro-card ro-pchart" aria-labelledby="ch-t">
      <div className="ro-cardhead"><h2 id="ch-t">Отток по дням</h2><span className="ro-small">€ выручки в день · факт и прогноз</span></div>
      <div ref={host} />
      <div className="ro-pchart__stats">
        <div><span className="ro-small">Ушло за {fact.length} дн.</span><b>{eur0(sum(fact))}</b></div>
        <div><span className="ro-small">Прогноз на {forecast.length} дн.</span><b>{eur0(sum(forecast))}</b></div>
        <div><span className="ro-small">Средний день, прогноз</span><b>{eur0(forecast.length ? sum(forecast) / forecast.length : 0)}</b></div>
      </div>
      <p className="ro-small">{timeline.summary} Для удалений из исходного снимка дата восстановлена по правилу «срок + 5 дней», остальные — из журнала переходов.</p>
    </section>
  )
}
