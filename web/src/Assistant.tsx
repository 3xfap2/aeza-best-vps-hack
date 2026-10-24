import { Fragment, useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { ApiError, api, can, type AIStatus, type AskAnswer, type Message } from './api'
import { useUser } from './pages/Shell'

/** «#62» в ответе → ссылка на карточку; email подставляет Радар, модель его не видела. */
export function linkify(text: string, customers: AskAnswer['customers'], className = 'ask__link'): ReactNode[] {
  const byId = new Map(customers.map((c) => [String(c.id), c.email]))
  return text.split(/(#\d+)/).map((part, i) => {
    const id = part.startsWith('#') ? part.slice(1) : null
    return id && byId.has(id)
      ? <Link key={i} className={className} to={`/app/customers/${id}`} title={byId.get(id)}>{part}</Link>
      : <Fragment key={i}>{part}</Fragment>
  })
}

type Intent = 'loss' | 'today' | 'customer' | 'accuracy' | 'changes'
interface Suggestion { label: string; intent?: Intent; letter?: boolean; ask?: string }
interface Turn { question: string; answer?: AskAnswer; letter?: Message | null; error?: string }

/** Подсказки зависят от экрана: на карточке — про этого клиента, в «Удержании» — план, в «Модели» — точность. */
function suggestions(pathname: string, ai: boolean): Suggestion[] {
  const id = pathname.match(/^\/app\/customers\/(\d+)/)?.[1]
  if (id) return [
    { label: `Почему клиент #${id} в риске и что делать?`, intent: 'customer' },
    { label: `Черновик письма клиенту #${id}`, letter: true },
    ...(ai ? [{ label: 'Что сказать ему по телефону?', ask: `Клиент #${id}: что сказать ему по телефону, чтобы он продлил?` }] : []),
  ]
  if (pathname.startsWith('/app/retention')) return [
    { label: 'Кому звонить сегодня?', intent: 'today' },
    { label: 'Сколько под угрозой и из чего складывается?', intent: 'loss' },
  ]
  if (pathname.startsWith('/app/model')) return [
    { label: 'Насколько точна модель и какой вес поднять?', intent: 'accuracy' },
    { label: 'Что менялось в данных и модели?', intent: 'changes' },
  ]
  return [
    { label: 'Сколько под угрозой и из чего складывается?', intent: 'loss' },
    { label: 'Кому звонить сегодня?', intent: 'today' },
    { label: 'Насколько точна модель?', intent: 'accuracy' },
  ]
}

/**
 * Плавающий помощник на всех экранах. Частые вопросы Радар отвечает сам — мгновенно и без ИИ;
 * свободный вопрос уходит в подключённую модель, а каждое число ответа сверяется с данными.
 * Диалог сохраняется при переходах между экранами; Esc закрывает.
 */
export default function Assistant() {
  const { pathname } = useLocation()
  const admin = can(useUser(), 'admin')
  const [open, setOpen] = useState(false)
  const [status, setStatus] = useState<AIStatus | null>(null)
  const [turns, setTurns] = useState<Turn[]>([])
  const [question, setQuestion] = useState('')
  const [busy, setBusy] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const fab = useRef<HTMLButtonElement>(null)
  const log = useRef<HTMLDivElement>(null)

  useEffect(() => { if (open) { api<AIStatus>('/api/v1/ai/status').then(setStatus).catch(() => setStatus(null)); input.current?.focus() } }, [open])
  useEffect(() => { log.current?.scrollTo({ top: log.current.scrollHeight, behavior: 'smooth' }) }, [turns])
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { setOpen(false); fab.current?.focus() } }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  if (pathname.startsWith('/app/ask')) return null   // на странице «Спросить Радар» — свой полный экран
  const ai = !!status?.enabled
  const customerId = Number(pathname.match(/^\/app\/customers\/(\d+)/)?.[1]) || undefined

  async function run(label: string, fetcher: () => Promise<Partial<Turn>>) {
    if (busy) return
    setBusy(true)
    setTurns((t) => [...t, { question: label }])
    try {
      const part = await fetcher()
      setTurns((t) => t.map((x, i) => i === t.length - 1 ? { ...x, ...part } : x))
    } catch (e) {
      const error = e instanceof ApiError ? e.message : 'Не удалось получить ответ'
      setTurns((t) => t.map((x, i) => i === t.length - 1 ? { ...x, error } : x))
    } finally { setBusy(false) }
  }
  const pick = (s: Suggestion) => {
    if (s.letter) return run(s.label, async () => ({ letter: await api<Message | null>(`/api/v1/customers/${customerId}/message${ai ? '?useAi=true' : ''}`) }))
    if (s.intent) return run(s.label, async () => ({ answer: await api<AskAnswer>('/api/v1/ai/quick', { method: 'POST', json: { intent: s.intent, customerId } }) }))
    return ask(s.ask ?? s.label)
  }
  function ask(text: string) {
    const q = text.trim()
    if (q.length < 3) return
    setQuestion('')
    if (!ai) {
      setTurns((t) => [...t, { question: q, error: 'Свободные вопросы отвечает ИИ, а он не подключён. Выберите быстрый вопрос выше — на него Радар ответит сам.' }])
      return
    }
    run(q, async () => ({ answer: await api<AskAnswer>('/api/v1/ai/ask', { method: 'POST', json: { question: q } }) }))
  }
  const submit = (e: FormEvent) => { e.preventDefault(); ask(question) }

  return (
    <>
      <button ref={fab} type="button" className={`asst-fab${open ? ' is-open' : ''}`} aria-expanded={open} aria-controls="asst"
        aria-label={open ? 'Свернуть помощника' : 'Спросить Радар'} onClick={() => setOpen(!open)}>
        <svg viewBox="0 0 24 24" aria-hidden="true">{open
          ? <path d="M6 9.5l6 6 6-6" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
          : <><circle cx="12" cy="12" r="8.2" fill="none" stroke="currentColor" strokeWidth="2" /><circle cx="12" cy="12" r="3.6" fill="none" stroke="currentColor" strokeWidth="2" />
            <path d="M12 12l5.2-5.2" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" /><circle cx="15.6" cy="9.3" r="1.6" fill="currentColor" /></>}</svg>
        {!open && <span className="asst-fab__dot" aria-hidden="true" />}
      </button>

      {open && (
        <section id="asst" className="ro asst" role="dialog" aria-modal="false" aria-labelledby="asst-t">
          <header className="asst__head">
            <span className="asst__avatar" aria-hidden="true">Р</span>
            <div>
              <h2 id="asst-t">Спросить Радар</h2>
              <p className="asst__status"><span className={`asst__led${ai ? ' is-on' : ''}`} />
                {ai ? `ИИ: ${status!.model} · числа сверяются с данными` : 'Ответы из расчёта, без ИИ'}</p>
            </div>
            <button type="button" className="asst__close" aria-label="Закрыть" onClick={() => { setOpen(false); fab.current?.focus() }}>×</button>
          </header>

          <div className="asst__log" ref={log} aria-live="polite">
            <div className="asst__bubble asst__bubble--bot">
              Спросите про деньги, клиентов или модель. На вопросы ниже Радар отвечает сам и сразу —
              {ai ? ' свободный вопрос разберёт ИИ.' : ' свободные вопросы заработают, когда администратор подключит ИИ.'}
            </div>
            {turns.map((t, i) => (
              <Fragment key={i}>
                <div className="asst__bubble asst__bubble--me">{t.question}</div>
                {!t.answer && t.letter === undefined && !t.error && <div className="asst__bubble asst__bubble--bot asst__typing" aria-label="Радар думает"><i /><i /><i /></div>}
                {t.error && <div className="asst__bubble asst__bubble--bot asst__bubble--warn">{t.error}
                  {admin && !ai && <> <Link className="ro-link" to="/app/ask">Подключить ИИ</Link></>}</div>}
                {t.answer && (
                  <div className="asst__bubble asst__bubble--bot">
                    <div className="asst__text">{linkify(t.answer.answer, t.answer.customers, 'asst__link')}</div>
                    <span className={`asst__tag${t.answer.grounded ? '' : ' is-warn'}`}>
                      {t.answer.provider === 'Радар, без ИИ' ? 'из расчёта Радара, без ИИ'
                        : t.answer.grounded ? 'ИИ · все числа сверены с данными' : `ИИ · не нашёл в данных: ${t.answer.unverified.join(', ')}`}</span>
                  </div>
                )}
                {t.letter !== undefined && (
                  <div className="asst__bubble asst__bubble--bot">
                    {t.letter ? <>
                      <b>{t.letter.subject}</b>
                      <div className="asst__text">{t.letter.text}</div>
                      <button type="button" className="asst__copy" onClick={() => navigator.clipboard?.writeText(`${t.letter!.subject}\n\n${t.letter!.text}`)}>Скопировать</button>
                      <span className="asst__tag">{t.letter.generatedBy === 'ai' ? 'написал ИИ — проверьте перед отправкой' : 'шаблон Радара по главной причине'}</span>
                    </> : 'Этому клиенту писать не о чем: риска нет.'}
                  </div>
                )}
              </Fragment>
            ))}
          </div>

          <div className="asst__chips" role="group" aria-label="Быстрые вопросы">
            {suggestions(pathname, ai).map((s) => <button key={s.label} type="button" disabled={busy} onClick={() => pick(s)}>{s.label}</button>)}
          </div>
          <form className="asst__form" onSubmit={submit}>
            <label className="ro-sr" htmlFor="asst-q">Вопрос Радару</label>
            <input ref={input} id="asst-q" value={question} maxLength={500} disabled={busy} autoComplete="off"
              placeholder={ai ? 'Спросите своими словами…' : 'Свободный вопрос — когда подключат ИИ'} onChange={(e) => setQuestion(e.target.value)} />
            <button type="submit" aria-label="Отправить" disabled={busy || question.trim().length < 3}>
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h13M13 6l6 6-6 6" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" /></svg>
            </button>
          </form>
        </section>
      )}
    </>
  )
}
