// Валюта отображения: данные и расчёты в евро (как в источнике), рубли — по курсу ЦБ РФ на сегодня.
// Дизайн-библиотека форматирует деньги сама и читает window.RADAR_FX.

export type Currency = 'EUR' | 'RUB'
export interface Fx { rate: number; date: string; source: string; stale: boolean }

declare global {
  interface Window { RADAR_FX?: { currency: Currency; rate: number | null } }
}

const state: { currency: Currency; fx: Fx | null } = {
  currency: (() => { try { return localStorage.getItem('radar.currency') === 'RUB' ? 'RUB' : 'EUR' } catch { return 'EUR' } })(),
  fx: null,
}

function sync() {
  window.RADAR_FX = { currency: isRub() ? 'RUB' : 'EUR', rate: state.fx?.rate ?? null }
}

export const currentCurrency = () => state.currency
export const currentFx = () => state.fx
export const isRub = () => state.currency === 'RUB' && !!state.fx
export const sign = () => (isRub() ? '₽' : '€')

export function setFx(fx: Fx | null) { state.fx = fx; sync() }
export function setCurrency(c: Currency) {
  state.currency = c
  try { localStorage.setItem('radar.currency', c) } catch { /* приватный режим */ }
  sync()
}

const grouped = (v: number, digits: number) =>
  new Intl.NumberFormat('ru-RU', { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(v)

/** Сумма в евро → строка в выбранной валюте. В рублях копейки — только у сумм меньше 100 ₽. */
export function money(eurValue: number, cents = true): string {
  if (isRub()) {
    const rub = eurValue * state.fx!.rate
    return grouped(rub, cents && Math.abs(rub) < 100 ? 2 : 0) + ' ₽'
  }
  return '€ ' + grouped(eurValue, cents ? 2 : 0)
}
