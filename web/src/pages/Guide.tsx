import { useEffect } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { useLiveData } from '../live'

const STEPS = [
  {
    title: 'Посмотрите, сколько денег под угрозой',
    text: 'На «Обзоре» крупная сумма — сколько выручки за 30 дней бизнес, скорее всего, не получит. Рядом — худший случай и LTV под угрозой (если клиенты уйдут насовсем).',
    link: ['/app', 'Открыть обзор'],
  },
  {
    title: 'Найдите, кому звонить первым',
    text: '«Клиенты» — рейтинг по риску. Сортируйте по score, потере, балансу или пополнениям, фильтруйте по уровню. Блок «За что мы теряем деньги» на обзоре группирует клиентов по причине и выгружает CSV для обзвона.',
    link: ['/app/customers?sort=expectedLoss', 'Клиенты по сумме потери'],
  },
  {
    title: 'Откройте карточку клиента',
    text: '«Почему такой риск» — вклад каждого признака. «Что сделать» — готовое действие. «Что будет, если…» — как изменится риск, если клиент пополнит счёт или включит автопродление.',
    link: null,
  },
  {
    title: 'Предупредите заранее',
    text: '«Календарь продлений» на обзоре — продления на 14 дней, которым помешает нехватка денег или выключенное автопродление. Напомните до срока, а не после удаления.',
    link: ['/app', 'Календарь на обзоре'],
  },
  {
    title: 'Настройте модель под себя',
    text: '«Модель» — веса признаков и пороги уровней. Двигайте ползунок: справа сразу видно, сколько клиентов сменят уровень и как изменится сумма. «Сохранить» — пересчитаются все экраны.',
    link: ['/app/model', 'Открыть модель'],
  },
  {
    title: 'Проверьте на живых данных',
    text: '«Данные» — загрузите свой снимок, отправьте событие биллинга (например, пополнение) или перемотайте время на день вперёд: клиенты перейдут между уровнями прямо на глазах.',
    link: ['/app/data', 'Открыть данные'],
  },
]

const LEVELS = [
  ['high', 'Высокий — score от 60', 'Срочно: клиент, скорее всего, не продлит в ближайшие дни. Связаться сегодня.'],
  ['mid', 'Средний — от 30', 'Есть сигналы: напомнить о продлении, предложить включить автопродление.'],
  ['low', 'Низкий — ниже 30', 'Всё в порядке или слабые сигналы. Действий не требует.'],
  ['none', 'Нет оценки / Ушёл', 'Нет действующих услуг (все заблокированы) или все удалены — в сумму потерь не входят.'],
]

const FAQ = [
  ['Что такое score?', 'Число 0–100: сумма баллов по признакам риска. Балл признака = вес × сила сигнала (0–1). Score клиента — максимум по его действующим услугам.'],
  ['Как считается сумма под угрозой?', 'Для каждой действующей услуги: выручка за 30 дней × вероятность непродления (score / 100). Почасовая услуга считается за 720 часов, годовая — 30/365 цены.'],
  ['Что значит «удаление задержано»?', 'Услуга ждёт оплаты дольше обычных 5 дней. На практике так бывает; клиента ещё можно вернуть, поэтому он в высоком риске.'],
  ['Данные обновляются сами?', 'Да. Как только биллинг присылает событие или вы меняете настройки, все экраны пересчитываются без перезагрузки — следите за строкой «пересчитано N с назад».'],
]

export default function Guide() {
  const tg = useLiveData(() => api<{ enabled: boolean; botUsername: string | null; connectUrl: string | null }>('/api/v1/integrations/telegram'))
  useEffect(() => { document.title = 'Как пользоваться — Радар оттока' }, [])
  return (
    <>
      <div className="ro-top"><div><h1>Как пользоваться</h1>
        <p className="ro-small">Шесть шагов, чтобы за 5 минут понять, кого удерживать и сколько это стоит.</p></div></div>

      <ol className="guide">
        {STEPS.map((s, i) => (
          <li key={s.title} className="ro-card">
            <span className="guide__n" aria-hidden="true">{i + 1}</span>
            <div>
              <h2>{s.title}</h2>
              <p className="ro-text">{s.text}</p>
              {s.link && <Link className="ro-link" to={s.link[0]}>{s.link[1]} →</Link>}
            </div>
          </li>
        ))}
      </ol>

      <div className="guide__cols">
        <section className="ro-card guide__block" aria-labelledby="g-lv">
          <h2 id="g-lv">Уровни риска</h2>
          <ul className="guide__levels">
            {LEVELS.map(([cls, t, d]) => (
              <li key={t}><span className={`ro-risk ro-risk--${cls}`}>{t}</span><span className="ro-small">{d}</span></li>
            ))}
          </ul>
        </section>
        <section className="ro-panel guide__block" aria-labelledby="g-tg">
          <h2 id="g-tg">Уведомления в Telegram</h2>
          <p className="ro-text">Бот пишет, когда клиент переходит в высокий риск: кто, почему, сколько и что сделать. Команды: /summary, /top, /client 42.</p>
          {tg.data?.connectUrl
            ? <a className="ro-btn ro-btn--sm" href={tg.data.connectUrl} target="_blank" rel="noreferrer">Подключить @{tg.data.botUsername}</a>
            : <p className="ro-small">{tg.data?.enabled ? 'Бот запускается — обновите страницу через минуту.' : 'Бот не настроен на этом сервере.'}</p>}
        </section>
      </div>

      <section className="ro-card guide__block" aria-labelledby="g-faq">
        <h2 id="g-faq">Частые вопросы</h2>
        <div className="guide__faq">
          {FAQ.map(([q, a]) => <details key={q}><summary>{q}</summary><p className="ro-text">{a}</p></details>)}
        </div>
        <p className="ro-small">Подробно о модели и API — в <a className="ro-link" href="/api/v1/docs" target="_blank" rel="noreferrer">документации API</a>.</p>
      </section>
    </>
  )
}
