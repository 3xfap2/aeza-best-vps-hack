import { useState } from 'react'
import { applyTheme, currentTheme, THEMES, type Theme } from './theme'

/** Переключатель оформления: наш дизайн или фирменные цвета Aéza. floating — плавающая плашка для лендинга. */
export default function ThemeSwitch({ floating = false }: { floating?: boolean }) {
  const [theme, setTheme] = useState<Theme>(currentTheme)
  const pick = (t: Theme) => { applyTheme(t); setTheme(t) }
  return (
    <div className={floating ? 'theme-float' : 'theme-box'}>
      <span className="theme-switch__label" id={floating ? 'theme-l-f' : 'theme-l'}>Оформление</span>
      <div className="theme-switch" role="group" aria-labelledby={floating ? 'theme-l-f' : 'theme-l'}>
        {THEMES.map((t) => (
          <button key={t.key} type="button" aria-pressed={theme === t.key} onClick={() => pick(t.key)}>{t.title}</button>
        ))}
      </div>
    </div>
  )
}
