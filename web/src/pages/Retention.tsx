import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ACTION_TITLE, ApiError, api, can, type Message, type Queue, type QueueItem } from '../api'
import { useLiveData } from '../live'
import { Empty, ErrorBox, eur, eur0, Skeleton, shortDate, useToast } from '../ui'
import { StatusLine, useUser } from './Shell'

type Bucket = QueueItem['bucket'] | 'all'
type Status = QueueItem['status'] | 'all'

const BUCKETS: [Bucket, string][] = [['today', 'Сегодня'], ['week', 'На неделе'], ['later', 'Позже'], ['all', 'Все']]
const STATUSES: [Status, string][] = [['all', 'Все'], ['new', 'Не начато'], ['in_progress', 'В работе'], ['done', 'Готово']]
const LEVEL_SHORT = { high: 'Высокий', medium: 'Средний', low: 'Низкий', no_score: 'Нет оценки', churned: 'Ушёл' } as const

function loadPrefs() {
  try { return { rate: 30, cost: 3, ...JSON.parse(localStorage.getItem('radar.retention') ?? '{}') } } catch { return { rate: 30, cost: 3 } }
}

/** Срок человеческими словами: когда деньги будут потеряны. */
function deadlineText(x: QueueItem) {
  const hours = Math.round(x.daysLeft * 24)
  const when = x.daysLeft < 1 ? `через ${Math.max(1, hours)} ч` : `через ${Math.round(x.daysLeft)} дн.`
  if (x.deadlineKind === 'overdue') return { title: 'удаление задержано', sub: 'данные ещё на сервере', tone: 'high' }
  if (x.deadlineKind === 'deletion') return { title: `удалится ${when}`, sub: shortDate(x.deadline) + ' вместе с данными', tone: 'high' }
  if (x.daysLeft === 0) return { title: 'срок истёк', sub: 'продление не прошло', tone: 'mid' }
  return { title: `продление ${when}`, sub: shortDate(x.deadline), tone: x.daysLeft <= 7 ? 'mid' : 'low' }
}

export default function Retention() {
  const user = useUser()
  const [prefs, setPrefs] = useState(loadPrefs)
  const [bucket, setBucket] = useState<Bucket | null>(null)
  const [status, setStatus] = useState<Status>('all')
  const [tick, setTick] = useState(0)
  const [busy, setBusy] = useState<number | null>(null)
  const toast = useToast()
  const qs = `saveRate=${prefs.rate}&callCost=${prefs.cost}`
  const { data, error, loading } = useLiveData(() => api<Queue>(`/api/v1/retention/queue?${qs}`), [qs, tick])

  useEffect(() => { document.title = 'Удержание — Радар оттока' }, [])
  useEffect(() => { try { localStorage.setItem('radar.retention', JSON.stringify(prefs)) } catch { /* приватный режим */ } }, [prefs])
  // по умолчанию — самый срочный непустой срок
  const active: Bucket = bucket ?? (data ? (['today', 'week', 'later'] as const).find((b) => data.summary.buckets[b] > 0) ?? 'all' : 'today')

  async function mark(x: QueueItem, kind: string) {
    setBusy(x.customerId)
    try {
      await api(`/api/v1/customers/${x.customerId}/actions`, { method: 'POST', json: { kind } })
      toast.show(`${x.email}: ${ACTION_TITLE[kind].toLowerCase()}`)
      setTick((t) => t + 1)
    } catch (e) { toast.show(e instanceof ApiError ? e.message : 'Не удалось сохранить', 'error') } finally { setBusy(null) }
  }

  async function copyMessage(x: QueueItem) {
    try {
      const m = await api<Message | null>(`/api/v1/customers/${x.customerId}/message`)
      if (!m) return toast.show('Для клиента нет черновика', 'error')
      await navigator.clipboard.writeText(`Кому: ${m.email}\nТема: ${m.subject}\n\n${m.text}`)
      toast.show('Письмо скопировано — вставьте в почту и отметьте «Написали»')
    } catch { toast.show('Не удалось скопировать письмо', 'error') }
  }

  if (error && !data) return <ErrorBox message={error} onRetry={() => setTick((t) => t + 1)} />
  const s = data?.summary
  const items = (data?.items ?? []).filter((x) => (active === 'all' || x.bucket === active) && (status === 'all' || x.status === status))
  const editable = can(user, 'marketer')

  return (
    <>
      <div className="ro-top">
        <div>
          <h1>Удержание</h1>
          <StatusLine asOf={data?.asOf} />
        </div>
        <div className="ro-top__actions">
          <a className="ro-btn ro-btn--quiet ro-btn--sm" href={`/api/v1/retention/queue.csv?${qs}&includeEmail=true`}>CSV для обзвона</a>
        </div>
      </div>

      {!s ? <div className="ro-grid"><div className="span12"><Skeleton h={220} /></div><div className="span12"><Skeleton h={360} /></div></div> : (
        <div className="ro-grid" aria-busy={loading}>
          <section className="ro-panel camp" aria-labelledby="camp-t">
            <div className="camp__main">
              <span className="ro-label" id="camp-t">Если отработать очередь</span>
              <div className="ro-sum__amount"><span className="ro-mark" data-draw="done">{eur(s.net)}</span></div>
              <p className="ro-text">
                Сохраним <b className="ro-mono">{eur(s.expectedSaved)}</b> за {data!.horizonDays} дней
                минус <b className="ro-mono">{eur(s.callsCost)}</b> на {s.calls} звонков. Остальным {s.emails} клиентам
                хватит письма: звонок им не окупится.
              </p>
              <p className="ro-small">
                Деньги — реально собираемая выручка: не больше, чем клиент платит в среднем или держит на счёте
                (по прайсу было бы {eur0(s.expectedLoss)}, реально {eur0(s.expectedLossPaid)}).
              </p>
            </div>
            <div className="camp__ctl">
              <label className="field" htmlFor="rate"><span>Удаётся удержать — <b className="ro-mono">{prefs.rate} %</b></span></label>
              <input id="rate" className="ro-range" type="range" min={5} max={80} step={5} value={prefs.rate}
                style={{ ['--p' as string]: `${(prefs.rate - 5) / 75 * 100}%` }}
                onChange={(e) => setPrefs({ ...prefs, rate: Number(e.target.value) })} />
              <label className="field"><span>Стоимость звонка (время менеджера), €</span>
                <input className="num-input" type="number" min={0} max={100} step={0.5} value={prefs.cost}
                  onChange={(e) => setPrefs({ ...prefs, cost: Math.max(0, Math.min(100, Number(e.target.value) || 0)) })} /></label>
              <p className="ro-small">Ориентир: напоминания об оплате возвращают 20–50 % неуспешных платежей; звонок ≈ 10 минут менеджера.</p>
            </div>
            <dl className="camp__fact">
              <div><dt>Клиентов в очереди</dt><dd>{s.customers}</dd></div>
              <div><dt>Отработано</dt><dd>{s.worked} из {s.customers}</dd></div>
              <div><dt>Продлили / ушли</dt><dd>{s.renewed} / {s.churned}</dd></div>
              <div><dt>Сохранено фактически</dt><dd className="camp__saved">{eur(s.savedFact)}</dd></div>
            </dl>
          </section>

          <section className="ro-card ro-top5 queue" aria-labelledby="q-t">
            <div className="ro-cardhead"><h2 id="q-t">Кому и до какого срока</h2>
              <span className="ro-small">сначала то, что удалится раньше; внутри срока — по выгоде контакта</span></div>
            <div className="queue__filters">
              <div className="chips" role="group" aria-label="Срок">
                {BUCKETS.map(([k, t]) => (
                  <button key={k} type="button" aria-pressed={active === k} onClick={() => setBucket(k)}>
                    {t} <span className="ro-mono">{k === 'all' ? s.customers : s.buckets[k]}</span></button>
                ))}
              </div>
              <div className="chips" role="group" aria-label="Статус работы">
                {STATUSES.map(([k, t]) => <button key={k} type="button" aria-pressed={status === k} onClick={() => setStatus(k)}>{t}</button>)}
              </div>
            </div>
            {items.length === 0 ? <div className="queue__empty"><Empty>{active === 'today' ? 'На сегодня срочных клиентов нет.' : 'Никого с такими условиями.'}</Empty></div> : (
              <div className="table-scroll">
                <table className="ro-table queue__table">
                  <thead><tr><th>Срок</th><th>Клиент</th><th>Причина и что сделать</th><th className="r">Сохраним</th>
                    <th>Канал</th><th>Работа</th></tr></thead>
                  <tbody>
                    {items.map((x) => {
                      const d = deadlineText(x)
                      return (
                        <tr key={x.customerId} className={x.status === 'done' ? 'is-done' : undefined}>
                          <td><span className={`ro-risk ro-risk--${d.tone}`}>{d.title}</span><div className="ro-small">{d.sub}</div></td>
                          <td><Link className="row-link ro-mono" to={`/app/customers/${x.customerId}`}>{x.email}</Link>
                            <div className="ro-small">ID {x.customerId} · {LEVEL_SHORT[x.level].toLowerCase()} риск, {x.score}</div></td>
                          <td className="queue__why"><b>{x.causeTitle}</b><div className="ro-small">{x.action}</div></td>
                          <td className="r mono queue__money">{eur(x.expectedSaved)}
                            <div className="ro-small">из {eur0(x.expectedLossPaid)}</div>
                            {x.expectedLossPaid < x.expectedLoss - 0.5 && <div className="ro-small" title="Потеря по прайсу услуг">по прайсу {eur0(x.expectedLoss)}</div>}</td>
                          <td><span className={`work-tag queue__ch--${x.channel}`}>{x.channel === 'call' ? 'звонок' : 'письмо'}</span>
                            {x.channel === 'call' && <div className="ro-small">выгода {eur(x.net)}</div>}</td>
                          <td><div className="queue__act">
                            {x.lastAction && <div><span className={`work-tag work-tag--${x.lastAction}`}>{ACTION_TITLE[x.lastAction]}</span>
                              <span className="ro-small"> {x.lastAuthor} · {shortDate(x.lastActionAt!)}</span></div>}
                            {editable && x.status !== 'done' && (x.channel === 'call' ? <>
                              <button type="button" className="ro-btn ro-btn--sm ro-btn--quiet" disabled={busy === x.customerId} onClick={() => mark(x, 'called')}>Позвонили</button>
                              <button type="button" className="ro-btn ro-btn--sm ro-btn--quiet" disabled={busy === x.customerId} onClick={() => mark(x, 'no_answer')}>Не дозвонились</button>
                            </> : <>
                              <button type="button" className="ro-btn ro-btn--sm ro-btn--quiet" onClick={() => copyMessage(x)}>Скопировать письмо</button>
                              <button type="button" className="ro-btn ro-btn--sm ro-btn--quiet" disabled={busy === x.customerId} onClick={() => mark(x, 'messaged')}>Написали</button>
                            </>)}
                            {editable && x.status !== 'done' && x.status !== 'new' &&
                              <button type="button" className="ro-btn ro-btn--sm" disabled={busy === x.customerId} onClick={() => mark(x, 'renewed')}>Продлил</button>}
                            {(!editable || x.status === 'done') && <Link className="ro-link" to={`/app/customers/${x.customerId}`}>Карточка</Link>}
                          </div></td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
            {!editable && <p className="ro-small queue__ro">Роль «Наблюдатель»: отмечать работу с клиентами может маркетолог или администратор.</p>}
          </section>
        </div>
      )}
      {toast.node}
    </>
  )
}
