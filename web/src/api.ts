// Клиент API. Сессия — httpOnly-cookie, поэтому токен в браузере не хранится.

export type Level = 'high' | 'medium' | 'low' | 'no_score' | 'churned'

export interface User { id: number; login: string; displayName: string; viaSso: boolean }

export interface CustomerItem {
  id: number; email: string; balance: number; totalReplenished: number
  score: number | null; loyalty: number | null; level: Level; levelTitle: string; topReason: string | null
  expectedLoss: number; qualityIssues: number
}
export interface CustomerPage {
  items: CustomerItem[]; total: number; page: number; pageSize: number
  sort: string; order: 'asc' | 'desc'; asOf: string; version: number
}
export interface Contribution {
  feature: string; title: string; weight: number; strength: number; points: number
  reason: string; serviceId: number; serviceName: string
}
export interface ServiceOut {
  id: number; name: string; paymentTerm: string; status: string; statusTitle: string; autoProlong: boolean
  price: number; createdAt: string; expiresAt: string; daysLeft: number; score: number | null
  probability: number; revenueHorizon: number; expectedLoss: number
  contributions: Contribution[]; qualityIssues: string[]
}
export interface CustomerDetail extends CustomerItem {
  note: string | null; revenueHorizon: number; ltvAtRisk: number
  finances: { balance: number; bonusBalance: number; bonusState: string; withdrawBalance: number
    totalReplenished: number; availableFunds: number; renewalNeed: number }
  contributions: Contribution[]; actions: string[]; issues: string[]; services: ServiceOut[]
  transactions: { id: number; serviceId: number; type: string; amount: number; status: string
    createdAt: string; performedAt: string | null }[]
  asOf: string; horizonDays: number; version: number
  history: { score: number | null; level: Level; asOf: string; reason: string; recordedAt: string }[]
}
export interface WhatIf { key: string; title: string; score: number | null; level: Level; expectedLoss: number; saved: number }
export interface Renewal {
  date: string; customerId: number; email: string; serviceId: number; service: string; paymentTerm: string
  price: number; autoProlong: boolean; funds: number; problem: 'no_money' | 'no_autoprolong' | null
  level: Level | null; score: number | null
}
export interface Summary {
  asOf: string; horizonDays: number; customersTotal: number
  levels: Record<Level, number>; levelLoss: Record<Level, number>
  expectedLoss: number; worstCase: number; revenueAtRisk: number; balanceAtRisk: number
  ltvAtRisk: number; ltvMonthlyChurn: number
  servicesAtRisk: number; qualityIssues: number; explanation: string
  causes: { feature: string; title: string; kind: 'involuntary' | 'voluntary'; customers: number; expectedLoss: number }[]
  version: number; computedAt: string; durationMs: number
}
export interface TimelinePoint {
  date: string; factRecorded: number; factRestored: number; forecast: number
  factRecordedEur: number; factRestoredEur: number; forecastEur: number
}
export interface Timeline { asOf: string; horizonDays: number; points: TimelinePoint[]; summary: string }

export interface FeatureSetting { weight: number; enabled: boolean }
export interface ModelSettings {
  features: Record<string, FeatureSetting>
  thresholds: { high: number; medium: number }
  horizonDays: number
  ltvMonthlyChurn: number
  asOf: { mode: 'snapshot' | 'now' | 'fixed'; value: string | null }
}
export interface SettingsOut {
  version: number; settings: ModelSettings; resolvedAsOf: string
  features: { key: string; title: string; description: string; defaultWeight: number; weight: number; enabled: boolean }[]
}
export interface Preview {
  before: { levels: Record<Level, number>; expectedLoss: number }
  after: { levels: Record<Level, number>; expectedLoss: number }
  changed: { id: number; email: string; fromLevel: Level; toLevel: Level; fromScore: number | null; toScore: number | null }[]
  changes: string[]
}
export interface HistoryItem { version: number; changes: string[]; author: string; createdAt: string }
export interface ActivityItem { id: number; kind: string; summary: string; author: string; createdAt: string }
export interface SnapshotInfo { id: number; referenceAt: string | null; counts: Record<string, number>; loadedBy: string; loadedAt: string }
export interface LoadResult { accounts: number; services: number; transactions: number; statusChanges: number; version: number }

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message) }
}

function detailText(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail.map((d) => {
      const loc = Array.isArray(d?.loc) ? d.loc.filter((x: unknown) => x !== 'body').join('.') : ''
      return (loc ? loc + ': ' : '') + String(d?.msg ?? '').replace(/^Value error, /, '')
    }).join('; ')
  }
  return 'Ошибка запроса'
}

export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, headers, ...rest } = init
  let res: Response
  try {
    res = await fetch(path, {
      credentials: 'same-origin',
      ...rest,
      headers: { ...(json !== undefined ? { 'Content-Type': 'application/json' } : {}), ...headers },
      body: json !== undefined ? JSON.stringify(json) : rest.body,
    })
  } catch {
    throw new ApiError(0, 'Нет связи с сервером')
  }
  if (res.status === 401 && !path.startsWith('/api/v1/auth/login')) {
    const back = encodeURIComponent(location.pathname + location.search)
    location.assign(`/login?next=${back}`)
    throw new ApiError(401, 'Нужно войти')
  }
  if (!res.ok) {
    let message = `Ошибка ${res.status}`
    try { message = detailText((await res.json()).detail) } catch { /* тело не JSON */ }
    throw new ApiError(res.status, message)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

export const CAUSE_TITLE: Record<string, string> = {
  prolong_wait: 'Ждёт оплаты', insufficient_funds: 'Не хватает средств', unpaid_transactions: 'Неоплаченные счета',
  expiring_no_autoprolong: 'Истекает без автопродления', suspended: 'Сервер выключен',
}

export const LEVEL_TITLE: Record<Level, string> = {
  high: 'Высокий', medium: 'Средний', low: 'Низкий', no_score: 'Нет оценки', churned: 'Ушёл',
}
