import { Fragment, useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { ApiError, api, type AIStatus, type AskAnswer } from '../api'
import { Icon } from '../ui'
import { StatusLine } from './Shell'

const EXAMPLES = [
  'Кому звонить первым и что сказать?',
  'За что мы теряем больше всего денег?',
  'Почему клиент #5 в высоком риске и что будет, если он пополнит счёт?',
  'Что изменилось в данных и модели за последнее время?',
]

interface Turn { question: string; answer?: AskAnswer; error?: string }

/** «#62» в ответе → ссылка на карточку; email подставляет Радар, модель его не видела. */
function linkify(text: string, customers: AskAnswer['customers']): ReactNode[] {
  const byId = new Map(customers.map((c) => [String(c.id), c.email]))
  return text.split(/(#\d+)/).map((part, i) => {
    const id = part.startsWith('#') ? part.slice(1) : null
    return id && byId.has(id)
      ? <Link key={i} className="ask__link" to={`/app/customers/${id}`} title={byId.get(id)}>{part}</Link>
      : <Fragment key={i}>{part}</Fragment>
  })
}

export default function Ask() {
  const [status, setStatus] = useState<AIStatus | null>(null)
  const [question, setQuestion] = useState('')
  const [turns, setTurns] = useState<Turn[]>([])
  const [busy, setBusy] = useState(false)
  useEffect(() => { document.title = 'Спросить Радар — Радар оттока' }, [])
  useEffect(() => { api<AIStatus>('/api/v1/ai/status').then(setStatus).catch(() => setStatus(null)) }, [])

  async function ask(q: string) {
    const text = q.trim()
    if (text.length < 3 || busy) return
    setBusy(true); setQuestion('')
    setTurns((t) => [{ question: text }, ...t])
    try {
      const answer = await api<AskAnswer>('/api/v1/ai/ask', { method: 'POST', json: { question: text } })
      setTurns((t) => t.map((x, i) => i === 0 ? { ...x, answer } : x))
    } catch (e) {
      setTurns((t) => t.map((x, i) => i === 0 ? { ...x, error: e instanceof ApiError ? e.message : 'Не удалось получить ответ' } : x))
    } finally { setBusy(false) }
  }
  const submit = (e: FormEvent) => { e.preventDefault(); ask(question) }

  return (
    <>
      <div className="ro-top"><div><h1>Спросить Радар</h1><StatusLine /></div></div>
      <div className="ro-grid">
        <section className="ro-panel ask" aria-labelledby="ask-t">
          <h2 id="ask-t" className="ask__title">Вопрос простыми словами — ответ по данным Радара</h2>
          {status && !status.enabled ? (
            <div className="ask__off">
              <p className="ro-text">ИИ-помощник не подключён. Чтобы включить, задайте в <code>.env</code> провайдера и ключ:
                <code className="formula">AI_PROVIDER=openai · AI_API_KEY=sk-…</code>
                или <code>AI_PROVIDER=yandex</code> / <code>gigachat</code> — тогда данные обрабатываются в РФ.</p>
              <p className="ro-small">Без ИИ всё остальное работает: письма — по шаблонам, план дня — в «Удержании» и в боте (/today).</p>
            </div>
          ) : (
            <>
              <form className="ask__form" onSubmit={submit}>
                <label className="ro-sr" htmlFor="ask-q">Вопрос</label>
                <input id="ask-q" className="ask__input" value={question} maxLength={500} disabled={busy}
                  placeholder="Например: кому звонить первым и что сказать?" onChange={(e) => setQuestion(e.target.value)} />
                <button className="ro-btn" type="submit" disabled={busy || question.trim().length < 3}>
                  {busy ? 'Думаю…' : 'Спросить'}<Icon name="arrow" /></button>
              </form>
              <div className="chips" role="group" aria-label="Примеры вопросов">
                {EXAMPLES.map((q) => <button key={q} type="button" disabled={busy} onClick={() => ask(q)}>{q}</button>)}
              </div>
            </>
          )}
          <p className="ro-small ask__how">
            {status?.enabled ? `${status.provider} · ` : ''}ИИ ничего не считает и не меняет: суммы, сроки и причины Радар берёт из того же расчёта,
            что панель, а каждое число ответа сверяет с данными. Email клиентов в модель не уходят — она видит «клиент #62».
          </p>
        </section>

        {turns.map((t, i) => (
          <section key={turns.length - i} className="ro-card ask__turn" aria-live={i === 0 ? 'polite' : undefined}>
            <p className="ask__q"><Icon name="chat" />{t.question}</p>
            {!t.answer && !t.error && <p className="ro-small">Собираю факты и спрашиваю модель…</p>}
            {t.error && <p className="form__error" role="alert">{t.error}</p>}
            {t.answer && (
              <>
                <div className="ask__a">{linkify(t.answer.answer, t.answer.customers)}</div>
                {t.answer.grounded
                  ? <span className="work-tag work-tag--renewed">все числа сверены с данными</span>
                  : <span className="work-tag work-tag--churned">не нашёл в данных: {t.answer.unverified.join(', ')} — перепроверьте</span>}
                {t.answer.customers.length > 0 && (
                  <p className="ro-small">Клиенты из ответа: {t.answer.customers.map((c, k) => (
                    <Fragment key={c.id}>{k > 0 && ', '}<Link className="ro-link" to={`/app/customers/${c.id}`}>#{c.id} {c.email}</Link></Fragment>
                  ))}</p>
                )}
              </>
            )}
          </section>
        ))}
      </div>
    </>
  )
}
