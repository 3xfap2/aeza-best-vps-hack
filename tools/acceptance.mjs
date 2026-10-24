// Приёмочная проверка стенда по пунктам ТЗ — одной командой, без зависимостей (Node.js 18+).
//
//   docker compose up -d --build --wait
//   node tools/acceptance.mjs
//
// Адреса и пароли — из переменных окружения (по умолчанию демо-значения compose.yaml):
//   BASE_URL, ADMIN_LOGIN, ADMIN_PASSWORD, METRICS_TOKEN, PROMETHEUS_URL, GRAFANA_URL.
// Проверка меняет данные (событие биллинга, настройки модели) и в конце возвращает их обратно:
// данные — к снимку, настройки — к исходной версии; временный сотрудник удаляется.
// Отметки работы с клиентами не создаёт.

const env = process.env
const B = env.BASE_URL || 'http://localhost:8080'
const LOGIN = env.ADMIN_LOGIN || 'admin'
const PASSWORD = env.ADMIN_PASSWORD || 'radar-demo-2026'
const METRICS = env.METRICS_TOKEN || 'radar-metrics-demo-token'
const PROM = env.PROMETHEUS_URL || 'http://localhost:9090'
const GRAFANA = env.GRAFANA_URL || 'http://localhost:3000'

const results = []
let section = ''
const check = (name, ok, extra = '') => results.push({ section, name, ok: !!ok, extra })
const title = (s) => { section = s }

async function req(path, { method = 'GET', body, cookie, headers = {}, base = B } = {}) {
  const r = await fetch(base + path, {
    method, redirect: 'manual',
    headers: { ...(body ? { 'Content-Type': 'application/json' } : {}), Origin: B, ...(cookie ? { Cookie: cookie } : {}), ...headers },
    body: body ? JSON.stringify(body) : undefined,
  })
  const text = await r.text()
  let json = null
  try { json = JSON.parse(text) } catch { /* не JSON */ }
  return { status: r.status, json, text, headers: r.headers, cookie: (r.headers.get('set-cookie') || '').split(';')[0] }
}

async function firstSseEvent(cookie, ms = 4000) {
  const ctl = new AbortController()
  const timer = setTimeout(() => ctl.abort(), ms)
  try {
    const r = await fetch(B + '/api/v1/stream', { headers: { Cookie: cookie }, signal: ctl.signal })
    const reader = r.body.getReader()
    const { value } = await reader.read()
    ctl.abort()
    return new TextDecoder().decode(value)
  } catch { return '' } finally { clearTimeout(timer) }
}

const run = async () => {
  title('Сайт и безопасность')
  const site = await req('/')
  check('сайт открывается', site.status === 200 && site.text.includes('<div id="root">'))
  check('CSP: скрипты только свои', (site.headers.get('content-security-policy') || '').includes("script-src 'self'"))
  for (const p of ['/api/v1/customers', '/api/v1/analytics/summary', '/api/v1/model/settings', '/api/v1/retention/queue']) {
    check(`без входа ${p} → 401`, (await req(p)).status === 401)
  }
  check('/metrics без токена → 401', (await req('/metrics')).status === 401)
  check('изменение с чужого сайта → 403', (await req('/api/v1/auth/login', { method: 'POST', body: { login: 'x', password: 'y' }, headers: { Origin: 'https://evil.example' } })).status === 403)

  title('Вход')
  const login = await req('/api/v1/auth/login', { method: 'POST', body: { login: LOGIN, password: PASSWORD } })
  check('вход администратора', login.status === 200 && login.json?.user?.role === 'admin')
  const admin = login.cookie
  if (!admin) return
  const oidc = await req('/api/v1/auth/oidc/config')
  check('вход через SSO включён', oidc.json?.enabled === true)

  title('Клиенты: рейтинг, сортировка, фильтр')
  const asc = (await req('/api/v1/customers?sort=balance&order=asc&pageSize=50', { cookie: admin })).json
  const desc = (await req('/api/v1/customers?sort=balance&order=desc&pageSize=50', { cookie: admin })).json
  const sorted = (xs, dir) => xs.every((x, i) => i === 0 || dir * (x.balance - xs[i - 1].balance) >= 0)
  check('сортировка по балансу ↑ и ↓', sorted(asc.items, 1) && sorted(desc.items, -1), `${asc.total} клиентов`)
  const high = (await req('/api/v1/customers?level=high&pageSize=200', { cookie: admin })).json
  check('фильтр «высокий риск»', high.items.length > 0 && high.items.every((c) => c.level === 'high'), `${high.total}`)
  const top = high.items[0]
  const card = (await req(`/api/v1/customers/${top.id}`, { cookie: admin })).json
  check('карточка: причины и вклад признаков', card.contributions.length > 0 && card.contributions.every((c) => c.points > 0 && c.reason))
  check('«что будет, если»', (await req(`/api/v1/customers/${top.id}/what-if`, { cookie: admin })).status === 200)

  title('Деньги')
  const s = (await req('/api/v1/analytics/summary', { cookie: admin })).json
  const levelsSum = Object.values(s.levels).reduce((a, b) => a + b, 0)
  check('уровни складываются в число клиентов', levelsSum === s.customersTotal, `${levelsSum}`)
  check('потеря по прайсу и реально собираемая', s.expectedLoss > 0 && s.expectedLossPaid > 0 && s.expectedLossPaid <= s.expectedLoss,
    `€ ${s.expectedLoss} / € ${s.expectedLossPaid}`)
  check('график оттока: факт и прогноз', (await req('/api/v1/analytics/churn-timeline', { cookie: admin })).json?.points?.length > 0)

  title('Модель: настройки и пересчёт')
  const st = (await req('/api/v1/model/settings', { cookie: admin })).json
  const key = st.features[0].key, w = st.features[0].weight
  const patched = await req(`/api/v1/model/settings?expectedVersion=${st.version}`, { method: 'PATCH', cookie: admin, body: { features: { [key]: { weight: Math.min(100, w + 5) } } } })
  check('PATCH настроек → новая версия', patched.status === 200 && patched.json.version === st.version + 1)
  const stale = await req(`/api/v1/model/settings?expectedVersion=${st.version}`, { method: 'PATCH', cookie: admin, body: { features: { [key]: { weight: w } } } })
  check('устаревшая версия → 409', stale.status === 409)
  const back = await req(`/api/v1/model/settings?expectedVersion=${patched.json.version}`, { method: 'PATCH', cookie: admin, body: { features: { [key]: { weight: w } } } })
  check('настройки вернули', back.status === 200)

  title('Данные в реальном времени')
  const hello = await firstSseEvent(admin)
  check('SSE: поток событий открыт', hello.includes('event:'), hello.split('\n')[0])
  const before = (await req(`/api/v1/customers/${top.id}`, { cookie: admin })).json
  const ev = await req('/api/v1/source/events', { method: 'POST', cookie: admin, body: { type: 'topup', accountId: top.id, amount: 5000 } })
  const after = (await req(`/api/v1/customers/${top.id}`, { cookie: admin })).json
  check('событие биллинга пересчитывает риск', ev.status === 200 && after.score <= before.score, `score ${before.score} → ${after.score}`)
  check('вернуть данные к снимку', (await req('/api/v1/source/reset', { method: 'POST', cookie: admin })).status === 200)

  title('Удержание')
  const q = (await req('/api/v1/retention/queue?callsPerDay=10', { cookie: admin })).json
  check('очередь удержания', q.summary.customers > 0, JSON.stringify(q.summary.buckets))
  const csv = await req('/api/v1/retention/queue.csv', { cookie: admin })
  check('CSV без email по умолчанию', csv.status === 200 && !csv.text.includes('@'))

  title('Роли')
  const tmp = `acceptance-${Date.now()}`
  const created = await req('/api/v1/users', { method: 'POST', cookie: admin, body: { login: tmp, displayName: 'Проверка', password: 'acceptance-pass-2026', role: 'viewer' } })
  check('добавить наблюдателя', created.status === 201)
  const viewer = (await req('/api/v1/auth/login', { method: 'POST', body: { login: tmp, password: 'acceptance-pass-2026' } })).cookie
  check('наблюдатель видит данные', (await req('/api/v1/customers', { cookie: viewer })).status === 200)
  check('наблюдатель не меняет модель (403)', (await req('/api/v1/model/settings', { method: 'PATCH', cookie: viewer, body: {} })).status === 403)
  check('наблюдатель не грузит данные (403)', (await req('/api/v1/source/reset', { method: 'POST', cookie: viewer })).status === 403)
  if (created.json?.id) check('временный сотрудник удалён', (await req(`/api/v1/users/${created.json.id}`, { method: 'DELETE', cookie: admin })).status === 204)

  title('Метрики и мониторинг')
  const m = await req('/metrics', { headers: { Authorization: `Bearer ${METRICS}` } })
  const loss = Number((m.text.match(/^churn_radar_expected_loss_eur\{[^}]*\} ([\d.e+]+)/m) || [])[1])
  check('/metrics с токеном = сводка', m.status === 200 && Math.abs(loss - s.expectedLoss) < 0.01, `€ ${loss}`)
  try {
    // сразу после запуска Prometheus ещё не успел сходить за метриками (раз в 15 с) — ждём до 30 с
    let up = false
    for (let i = 0; i < 15 && !up; i++) {
      const t = await (await fetch(PROM + '/api/v1/targets')).json()
      up = t.data.activeTargets.some((x) => x.health === 'up')
      if (!up) await new Promise((r) => setTimeout(r, 2000))
    }
    check('Prometheus собирает /metrics', up)
    const rules = await (await fetch(PROM + '/api/v1/rules')).json()
    check('Prometheus: правила алертов', rules.data.groups.some((g) => g.name === 'churn-radar'))
  } catch (e) { check('Prometheus доступен', false, String(e.message)) }
  try {
    const g = await (await fetch(GRAFANA + '/api/dashboards/uid/churn-radar')).json()
    check('Grafana: дашборд «Радар оттока»', g.dashboard?.panels?.length > 0)
  } catch (e) { check('Grafana доступна', false, String(e.message)) }

  title('Документация и ИИ')
  check('Swagger', (await req('/api/v1/docs')).status === 200)
  const ai = (await req('/api/v1/ai/status', { cookie: admin })).json
  check('ИИ-помощник отвечает о статусе', ai && typeof ai.enabled === 'boolean', ai?.enabled ? `${ai.provider}, ${ai.model}` : 'не подключён — работают шаблоны')
}

run().catch((e) => check('проверка завершилась ошибкой', false, e.message)).finally(() => {
  let last = ''
  for (const r of results) {
    if (r.section !== last) { console.log(`\n${r.section}`); last = r.section }
    console.log(`  ${r.ok ? 'OK  ' : 'FAIL'} ${r.name}${r.extra ? ' — ' + r.extra : ''}`)
  }
  const failed = results.filter((r) => !r.ok).length
  console.log(`\n${results.length - failed} из ${results.length} проверок пройдено${failed ? `, ошибок: ${failed}` : ''}`)
  process.exit(failed ? 1 : 0)
})
