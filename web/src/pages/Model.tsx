import { useEffect, useMemo, useState } from 'react'
import { useBlocker } from 'react-router-dom'
import { api, ApiError, LEVEL_TITLE, type HistoryItem, type ModelSettings, type Preview, type SettingsOut } from '../api'
import { useLive, useLiveData } from '../live'
import { dateTime, ErrorBox, eur, RiskBadge, Skeleton, useToast } from '../ui'
import { StatusLine } from './Shell'

/** Разница между сохранёнными и черновыми настройками — тело PATCH (JSON Merge Patch). */
function diffPatch(saved: ModelSettings, draft: ModelSettings) {
  const patch: Record<string, unknown> = {}
  const features: Record<string, unknown> = {}
  for (const [k, f] of Object.entries(draft.features)) {
    const s = saved.features[k]
    if (!s || s.weight !== f.weight || s.enabled !== f.enabled) features[k] = f
  }
  if (Object.keys(features).length) patch.features = features
  if (saved.thresholds.high !== draft.thresholds.high || saved.thresholds.medium !== draft.thresholds.medium) patch.thresholds = draft.thresholds
  if (saved.horizonDays !== draft.horizonDays) patch.horizonDays = draft.horizonDays
  if (saved.ltvMonthlyChurn !== draft.ltvMonthlyChurn) patch.ltvMonthlyChurn = draft.ltvMonthlyChurn
  if (saved.recalcMinutes !== draft.recalcMinutes) patch.recalcMinutes = draft.recalcMinutes
  if (saved.asOf.mode !== draft.asOf.mode || saved.asOf.value !== draft.asOf.value) patch.asOf = draft.asOf
  return patch
}

export default function Model() {
  const { version } = useLive()
  const { data, error } = useLiveData(() => api<SettingsOut>('/api/v1/model/settings'))
  const history = useLiveData(() => api<HistoryItem[]>('/api/v1/model/settings/history?limit=20'))
  const [draft, setDraft] = useState<ModelSettings | null>(null)
  const [preview, setPreview] = useState<Preview | null>(null)
  const [invalid, setInvalid] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const toast = useToast()

  useEffect(() => { document.title = 'Модель — Радар оттока' }, [])
  // черновик берём с сервера только при смене версии настроек (своё или чужое сохранение),
  // пересчёты из-за новых данных не должны сбрасывать то, что пользователь двигает
  useEffect(() => { if (data) setDraft(structuredClone(data.settings)) }, [data?.version]) // eslint-disable-line react-hooks/exhaustive-deps

  const patch = useMemo(() => (data && draft ? diffPatch(data.settings, draft) : {}), [data, draft])
  const dirty = Object.keys(patch).length > 0
  const blocker = useBlocker(({ currentLocation, nextLocation }) => dirty && currentLocation.pathname !== nextLocation.pathname)
  useEffect(() => {
    if (blocker.state === 'blocked') {
      if (window.confirm('Есть несохранённые изменения модели. Уйти без сохранения?')) blocker.proceed()
      else blocker.reset()
    }
  }, [blocker])
  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => { if (dirty) e.preventDefault() }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  // предпросмотр — сразу при движении ползунка, с задержкой 250 мс
  useEffect(() => {
    if (!dirty) { setPreview(null); setInvalid(null); return }
    const t = window.setTimeout(() => {
      api<Preview>('/api/v1/model/preview', { method: 'POST', json: patch })
        .then((p) => { setPreview(p); setInvalid(null) })
        .catch((e: Error) => { setPreview(null); setInvalid(e.message) })
    }, 250)
    return () => window.clearTimeout(t)
  }, [patch, dirty, version])

  async function save() {
    setSaving(true)
    try {
      await api<SettingsOut>(`/api/v1/model/settings?expectedVersion=${data!.version}`, { method: 'PATCH', json: patch })
      toast.show('Сохранено. Панель пересчитана')
    } catch (e) {
      toast.show(e instanceof ApiError ? `Не удалось сохранить: ${e.message}` : 'Не удалось сохранить', 'error')
    } finally {
      setSaving(false)
    }
  }

  if (error && !data) return <ErrorBox message={error} />
  if (!data || !draft) return <Skeleton h={560} />

  const setFeature = (key: string, part: Partial<{ weight: number; enabled: boolean }>) =>
    setDraft({ ...draft, features: { ...draft.features, [key]: { ...draft.features[key], ...part } } })

  return (
    <>
      <div className="ro-top">
        <div><h1>Модель</h1><StatusLine asOf={data.resolvedAsOf} /></div>
        <div className="ro-top__actions">
          <button className="ro-btn ro-btn--quiet ro-btn--sm" type="button" disabled={!dirty || saving}
            onClick={() => setDraft(structuredClone(data.settings))}>Сбросить</button>
          <button className="ro-btn ro-btn--sm" type="button" disabled={!dirty || saving || !!invalid} onClick={save}>
            {saving ? 'Сохраняем…' : 'Сохранить'}</button>
        </div>
      </div>

      <div className="model">
        <section className="ro-card model__weights" aria-labelledby="w-t">
          <div className="ro-cardhead"><h2 id="w-t">Коэффициенты</h2><span className="ro-small">версия настроек {data.version}</span></div>
          {data.features.map((f) => {
            const st = draft.features[f.key]
            return (
              <div className={`weight${st.enabled ? '' : ' is-off'}`} key={f.key}>
                <div className="weight__top">
                  <label htmlFor={`w-${f.key}`}><b>{f.title}</b></label>
                  <label className="switch"><input type="checkbox" checked={st.enabled}
                    onChange={(e) => setFeature(f.key, { enabled: e.target.checked })} />включён</label>
                </div>
                <p className="ro-small">{f.description}</p>
                <div className="weight__ctl">
                  <input id={`w-${f.key}`} className="ro-range" type="range" min={0} max={100} step={1} value={st.weight}
                    disabled={!st.enabled} style={{ ['--p' as string]: `${st.weight}%` }}
                    onChange={(e) => setFeature(f.key, { weight: Number(e.target.value) })} />
                  <input className="num-input" type="number" min={0} max={100} value={st.weight} disabled={!st.enabled}
                    aria-label={`Вес: ${f.title}`} onChange={(e) => setFeature(f.key, { weight: Math.max(0, Math.min(100, Number(e.target.value))) })} />
                </div>
              </div>
            )
          })}

          <div className="model__group">
            <h3 className="ro-label">Пороги уровней</h3>
            <div className="model__row">
              <label className="field"><span>Высокий риск — score от</span>
                <input type="number" min={1} max={100} value={draft.thresholds.high}
                  onChange={(e) => setDraft({ ...draft, thresholds: { ...draft.thresholds, high: Number(e.target.value) } })} /></label>
              <label className="field"><span>Средний риск — score от</span>
                <input type="number" min={1} max={100} value={draft.thresholds.medium}
                  onChange={(e) => setDraft({ ...draft, thresholds: { ...draft.thresholds, medium: Number(e.target.value) } })} /></label>
            </div>
          </div>
          <div className="model__group">
            <h3 className="ro-label">Горизонт прогноза</h3>
            <div className="chips" role="group" aria-label="Горизонт прогноза">
              {[7, 14, 30, 90].map((d) => (
                <button key={d} type="button" aria-pressed={draft.horizonDays === d}
                  onClick={() => setDraft({ ...draft, horizonDays: d })}>{d} дней</button>
              ))}
            </div>
          </div>
          <div className="model__group">
            <h3 className="ro-label">Периодичность прогноза</h3>
            <label className="field"><span>Плановый пересчёт раз в N минут (изменения данных и настроек пересчитываются сразу)</span>
              <input type="number" min={1} max={1440} value={draft.recalcMinutes}
                onChange={(e) => setDraft({ ...draft, recalcMinutes: Number(e.target.value) })} /></label>
          </div>
          <div className="model__group">
            <h3 className="ro-label">LTV</h3>
            <label className="field"><span>Базовый месячный отток, % — LTV = месячная выручка / отток (не больше 36 мес.)</span>
              <input type="number" min={0.5} max={50} step={0.5} value={draft.ltvMonthlyChurn}
                onChange={(e) => setDraft({ ...draft, ltvMonthlyChurn: Number(e.target.value) })} /></label>
          </div>
          <div className="model__group">
            <h3 className="ro-label">Дата расчёта</h3>
            <div className="chips" role="group" aria-label="Дата расчёта">
              {([['snapshot', 'Дата снимка'], ['now', 'Сейчас'], ['fixed', 'Указать дату']] as const).map(([m, t]) => (
                <button key={m} type="button" aria-pressed={draft.asOf.mode === m}
                  onClick={() => setDraft({ ...draft, asOf: { mode: m, value: m === 'fixed' ? (draft.asOf.value ?? data.resolvedAsOf) : null } })}>{t}</button>
              ))}
            </div>
            {draft.asOf.mode === 'fixed' && (
              <label className="field"><span>Дата и время (UTC)</span>
                <input type="datetime-local" value={(draft.asOf.value ?? data.resolvedAsOf).slice(0, 16)}
                  onChange={(e) => setDraft({ ...draft, asOf: { mode: 'fixed', value: e.target.value ? `${e.target.value}:00Z` : null } })} /></label>
            )}
          </div>
        </section>

        <aside className="model__side">
          <section className={`ro-panel preview${dirty ? ' is-dirty' : ''}`} aria-live="polite" aria-labelledby="pv-t">
            <div className="ro-cardhead"><h2 id="pv-t">Предпросмотр изменений</h2></div>
            {!dirty ? <p className="ro-small">Подвигайте вес или порог — здесь сразу появится, сколько клиентов сменят уровень и как изменится сумма. До сохранения ничего не меняется.</p>
              : invalid ? <p className="form__error" role="alert">{invalid}</p>
                : !preview ? <p className="ro-small">Считаем…</p> : (
                  <>
                    <ul className="pv">
                      {(['high', 'medium', 'low'] as const).map((l) => {
                        const a = preview.before.levels[l], b = preview.after.levels[l]
                        return <li key={l}><RiskBadge level={l} title={LEVEL_TITLE[l]} /><span className="ro-mono">{a} → <b>{b}</b>{b !== a && ` (${b > a ? '+' : ''}${b - a})`}</span></li>
                      })}
                      <li><span>Ожидаемая потеря</span><span className="ro-mono">{eur(preview.before.expectedLoss)} → <b>{eur(preview.after.expectedLoss)}</b></span></li>
                    </ul>
                    {preview.changed.length > 0 && (
                      <details className="pv__list" open={preview.changed.length <= 8}>
                        <summary>Сменят уровень: {preview.changed.length}</summary>
                        <ul>{preview.changed.slice(0, 50).map((x) => (
                          <li key={x.id}><span className="ro-mono">{x.email}</span>
                            <span className="ro-small">{LEVEL_TITLE[x.fromLevel].toLowerCase()} {x.fromScore ?? '—'} → {LEVEL_TITLE[x.toLevel].toLowerCase()} {x.toScore ?? '—'}</span></li>
                        ))}</ul>
                      </details>
                    )}
                  </>
                )}
          </section>

          <section className="ro-card" aria-labelledby="f-t">
            <div className="ro-cardhead"><h2 id="f-t">Как считается</h2></div>
            <code className="ro-mono formula">score услуги = min(100, Σ вес × сила сигнала)</code>
            <p className="ro-small">Сила сигнала — от 0 до 1: например, «Не хватает средств» = доля продлений, которую нечем оплатить. Score клиента — максимум по его действующим услугам. Уровень: высокий от {draft.thresholds.high}, средний от {draft.thresholds.medium}.</p>
            <code className="ro-mono formula">потеря = Σ выручка услуги за {draft.horizonDays} дн. × score / 100</code>
          </section>

          <section className="ro-card" aria-labelledby="h-t">
            <div className="ro-cardhead"><h2 id="h-t">Журнал изменений</h2></div>
            {!history.data ? <Skeleton h={120} /> : (
              <ul className="log">
                {history.data.map((h) => (
                  <li key={h.version}><span className="ro-small ro-mono">{h.author} · {dateTime(h.createdAt)}</span>{h.changes.join('; ')}</li>
                ))}
              </ul>
            )}
          </section>
        </aside>
      </div>
      {dirty && preview && (
        <div className="pv-dock" aria-hidden="true">
          Высокий {preview.before.levels.high} → {preview.after.levels.high} · Потеря {eur(preview.after.expectedLoss - preview.before.expectedLoss)}
          <button className="ro-btn ro-btn--sm" type="button" disabled={saving || !!invalid} onClick={save} tabIndex={-1}>Сохранить</button>
        </div>
      )}
      {toast.node}
    </>
  )
}
