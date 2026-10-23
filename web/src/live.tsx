import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'

// Живое обновление: сервер шлёт событие recalculated после каждого пересчёта,
// экраны подписаны на version и перечитывают данные сами.

interface Live {
  version: number
  connected: boolean
  computedAt: string | null
  reason: string | null
}

const LiveContext = createContext<Live>({ version: 0, connected: false, computedAt: null, reason: null })

export function LiveProvider({ children }: { children: ReactNode }) {
  const [live, setLive] = useState<Live>({ version: 0, connected: false, computedAt: null, reason: null })

  useEffect(() => {
    const es = new EventSource('/api/v1/stream')
    const onData = (e: MessageEvent) => {
      try {
        const d = JSON.parse(e.data)
        setLive((s) => ({ version: d.version ?? s.version, connected: true, computedAt: d.computedAt ?? s.computedAt,
          reason: d.reason ?? null }))
      } catch { /* служебное сообщение */ }
    }
    es.addEventListener('hello', onData as EventListener)
    es.addEventListener('recalculated', onData as EventListener)
    es.onopen = () => setLive((s) => ({ ...s, connected: true }))
    es.onerror = () => setLive((s) => ({ ...s, connected: false }))
    // При уходе со страницы соединение закрываем сразу — иначе браузер держит лишний поток.
    const close = () => es.close()
    window.addEventListener('pagehide', close)
    return () => { window.removeEventListener('pagehide', close); es.close() }
  }, [])

  return <LiveContext.Provider value={live}>{children}</LiveContext.Provider>
}

export function useLive() {
  return useContext(LiveContext)
}

/** Загрузка данных, которая повторяется после каждого пересчёта на сервере. */
export function useLiveData<T>(load: () => Promise<T>, deps: unknown[] = []) {
  const { version } = useLive()
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    let alive = true
    setLoading(true)
    load()
      .then((d) => { if (alive) { setData(d); setError(null) } })
      .catch((e: Error) => { if (alive) setError(e.message) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [version, ...deps])
  return { data, error, loading }
}
