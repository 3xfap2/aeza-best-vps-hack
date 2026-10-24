import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
// Порядок важен: библиотека дизайна читает window.OTTOK_DATA при загрузке.
import './design/landing-data.js'
import './design/ottok.js'
import './design/ottok.css'
import './app.css'
import App from './App'
import { applyTheme, currentTheme } from './theme'

// После обновления стенда у открытой вкладки старые имена файлов экранов — перезагружаем страницу один раз
// (не чаще раза в минуту, чтобы не зациклиться, если сервер недоступен).
window.addEventListener('vite:preloadError', (e) => {
  try {
    const last = Number(sessionStorage.getItem('radar.reloadAt') || 0)
    if (Date.now() - last < 60_000) return
    sessionStorage.setItem('radar.reloadAt', String(Date.now()))
  } catch { /* без хранилища — просто перезагрузим */ }
  e.preventDefault()
  window.location.reload()
})

applyTheme(currentTheme())   // до первой отрисовки — без мигания цветов

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
