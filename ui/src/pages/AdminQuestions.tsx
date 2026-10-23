import { useCallback, useEffect, useRef, useState } from 'react'

import { api, type AdminQuestion, type AdminQuestionDetail, type QuestionStats } from '../api'
import { Sheet, useToast } from '../components/ui'
import { describeSampling } from '../components/SamplingPanel'
import { useLiveEvents } from '../events'
import { duration, relativeTime } from '../format'
import { splitCitations } from '../progress'
import { stream } from '../session'

const OUTCOMES = [
  { key: '', label: 'All' },
  { key: 'agent', label: 'Agent' },
  { key: 'cache', label: 'Cache' },
  { key: 'refused', label: 'Refused' },
  { key: 'failed', label: 'Failed' },
  { key: 'active', label: 'In progress' },
] as const

const REFUSALS: Record<string, string> = { injection: 'injection', not_in_book: 'not in the book', off_topic: 'off-topic' }

export function outcomeOf(q: Pick<AdminQuestion, 'status' | 'answered_by' | 'refused' | 'supported'>): [string, string] {
  if (q.status === 'refused') return [`Refused: ${REFUSALS[q.refused ?? ''] ?? 'guard'}`, 'orange']
  if (q.status === 'failed') return ['Failed', 'red']
  if (q.status === 'queued' || q.status === 'running') return [q.status === 'queued' ? 'Queued' : 'Answering', 'blue']
  if (q.answered_by === 'cache') return ['From cache', 'green']
  return [q.supported === false ? 'Answered, unchecked claims' : 'Answered', q.supported === false ? 'orange' : 'green']
}

export function QuestionsTab() {
  const toast = useToast()
  const [outcome, setOutcome] = useState('')
  const [search, setSearch] = useState('')
  const [items, setItems] = useState<AdminQuestion[] | null>(null)
  const [next, setNext] = useState<string | null>(null)
  const [stats, setStats] = useState<QuestionStats | null>(null)
  const [open, setOpen] = useState<AdminQuestionDetail | null>(null)
  const refresh = useRef<ReturnType<typeof setTimeout> | null>(null)

  const load = useCallback(async () => {
    try {
      const [page, s] = await Promise.all([api.adminQuestions({ outcome, search }), api.questionStats(24)])
      setItems(page.items)
      setNext(page.next)
      setStats(s)
    } catch (e) {
      toast((e as Error).message, true)
    }
  }, [outcome, search, toast])

  useEffect(() => {
    const t = setTimeout(() => void load(), search ? 300 : 0)      // let the reader finish typing
    return () => clearTimeout(t)
  }, [load, search])

  // New and finished questions arrive as events; refresh at most once a second.
  useLiveEvents(stream, (ev) => {
    if (!ev.type.startsWith('question.') || (ev.type === 'question.status' && ev.status !== 'screening')) return
    if (refresh.current) return
    refresh.current = setTimeout(() => { refresh.current = null; void load() }, 1000)
  })

  async function more() {
    if (!next) return
    const page = await api.adminQuestions({ outcome, search, before: next })
    setItems((list) => [...(list ?? []), ...page.items])
    setNext(page.next)
  }

  return (
    <div className="stack">
      {stats && (
        <div className="stats">
          <Stat label="Questions, last 24 h" value={stats.total} note={`${stats.readers} reader(s)`} />
          <Stat label="Answered by the agent" value={stats.outcomes.agent}
            note={stats.agent_seconds.p50 != null ? `median ${duration(stats.agent_seconds.p50)}, p95 ${duration(stats.agent_seconds.p95)}` : ''} />
          <Stat label="From the cache" value={stats.outcomes.cache}
            note={stats.cache_hit_rate != null ? `${Math.round(stats.cache_hit_rate * 100)}% of answers` : ''} />
          <Stat label="Refused" value={stats.outcomes.refused}
            note={Object.entries(stats.refused).map(([k, n]) => `${n} ${REFUSALS[k] ?? k}`).join(', ')} />
          <Stat label="Failed" value={stats.outcomes.failed} note={stats.outcomes.active ? `${stats.outcomes.active} in progress` : ''} />
          <Stat label="Tokens, last 24 h" value={stats.tokens.total}
            note={stats.tokens.mean != null ? `${stats.tokens.mean} per answer (${stats.tokens.min}–${stats.tokens.max})` +
              (stats.tokens.per_second ? `, ${stats.tokens.per_second}/s` : '') : ''} />
        </div>
      )}

      <div className="spread">
        <div className="segmented" role="tablist" aria-label="Outcome">
          {OUTCOMES.map((o) => (
            <button key={o.key} role="tab" aria-selected={outcome === o.key} className={outcome === o.key ? 'on' : ''}
              onClick={() => setOutcome(o.key)}>{o.label}</button>
          ))}
        </div>
        <input className="input" style={{ maxWidth: 280 }} placeholder="Search questions" aria-label="Search questions"
          value={search} onChange={(e) => setSearch(e.target.value)} />
      </div>

      <div className="card flush">
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Asked</th><th>Reader</th><th>Book</th><th>Question</th><th>Outcome</th><th className="num">Tokens</th>
              <th className="num">Took</th></tr></thead>
            <tbody>
              {items === null && <tr><td colSpan={7} className="muted"><span className="spinner" /> Loading…</td></tr>}
              {items?.length === 0 && <tr><td colSpan={7} className="muted">No questions match.</td></tr>}
              {items?.map((q) => {
                const [label, tone] = outcomeOf(q)
                return (
                  <tr key={q.id} className="selectable" onClick={() => void api.adminQuestion(q.id).then(setOpen)}>
                    <td className="muted small" title={q.created_at}>{relativeTime(q.created_at)}</td>
                    <td className="small">{q.user}</td>
                    <td className="small">{q.book}</td>
                    <td className="clip">{q.text}</td>
                    <td><span className={`pill ${tone}`}>{label}</span></td>
                    <td className="num muted">{q.tokens?.toLocaleString() ?? ''}</td>
                    <td className="num muted">{duration(q.seconds)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        {next && <div style={{ padding: 14, textAlign: 'center' }}><button className="btn secondary small" onClick={() => void more()}>Show older</button></div>}
      </div>

      {open && <QuestionSheet q={open} onClose={() => setOpen(null)} />}
    </div>
  )
}

function Stat({ label, value, note }: { label: string; value: number; note?: string }) {
  return (
    <div className="card">
      <div className="muted small">{label}</div>
      <div className="stat">{value}</div>
      {note && <div className="muted small" style={{ marginTop: 8 }}>{note}</div>}
    </div>
  )
}

function QuestionSheet({ q, onClose }: { q: AdminQuestionDetail; onClose: () => void }) {
  const [label, tone] = outcomeOf(q)
  const guard = q.details.guard as Record<string, unknown> | undefined
  const calls = (q.details.tool_calls as { name: string; args: Record<string, unknown> }[] | undefined) ?? []
  const unsupported = (q.details.unsupported_claims as string[] | undefined) ?? []
  type Tally = { calls: number; prompt: number; completion: number; total: number; per_second: number | null }
  const tokens = q.details.tokens as Record<string, Tally> | undefined
  return (
    <Sheet title="Question" onClose={onClose}>
      <p style={{ fontWeight: 600, marginTop: 0 }}>{q.text}</p>
      <div className="row small muted">
        <span className={`pill ${tone}`}>{label}</span>
        <span>{q.user}</span><span>·</span><span>{q.book}</span><span>·</span>
        <span>{new Date(q.created_at).toLocaleString()}</span>
        {q.seconds != null && <><span>·</span><span>{duration(q.seconds)}</span></>}
      </div>
      {q.answer && (
        <div className="a" style={{ marginTop: 16 }}>
          {splitCitations(q.answer).map((p, i) => p.citation ? <span key={i} className="cite">{p.citation}</span> : <span key={i}>{p.text}</span>)}
        </div>
      )}
      {q.error && <div className={`banner ${q.status === 'failed' ? 'red' : ''}`} style={{ marginTop: 16 }}>{q.error}</div>}
      <dl className="facts">
        {describeSampling(q.sampling) && <><dt>Answer settings</dt><dd>{describeSampling(q.sampling)}</dd></>}
        {q.cached && <><dt>Cache</dt><dd>{q.cached} match</dd></>}
        {guard && <><dt>Guard</dt><dd>{Object.entries(guard).map(([k, v]) => `${k}: ${JSON.stringify(v)}`).join(' · ')}</dd></>}
        {calls.length > 0 && <><dt>Searches</dt><dd><ol>{calls.map((c, i) => <li key={i}><code>{c.name}</code> {JSON.stringify(c.args)}</li>)}</ol></dd></>}
        {unsupported.length > 0 && <><dt>Unchecked claims</dt><dd><ul>{unsupported.map((c, i) => <li key={i}>{c}</li>)}</ul></dd></>}
        {q.revisions != null && <><dt>Revisions</dt><dd>{q.revisions}</dd></>}
        {tokens?.total && <><dt>Tokens</dt><dd>{Object.entries(tokens).filter(([k]) => k !== 'total').map(([role, t]) =>
          `${role}: ${t.prompt.toLocaleString()} in, ${t.completion.toLocaleString()} out in ${t.calls} call(s)` +
          (t.per_second ? `, ${t.per_second}/s` : '')).join(' · ')}</dd></>}
      </dl>
    </Sheet>
  )
}
