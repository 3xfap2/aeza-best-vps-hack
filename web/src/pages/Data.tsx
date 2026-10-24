import { useEffect, useRef, useState, type FormEvent } from 'react'
import { api, ApiError, type ActivityItem, type CustomerDetail, type LoadResult, type SnapshotInfo } from '../api'
import { useLiveData } from '../live'
import { dateTime, Empty, Skeleton, useToast } from '../ui'
import { StatusLine } from './Shell'

type EventType = 'topup' | 'pay_renewal' | 'autoprolong' | 'service_status' | 'signal'
const SIGNALS = [['loadDrop', 'Падение нагрузки сервера, 0–1'], ['downgraded', 'Понизил тариф'], ['supportTickets30d', 'Обращений в поддержку за 30 дней']]
const STATUSES = [['active', 'Активна'], ['suspended', 'Выключена'], ['prolong_wait', 'Ждёт оплаты'], ['deleted', 'Удалена'], ['blocked', 'Заблокирована']]

export default function DataPage() {
  const info = useLiveData(() => api<SnapshotInfo | null>('/api/v1/source/snapshot'))
  const activity = useLiveData(() => api<ActivityItem[]>('/api/v1/source/activity?limit=30'))
  const tg = useLiveData(() => api<{ enabled: boolean; botUsername: string | null; connectUrl: string | null }>('/api/v1/integrations/telegram'))
  const [busy, setBusy] = useState<string | null>(null)
  const toast = useToast()
  const fileRef = useRef<HTMLInputElement>(null)
  const [drag, setDrag] = useState(false)

  useEffect(() => { document.title = 'Данные — Радар оттока' }, [])

  async function run(label: string, fn: () => Promise<string>) {
    setBusy(label)
    try { toast.show(await fn()) } catch (e) { toast.show(e instanceof ApiError ? e.message : 'Не удалось выполнить', 'error') } finally { setBusy(null) }
  }

  async function upload(file: File) {
    await run('upload', async () => {
      let body: unknown
      try { body = JSON.parse(await file.text()) } catch { throw new ApiError(400, 'Файл не похож на JSON') }
      const r = await api<LoadResult>('/api/v1/source/snapshot', { method: 'PUT', json: body })
      return `Снимок загружен: ${r.accounts} аккаунтов, ${r.services} услуг; смен статуса: ${r.statusChanges}`
    })
  }

  const preset = (name: 'case' | 'extended') => run(name, async () => {
    const r = await api<LoadResult>(`/api/v1/source/preset/${name}`, { method: 'POST' })
    return `Загружено: ${r.accounts} аккаунтов, ${r.services} услуг`
  })
  const simulate = (days: number) => run(`sim${days}`, async () => {
    const r = await api<{ asOf: string; notes: string[] }>('/api/v1/source/simulate', { method: 'POST', json: { days } })
    return `Дата расчёта: ${dateTime(r.asOf)}; изменений: ${r.notes.length}`
  })
  const reset = () => run('reset', async () => {
    await api('/api/v1/source/reset', { method: 'POST' })
    return 'Данные возвращены к последнему снимку'
  })

  const s = info.data
  return (
    <>
      <div className="ro-top"><div><h1>Данные</h1><StatusLine /></div></div>

      <div className="data">
        <section className="ro-card" aria-labelledby="snap-t">
          <div className="ro-cardhead"><h2 id="snap-t">Снимок данных</h2></div>
          {info.loading && !s ? <Skeleton h={80} /> : s ? (
            <p className="ro-text">Загружен <b>{dateTime(s.loadedAt)}</b> ({s.loadedBy}): <span className="ro-mono">{s.counts.accounts}</span> аккаунтов,{' '}
              <span className="ro-mono">{s.counts.services}</span> услуг, <span className="ro-mono">{s.counts.transactions}</span> транзакций.
              {s.referenceAt && <> Снимок на {dateTime(s.referenceAt)}.</>}</p>
          ) : <p className="ro-text">Данные ещё не загружены.</p>}
          <div className="btns">
            <button className="ro-btn ro-btn--sm" type="button" disabled={!!busy} onClick={() => preset('extended')}>Расширенный набор (кейс + 190)</button>
            <button className="ro-btn ro-btn--quiet ro-btn--sm" type="button" disabled={!!busy} onClick={() => preset('case')}>Только данные кейса (10)</button>
          </div>
          <div className={`drop${drag ? ' is-drag' : ''}`}
            onDragOver={(e) => { e.preventDefault(); setDrag(true) }} onDragLeave={() => setDrag(false)}
            onDrop={(e) => { e.preventDefault(); setDrag(false); const f = e.dataTransfer.files[0]; if (f) upload(f) }}>
            <p>Перетащите сюда <span className="ro-mono">data.json</span> в формате кейса или</p>
            <button className="ro-btn ro-btn--quiet ro-btn--sm" type="button" disabled={!!busy} onClick={() => fileRef.current?.click()}>
              {busy === 'upload' ? 'Загружаем…' : 'Выберите файл'}</button>
            <input ref={fileRef} type="file" accept="application/json,.json" hidden
              onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); e.target.value = '' }} />
            <p className="ro-small">Полная замена через <span className="ro-code">PUT /api/v1/source/snapshot</span>; ошибки формата покажут конкретное поле.</p>
          </div>
        </section>

        <EventsCard busy={!!busy} run={run} />

        <section className="ro-card" aria-labelledby="time-t">
          <div className="ro-cardhead"><h2 id="time-t">Перемотка времени</h2></div>
          <p className="ro-small">Сдвигает дату расчёта и применяет правила биллинга из кейса: истёк срок — списание или «ждёт оплаты»; 5 дней без оплаты — удаление. Изменения помечаются в журнале как имитация.</p>
          <div className="btns">
            <button className="ro-btn ro-btn--sm" type="button" disabled={!!busy} onClick={() => simulate(1)}>+1 день</button>
            <button className="ro-btn ro-btn--sm" type="button" disabled={!!busy} onClick={() => simulate(5)}>+5 дней</button>
            <button className="ro-btn ro-btn--quiet ro-btn--sm" type="button" disabled={!!busy} onClick={reset}>Вернуть к дате снимка</button>
          </div>
        </section>

        <section className="ro-card" aria-labelledby="int-t">
          <div className="ro-cardhead"><h2 id="int-t">Интеграции</h2></div>
          <ul className="integrations">
            <li>
              <b>Telegram-бот</b>
              {tg.data?.connectUrl ? (
                <span>Присылает клиентов, перешедших в высокий риск; команды /top, /summary, /client.{' '}
                  <a className="ro-link" href={tg.data.connectUrl} target="_blank" rel="noreferrer">Подключить @{tg.data.botUsername}</a></span>
              ) : <span className="ro-small">{tg.data?.enabled ? 'Бот запускается…' : 'Не настроен: задайте TELEGRAM_BOT_TOKEN и TELEGRAM_LINK_CODE.'}</span>}
            </li>
            <li><b>Prometheus</b><span>Метрики на <a className="ro-link" href="/metrics" target="_blank" rel="noreferrer">/metrics</a>; сборщик в Docker Compose — на порту 9090.</span></li>
            <li><b>API</b><span>Документация — <a className="ro-link" href="/api/v1/docs" target="_blank" rel="noreferrer">/api/v1/docs</a>.</span></li>
          </ul>
        </section>

        <section className="ro-card data__log" aria-labelledby="log-t">
          <div className="ro-cardhead"><h2 id="log-t">Журнал</h2></div>
          {!activity.data ? <Skeleton h={160} /> : activity.data.length === 0 ? (
            <Empty>Пока ничего не происходило. Отправьте событие или перемотайте время.</Empty>
          ) : (
            <ul className="log">
              {activity.data.map((a) => (
                <li key={a.id}><span className="ro-small ro-mono">{dateTime(a.createdAt)} · {a.author}</span>{a.summary}</li>
              ))}
            </ul>
          )}
        </section>
      </div>
      {toast.node}
    </>
  )
}

function EventsCard({ busy, run }: { busy: boolean; run: (label: string, fn: () => Promise<string>) => Promise<void> }) {
  const [type, setType] = useState<EventType>('topup')
  const [customerId, setCustomerId] = useState('')
  const [customer, setCustomer] = useState<CustomerDetail | null>(null)
  const [serviceId, setServiceId] = useState('')
  const [amount, setAmount] = useState('20')
  const [enabled, setEnabled] = useState(true)
  const [status, setStatus] = useState('active')
  const [signal, setSignal] = useState('loadDrop')
  const [signalValue, setSignalValue] = useState('0.8')
  const [lookupError, setLookupError] = useState<string | null>(null)

  useEffect(() => {
    setCustomer(null); setServiceId(''); setLookupError(null)
    if (!/^\d+$/.test(customerId)) return
    const t = window.setTimeout(() => {
      api<CustomerDetail>(`/api/v1/customers/${customerId}`)
        .then((c) => { setCustomer(c); setServiceId(c.services[0] ? String(c.services[0].id) : '') })
        .catch((e: Error) => setLookupError(e.message))
    }, 300)
    return () => window.clearTimeout(t)
  }, [customerId])

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!customer) return
    const body: Record<string, unknown> = { type }
    if (type === 'topup') { body.accountId = customer.id; body.amount = Number(amount.replace(',', '.')) }
    else { body.serviceId = Number(serviceId) }
    if (type === 'autoprolong') body.enabled = enabled
    if (type === 'service_status') body.status = status
    if (type === 'signal') {
      body.name = signal
      body.targetId = signal === 'supportTickets30d' ? customer.id : Number(serviceId)
      body.value = signal === 'downgraded' ? true : Number(signalValue.replace(',', '.'))
      delete body.serviceId
    }
    await run('event', async () => {
      const before = customer.score
      const r = await api<{ summary: string }>('/api/v1/source/events', { method: 'POST', json: body })
      const after = await api<CustomerDetail>(`/api/v1/customers/${customer.id}`)
      setCustomer(after)
      return `${r.summary} · score ${before ?? '—'} → ${after.score ?? '—'}`
    })
  }

  return (
    <section className="ro-card" aria-labelledby="ev-t">
      <div className="ro-cardhead"><h2 id="ev-t">События биллинга</h2></div>
      <p className="ro-small">Имитация данных, которые биллинг присылает в реальном времени (<span className="ro-code">POST /api/v1/source/events</span>).</p>
      <form className="form ev" onSubmit={submit}>
        <label className="field"><span>ID клиента</span>
          <input inputMode="numeric" value={customerId} onChange={(e) => setCustomerId(e.target.value.trim())} placeholder="например, 102" /></label>
        {customer && <p className="ro-small ev__who">{customer.email} · score {customer.score ?? '—'} · {customer.levelTitle}</p>}
        {lookupError && <p className="form__error">{lookupError}</p>}
        <label className="field"><span>Событие</span>
          <select value={type} onChange={(e) => setType(e.target.value as EventType)}>
            <option value="topup">Пополнение баланса</option>
            <option value="pay_renewal">Оплата продления</option>
            <option value="autoprolong">Автопродление вкл/выкл</option>
            <option value="service_status">Смена статуса услуги</option>
            <option value="signal">Сигнал: поддержка / мониторинг / тариф</option>
          </select></label>
        {type === 'topup' ? (
          <label className="field"><span>Сумма, €</span><input inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} /></label>
        ) : (
          <label className="field"><span>Услуга</span>
            <select value={serviceId} onChange={(e) => setServiceId(e.target.value)} disabled={!customer}>
              {customer?.services.map((s) => <option key={s.id} value={s.id}>{s.name} · {s.statusTitle.toLowerCase()}{s.autoProlong ? ' · автопродление' : ''}</option>)}
            </select></label>
        )}
        {type === 'autoprolong' && (
          <label className="check"><input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />Включить автопродление</label>
        )}
        {type === 'signal' && (
          <>
            <label className="field"><span>Сигнал</span>
              <select value={signal} onChange={(e) => setSignal(e.target.value)}>
                {SIGNALS.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
              </select></label>
            {signal !== 'downgraded' && (
              <label className="field"><span>Значение</span><input inputMode="decimal" value={signalValue} onChange={(e) => setSignalValue(e.target.value)} /></label>
            )}
          </>
        )}
        {type === 'service_status' && (
          <label className="field"><span>Новый статус</span>
            <select value={status} onChange={(e) => setStatus(e.target.value)}>
              {STATUSES.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
            </select></label>
        )}
        <button className="ro-btn ro-btn--sm" type="submit" disabled={busy || !customer}>Отправить</button>
      </form>
    </section>
  )
}
