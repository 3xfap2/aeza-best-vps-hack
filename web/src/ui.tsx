import { useEffect, useRef, useState, type ReactNode } from 'react'
import type { Level } from './api'
import { money } from './currency'

declare global {
  interface Window {
    Ottok: {
      icons: Record<string, string>
      wordmark: string
      Radar: { mount: (host: HTMLElement, o: Record<string, unknown>) => { setFilter: (lv: string) => void; destroy: () => void } }
      ForecastChart: { mount: (host: HTMLElement, o: Record<string, unknown>) => { destroy: () => void } }
      LandingA: { mount: (root: HTMLElement) => { destroy: () => void } }
      revealAll: (root: HTMLElement) => void
    }
  }
}

// Деньги в API — в евро; показываем в выбранной валюте (€ или ₽ по курсу ЦБ), см. currency.ts
export const eur = (v: number) => money(v, true)
export const eur0 = (v: number) => money(v, false)
export const dateTime = (iso: string) => new Date(iso).toLocaleString('ru-RU', {
  day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: 'UTC',
}) + ' UTC'
export const shortDate = (iso: string) => new Date(iso).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', timeZone: 'UTC' })

/** Уровень API → класс оформления из дизайн-системы. */
const RISK_CLASS: Record<Level, string> = { high: 'high', medium: 'mid', low: 'low', no_score: 'none', churned: 'none' }
export const radarLevel = (l: Level) => ({ high: 'high', medium: 'mid', low: 'low', no_score: 'none', churned: 'gone' })[l]

export function RiskBadge({ level, title }: { level: Level; title: string }) {
  return <span className={`ro-risk ro-risk--${RISK_CLASS[level]}`}>{title.toLowerCase()}</span>
}

export function ScoreBar({ score }: { score: number | null }) {
  if (score == null) return <span className="ro-small">—</span>
  const lv = score >= 60 ? 'high' : score >= 30 ? 'mid' : 'low'
  return <span className="ro-score" data-level={lv}><i />{score}</span>
}

export function Icon({ name, size }: { name: string; size?: number }) {
  const svg = window.Ottok.icons[name] ?? ''
  return <span className="ico" style={size ? { width: size, height: size } : undefined}
    dangerouslySetInnerHTML={{ __html: svg }} aria-hidden="true" />
}

export function Wordmark() {
  return <span className="ro-wordmark"><span dangerouslySetInnerHTML={{ __html: window.Ottok.wordmark }} /><span>Радар оттока</span></span>
}

export function ErrorBox({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="ro-card state state--error" role="alert">
      <b>Не удалось загрузить данные</b>
      <span className="ro-small">{message}</span>
      {onRetry && <button className="ro-btn ro-btn--sm ro-btn--quiet" onClick={onRetry}>Повторить</button>}
    </div>
  )
}

export function Skeleton({ h = 120 }: { h?: number }) {
  return <div className="skeleton" style={{ height: h }} aria-hidden="true" />
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="state state--empty">{children}</div>
}

/** Короткое уведомление внизу экрана. */
export function useToast() {
  const [toast, setToast] = useState<{ text: string; kind: 'ok' | 'error' } | null>(null)
  const timer = useRef<number>()
  const show = (text: string, kind: 'ok' | 'error' = 'ok') => {
    setToast({ text, kind })
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setToast(null), 4000)
  }
  useEffect(() => () => window.clearTimeout(timer.current), [])
  const node = toast ? <div className={`toast toast--${toast.kind}`} role={toast.kind === 'error' ? 'alert' : 'status'}>{toast.text}</div> : null
  return { show, node }
}

/** Подсветка строк, у которых изменилось значение после пересчёта. */
export function useChanged<T extends { id: number }>(items: T[] | undefined, key: (x: T) => unknown) {
  const prev = useRef<Map<number, unknown>>()
  const [changed, setChanged] = useState<Set<number>>(new Set())
  useEffect(() => {
    if (!items) return
    const now = new Map(items.map((x) => [x.id, key(x)]))
    if (prev.current) {
      const diff = new Set(items.filter((x) => prev.current!.has(x.id) && prev.current!.get(x.id) !== key(x)).map((x) => x.id))
      if (diff.size) {
        setChanged(diff)
        const t = window.setTimeout(() => setChanged(new Set()), 1600)
        prev.current = now
        return () => window.clearTimeout(t)
      }
    }
    prev.current = now
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items])
  return changed
}
