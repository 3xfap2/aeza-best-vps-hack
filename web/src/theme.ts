// Оформление: «Радар» — наш дизайн, «Aéza» — фирменные цвета хостинга (aeza.net),
// «Мягкая» — тёплая, округлая и дружелюбная. Тема — набор токенов цвета и шрифтов на <html data-theme>,
// компоненты не меняются.

export type Theme = 'radar' | 'aeza' | 'soft'
export const THEMES: { key: Theme; title: string }[] = [
  { key: 'radar', title: 'Радар' }, { key: 'aeza', title: 'Aéza' }, { key: 'soft', title: 'Мягкая' },
]
const META: Record<Theme, string> = { radar: '#FFFBF8', aeza: '#F4F4F4', soft: '#FFF8F3' }
const ATTR: Record<Theme, string> = { radar: 'light', aeza: 'aeza', soft: 'soft' }

export function currentTheme(): Theme {
  try {
    const saved = localStorage.getItem('radar.theme')
    return saved === 'aeza' || saved === 'soft' ? saved : 'radar'
  } catch { return 'radar' }
}

export function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = ATTR[theme]
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', META[theme])
  try { localStorage.setItem('radar.theme', theme) } catch { /* приватный режим — тема до перезагрузки */ }
}
