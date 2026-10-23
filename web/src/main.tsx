import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
// Порядок важен: библиотека дизайна читает window.OTTOK_DATA при загрузке.
import './design/landing-data.js'
import './design/ottok.js'
import './design/ottok.css'
import './app.css'
import App from './App'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
