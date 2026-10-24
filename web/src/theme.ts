// Оформление: «Радар» — наш дизайн, «Aéza» — фирменные цвета хостинга (aeza.net).
// Тема — набор токенов цвета и шрифтов на <html data-theme>, компоненты не меняются.

export type Theme = 'radar' | 'aeza'
export const THEMES: { key: Theme; title: string }[] = [{ key: 'radar', title: 'Радар' }, { key: 'aeza', title: 'Aéza' }]
const META: Record<Theme, string> = { radar: '#FFFBF8', aeza: '#F4F4F4' }

export function currentTheme(): Theme {
  try { return localStorage.getItem('radar.theme') === 'aeza' ? 'aeza' : 'radar' } catch { return 'radar' }
}

export function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme === 'aeza' ? 'aeza' : 'light'
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', META[theme])
  try { localStorage.setItem('radar.theme', theme) } catch { /* приватный режим — тема до перезагрузки */ }
}
