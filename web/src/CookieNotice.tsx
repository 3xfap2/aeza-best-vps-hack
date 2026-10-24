import { useState } from 'react'

// Уведомление о cookie. У Радара одна cookie — сессия после входа (строго необходимая), аналитики и рекламы нет,
// поэтому достаточно уведомить; если появятся необязательные cookie — здесь нужен будет выбор с согласием.
const KEY = 'radar.cookies'

export default function CookieNotice() {
  const [shown, setShown] = useState(() => { try { return localStorage.getItem(KEY) !== 'ok' } catch { return true } })
  if (!shown) return null
  const ok = () => { setShown(false); try { localStorage.setItem(KEY, 'ok') } catch { /* приватный режим — покажем снова */ } }
  return (
    <div className="ro cookie" role="region" aria-label="Уведомление о cookie">
      <p>Мы используем одну cookie — чтобы вы оставались в панели после входа. Аналитики и рекламы нет.
        Подробнее — в <a className="ro-link" href="/privacy">политике обработки персональных данных</a>.</p>
      <button className="ro-btn ro-btn--sm" type="button" onClick={ok}>Понятно</button>
    </div>
  )
}
