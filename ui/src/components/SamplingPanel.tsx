import { useEffect, useState } from 'react'

import { api, type Sampling, type SamplingOptions } from '../api'

// The reader's answer settings: how the agent's model samples. Empty means the server's default.
// Remembered per user in this browser, so they apply to every question until reset.

const FIELDS: { key: keyof Sampling; label: string; step: number }[] = [
  { key: 'temperature', label: 'Temperature', step: 0.05 },
  { key: 'top_p', label: 'Top P', step: 0.05 },
  { key: 'top_k', label: 'Top K', step: 1 },
  { key: 'max_searches', label: 'Searches', step: 1 },
]

const storageKey = (userId: string) => `marginalia.sampling.${userId}`

export function loadSampling(userId: string): Sampling {
  try {
    return JSON.parse(localStorage.getItem(storageKey(userId)) ?? '{}') as Sampling
  } catch {
    return {}
  }
}

function saveSampling(userId: string, value: Sampling) {
  try {
    if (Object.keys(value).length) localStorage.setItem(storageKey(userId), JSON.stringify(value))
    else localStorage.removeItem(storageKey(userId))
  } catch {
    /* private mode: the settings last for this page only */
  }
}

/** A short description of custom settings, for a question that used them. */
export function describeSampling(s: Sampling | undefined): string {
  if (!s || !Object.keys(s).length) return ''
  const names: Record<string, string> = { temperature: 'temperature', top_p: 'top P', top_k: 'top K',
    max_searches: 'searches', seed: 'seed' }
  return Object.entries(s).map(([k, v]) => `${names[k] ?? k} ${v}`).join(', ')
}

export function SamplingPanel({ userId, value, onChange }:
  { userId: string; value: Sampling; onChange: (s: Sampling) => void }) {
  const [options, setOptions] = useState<SamplingOptions | null>(null)
  useEffect(() => {
    api.samplingOptions().then(setOptions).catch(() => setOptions(null))
  }, [])

  function set(key: keyof Sampling, raw: string | number | null) {
    const next = { ...value }
    if (raw === null || raw === '' || (options && key !== 'seed' && Number(raw) === options.defaults[key])) delete next[key]
    else next[key] = Number(raw)
    saveSampling(userId, next)
    onChange(next)
  }

  if (!options) return <div className="sampling muted small">Loading answer settings…</div>
  return (
    <div className="sampling" role="group" aria-label="Answer settings">
      {FIELDS.map(({ key, label, step }) => {
        const limit = options.limits[key]
        const current = value[key] ?? options.defaults[key as keyof SamplingOptions['defaults']]
        return (
          <label key={key} className="slider" title={limit.description}>
            <span className="name">{label}</span>
            <input type="range" min={limit.min ?? 0} max={limit.max ?? 1} step={step} value={current}
              aria-label={label} onChange={(e) => set(key, e.target.value)} />
            <span className={`value ${value[key] !== undefined ? 'custom' : ''}`}>{current}</span>
          </label>
        )
      })}
      <label className="slider" title={options.limits.seed.description}>
        <span className="name">Seed</span>
        <input className="input small" type="number" min={0} placeholder="random" value={value.seed ?? ''}
          aria-label="Seed" onChange={(e) => set('seed', e.target.value === '' ? null : e.target.value)} />
        <span />
      </label>
      <div className="row spread">
        <span className="muted small">Lower temperature gives more focused answers; below 0.3 the model may repeat itself.</span>
        <button type="button" className="btn link small" disabled={!Object.keys(value).length}
          onClick={() => { saveSampling(userId, {}); onChange({}) }}>Reset to defaults</button>
      </div>
    </div>
  )
}
