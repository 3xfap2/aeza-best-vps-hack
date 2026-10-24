import { useEffect, useState, type FormEvent } from 'react'
import { api, ApiError, type AIStatus } from './api'

// Подключение OpenAI-совместимого ИИ из панели — на экранах «Данные» и «Спросить Радар».

interface AIConfig { source: 'panel' | 'env' | 'off'; baseUrl: string | null; model: string | null; keyHint: string | null; updatedBy?: string | null }

/** Подключение OpenAI-совместимого ИИ: администратор вводит адрес, модель и ключ — проверка запросом, затем сохранение. */
export default function AIConfigCard({ admin, onConnected }: { admin: boolean; onConnected?: () => void }) {
  const [cfg, setCfg] = useState<AIConfig | null>(null)
  const [status, setStatus] = useState<AIStatus | null>(null)
  const [form, setForm] = useState({ baseUrl: 'https://api.openai.com/v1', model: 'gpt-4.1-mini', apiKey: '' })
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null)
  const load = () => {
    api<AIStatus>('/api/v1/ai/status').then(setStatus).catch(() => setStatus(null))
    if (admin) api<AIConfig>('/api/v1/ai/config').then((c) => {
      setCfg(c)
      if (c.source === 'panel' && c.baseUrl && c.model) setForm((f) => ({ ...f, baseUrl: c.baseUrl!, model: c.model! }))
    }).catch(() => setCfg(null))
  }
  useEffect(load, [admin]) // eslint-disable-line react-hooks/exhaustive-deps

  async function save(e: FormEvent) {
    e.preventDefault(); setBusy(true); setMsg(null)
    try {
      const s = await api<AIStatus>('/api/v1/ai/config', { method: 'PUT', json: { ...form, apiKey: form.apiKey || null } })
      setMsg({ text: `Подключено: ${s.provider}, модель ${s.model}. Пробный запрос прошёл.`, ok: true })
      setForm((f) => ({ ...f, apiKey: '' })); load(); onConnected?.()
    } catch (err) { setMsg({ text: err instanceof ApiError ? err.message : 'Не удалось подключить', ok: false }) } finally { setBusy(false) }
  }
  async function off() {
    setBusy(true)
    try { await api('/api/v1/ai/config', { method: 'DELETE' }); setMsg({ text: 'ИИ из панели отключён.', ok: true }); load() } finally { setBusy(false) }
  }
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value })

  return (
    <section className="ro-card ai-card" aria-labelledby="ai-t">
      <div className="ro-cardhead"><h2 id="ai-t">ИИ-помощник</h2>
        <span className={`work-tag ${status?.enabled ? 'work-tag--renewed' : ''}`}>{status?.enabled ? `подключён: ${status.provider}` : 'не подключён'}</span></div>
      <p className="ro-small">Любой OpenAI-совместимый сервис: OpenAI, ИИ Aéza, OpenRouter, локальная Ollama. Работает в «Спросить Радар», письмах и боте.
        Модели уходят суммы и ID клиентов, email — никогда.</p>
      {!admin ? <p className="ro-small">Подключает администратор.</p> : (
        <form className="form" onSubmit={save}>
          <label className="field"><span>Адрес API (base URL)</span><input value={form.baseUrl} onChange={set('baseUrl')} required placeholder="https://api.openai.com/v1" /></label>
          <label className="field"><span>Модель</span><input value={form.model} onChange={set('model')} required placeholder="gpt-4.1-mini" /></label>
          <label className="field"><span>Ключ API {cfg?.keyHint ? `(сохранён: …${cfg.keyHint} — оставьте пустым, чтобы не менять)` : ''}</span>
            <input type="password" value={form.apiKey} onChange={set('apiKey')} autoComplete="off" placeholder="sk-…" required={!cfg?.keyHint || cfg.source !== 'panel'} /></label>
          {msg && <p className={msg.ok ? 'ro-small work__ok' : 'form__error'} role={msg.ok ? 'status' : 'alert'}>{msg.text}</p>}
          <div className="btns">
            <button className="ro-btn ro-btn--sm" type="submit" disabled={busy}>{busy ? 'Проверяю…' : 'Проверить и сохранить'}</button>
            {cfg?.source === 'panel' && <button className="ro-btn ro-btn--sm ro-btn--quiet" type="button" disabled={busy} onClick={off}>Отключить</button>}
          </div>
          <p className="ro-small">Ключ хранится на сервере и в браузер не возвращается. Сохраняется, только если пробный запрос к модели прошёл.</p>
        </form>
      )}
    </section>
  )
}
