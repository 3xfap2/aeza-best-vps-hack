import { useRef, useState, type FormEvent } from 'react'
import { useLocation } from 'react-router-dom'
import { ApiError, api, type FeedbackItem } from './api'
import { Icon } from './ui'

const KINDS: [FeedbackItem['kind'], string][] = [['idea', 'Идея'], ['problem', 'Проблема'], ['other', 'Другое']]

/** Кнопка «Обратная связь»: сотрудник пишет, что неудобно или чего не хватает; отзывы видит администратор в «Команде». */
export default function FeedbackButton() {
  const ref = useRef<HTMLDialogElement>(null)
  const { pathname } = useLocation()
  const [kind, setKind] = useState<FeedbackItem['kind']>('idea')
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [sent, setSent] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function open() { setSent(false); setError(null); ref.current?.showModal() }
  async function submit(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError(null)
    try {
      await api('/api/v1/feedback', { method: 'POST', json: { kind, text, page: pathname } })
      setSent(true); setText('')
    } catch (err) { setError(err instanceof ApiError ? err.message : 'Не удалось отправить') } finally { setBusy(false) }
  }

  return (
    <>
      <button type="button" onClick={open}><Icon name="pen" />Обратная связь</button>
      <dialog ref={ref} className="ro feedback" aria-labelledby="fb-t">
        <h2 id="fb-t">Обратная связь</h2>
        {sent ? <>
          <p>Спасибо! Отзыв получен — администратор увидит его в разделе «Команда».</p>
          <div className="feedback__btns"><button className="ro-btn ro-btn--sm" type="button" onClick={() => ref.current?.close()}>Закрыть</button></div>
        </> : (
          <form className="form" onSubmit={submit}>
            <p className="ro-small">Что неудобно, чего не хватает, где ошибка. Прочитает администратор Радара; экран, с которого вы пишете, приложится сам.</p>
            <div className="chips" role="group" aria-label="Тип отзыва">
              {KINDS.map(([k, t]) => <button key={k} type="button" aria-pressed={kind === k} onClick={() => setKind(k)}>{t}</button>)}
            </div>
            <textarea className="message__text" rows={5} required minLength={3} maxLength={2000} value={text}
              onChange={(e) => setText(e.target.value)} aria-label="Текст отзыва" placeholder="Например: хочу видеть в очереди телефон клиента" />
            {error && <p className="form__error" role="alert">{error}</p>}
            <div className="feedback__btns">
              <button className="ro-btn ro-btn--sm ro-btn--quiet" type="button" onClick={() => ref.current?.close()}>Отмена</button>
              <button className="ro-btn ro-btn--sm" type="submit" disabled={busy || text.trim().length < 3}>{busy ? 'Отправляем…' : 'Отправить'}</button>
            </div>
          </form>
        )}
      </dialog>
    </>
  )
}
