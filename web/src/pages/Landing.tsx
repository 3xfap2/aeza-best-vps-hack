import { useEffect, useRef } from 'react'
import ThemeSwitch from '../ThemeSwitch'

/** Лендинг — компоненты дизайн-системы (радар, история, формула), данные — демо-база из landing-data.js. */
export default function Landing() {
  const root = useRef<HTMLDivElement>(null)
  useEffect(() => {
    document.title = 'Радар оттока — кто из клиентов не продлит сервер'
    const page = window.Ottok.LandingA.mount(root.current!)
    return () => page.destroy()
  }, [])
  return <><div ref={root} /><ThemeSwitch floating /></>
}
