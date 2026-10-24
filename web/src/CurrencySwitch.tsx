import { useEffect, useState } from 'react'
import { api } from './api'
import { currentCurrency, currentFx, setCurrency, setFx, type Currency, type Fx } from './currency'

/** Валюта отображения: € или ₽ по курсу ЦБ. onChange — перерисовать экраны с новыми суммами. */
export default function CurrencySwitch({ onChange }: { onChange: (c: Currency) => void }) {
  const [cur, setCur] = useState<Currency>(currentCurrency)
  const [fx, setFxState] = useState<Fx | null>(currentFx)
  useEffect(() => {
    api<Fx>('/api/v1/fx').then((f) => { setFx(f); setFxState(f); onChange(currentCurrency()) }).catch(() => setFx(null))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const pick = (c: Currency) => { setCurrency(c); setCur(c); onChange(c) }
  const day = fx ? fx.date.split('-').reverse().join('.') : ''
  return (
    <div className="theme-box">
      <span className="theme-switch__label" id="cur-l">Валюта</span>
      <div className="theme-switch" role="group" aria-labelledby="cur-l">
        <button type="button" aria-pressed={cur === 'EUR'} onClick={() => pick('EUR')}>€</button>
        <button type="button" aria-pressed={cur === 'RUB' && !!fx} disabled={!fx} onClick={() => pick('RUB')}
          title={fx ? `1 € = ${fx.rate.toLocaleString('ru-RU', { maximumFractionDigits: 4 })} ₽ · ${fx.source}, ${day}` : 'Курс ЦБ сейчас недоступен'}>₽</button>
      </div>
    </div>
  )
}
