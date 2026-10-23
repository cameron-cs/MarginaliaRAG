import { useCallback, useEffect, useRef, useState } from 'react'

import { api, type LogLine, type Pipeline, type PipelineRun, type TaskInstance, type Waiting } from '../api'
import { Sheet, StatePill, Switch, useToast } from '../components/ui'
import { useLiveEvents, type LiveEvent } from '../events'
import { clockTime, describeSchedule, duration, relativeTime } from '../format'
import { stream } from '../session'

const ACTIVE = ['queued', 'running']
const PAGE = 15
const RUN_FILTERS = [
  { key: '', label: 'All' }, { key: 'running', label: 'Running' }, { key: 'success', label: 'Succeeded' },
  { key: 'failed', label: 'Failed' },
] as const

export function PipelineTab() {
  const toast = useToast()
  const [pipeline, setPipeline] = useState<Pipeline | null>(null)
  const [waiting, setWaiting] = useState<Waiting | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState('')
  const [pages, setPages] = useState(1)
  const [selected, setSelected] = useState<string | null>(null)
  const [tasks, setTasks] = useState<TaskInstance[]>([])
  const [feed, setFeed] = useState<LiveEvent[]>([])
  const [busy, setBusy] = useState(false)
  const [logOf, setLogOf] = useState<{ run: string; task: TaskInstance } | null>(null)

  const load = useCallback(async () => {
    try {
      const [p, w] = await Promise.all([api.pipeline({ state: filter, limit: PAGE * pages }), api.waiting()])
      setPipeline(p)
      setWaiting(w)
      setError(null)
      setSelected((s) => s ?? p.runs[0]?.run_id ?? null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [filter, pages])
  const loadTasks = useCallback(async (runId: string) => {
    try {
      setTasks(await api.runTasks(runId))
    } catch {
      setTasks([])
    }
  }, [])

  useEffect(() => void load(), [load])
  useEffect(() => {
    if (selected) void loadTasks(selected)
  }, [selected, loadTasks])

  // While a run is going, refresh every few seconds; events also trigger a refresh.
  const active = pipeline?.runs.some((r) => ACTIVE.includes(r.state))
  useEffect(() => {
    if (!active) return
    const t = setInterval(() => {
      void load()
      if (selected) void loadTasks(selected)
    }, 4000)
    return () => clearInterval(t)
  }, [active, selected, load, loadTasks])

  useLiveEvents(stream, (ev) => {
    if (ev.type !== 'pipeline.task' && ev.type !== 'book.status') return
    setFeed((f) => [ev, ...f].slice(0, 60))
    if (ev.type === 'pipeline.task' && (ev.task === 'plan' || ev.task === 'finalize')) void load()
    if (ev.type === 'pipeline.task' && ev.run_id === selected) void loadTasks(selected)
  })

  async function act(label: string, fn: () => Promise<unknown>) {
    setBusy(true)
    try {
      await fn()
      toast(label)
      await load()
      if (selected) await loadTasks(selected)
    } catch (e) {
      toast((e as Error).message, true)
    } finally {
      setBusy(false)
    }
  }

  const start = (only: string[] = []) => act(only.length ? `Batch started for ${only.length} book(s).` : 'Batch started.',
    async () => {
      const r = await api.startRun([], only)
      setFilter('')
      setSelected(r.run_id)
    })

  if (error) return <div className="banner red">{error}</div>
  if (!pipeline) return <div className="row muted"><span className="spinner" /> Loading the pipeline…</div>
  const run = pipeline.runs.find((r) => r.run_id === selected)
  const { by_state: byState, mean_duration: mean, longest } = pipeline.summary

  return (
    <div className="stack">
      <div className="stats">
        <div className="card">
          <div className="muted small">Schedule</div>
          <div className="stat">{describeSchedule(pipeline.schedule)}</div>
          <div className="row" style={{ marginTop: 12 }}>
            <Switch label="Schedule on" checked={!pipeline.paused} disabled={busy}
              onChange={(on) => void act(on ? 'Schedule resumed.' : 'Schedule paused.', () => api.setPaused(!on))} />
            <span className="small muted">{pipeline.paused ? 'Paused' : 'On'}</span>
          </div>
        </div>
        <div className="card">
          <div className="muted small">Next batch</div>
          <div className="stat">{pipeline.paused ? '—' : clockTime(pipeline.next_run_at)}</div>
          <div className="small muted" style={{ marginTop: 12 }}>{pipeline.paused ? 'Paused' : relativeTime(pipeline.next_run_at)}</div>
        </div>
        <div className="card">
          <div className="muted small">Recent batches</div>
          <div className="stat">{(byState.success ?? 0)} ok · {(byState.failed ?? 0)} failed</div>
          <div className="small muted" style={{ marginTop: 12 }}>
            {mean != null ? `${duration(mean)} on average, longest ${duration(longest)}` : 'No finished batches yet'}
          </div>
        </div>
      </div>

      {pipeline.import_errors && <div className="banner red">The DAG has import errors; check Airflow.</div>}

      {waiting && <WaitingCard waiting={waiting} busy={busy} onRun={(only) => void start(only)} />}

      <div className="spread">
        <h3 style={{ margin: 0 }}>Batches <span className="muted small">{pipeline.total} in all</span></h3>
        <div className="segmented" role="tablist" aria-label="Batch state">
          {RUN_FILTERS.map((f) => (
            <button key={f.key} role="tab" aria-selected={filter === f.key} className={filter === f.key ? 'on' : ''}
              onClick={() => { setFilter(f.key); setPages(1) }}>{f.label}</button>
          ))}
        </div>
      </div>

      <div className="card flush">
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Run</th><th>State</th><th>Started</th><th className="num">Took</th></tr></thead>
            <tbody>
              {pipeline.runs.length === 0 && <tr><td colSpan={4} className="muted">No batches here.</td></tr>}
              {pipeline.runs.map((r) => (
                <tr key={r.run_id} className={`selectable ${r.run_id === selected ? 'selected' : ''}`} onClick={() => setSelected(r.run_id)}>
                  <td>{runLabel(r)}<span className="muted small">{runScope(r)}</span></td>
                  <td><StatePill state={r.state} /></td>
                  <td className="muted" title={r.start_date ?? ''}>{relativeTime(r.start_date)}</td>
                  <td className="num muted">{runDuration(r)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {pipeline.runs.length < pipeline.total && (
          <div style={{ padding: 14, textAlign: 'center' }}>
            <button className="btn secondary small" onClick={() => setPages(pages + 1)}>Show older</button>
          </div>
        )}
      </div>

      {run && (
        <div className="card flush">
          <div className="spread" style={{ padding: '18px 20px' }}>
            <div>
              <h3 style={{ margin: 0 }}>{runLabel(run)}</h3>
              <span className="muted small">{run.run_id}{run.note ? ` · ${run.note}` : ''}</span>
            </div>
            {ACTIVE.includes(run.state) && (
              <button className="btn danger small" disabled={busy}
                onClick={() => confirm('Stop this batch? Running tasks are killed.') && void act('Batch stopped.', () => api.stopRun(run.run_id))}>
                Stop batch
              </button>
            )}
          </div>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Task</th><th>Book</th><th>State</th><th className="num">Try</th><th className="num">Took</th><th /></tr></thead>
              <tbody>
                {orderTasks(tasks).map((t) => (
                  <tr key={`${t.task_id}:${t.map_index}`}>
                    <td>{TASK_NAMES[t.task_id] ?? t.task_id}</td>
                    <td className="small">{t.label ?? (t.map_index >= 0 ? `#${t.map_index + 1}` : '')}</td>
                    <td><StatePill state={t.state} /></td>
                    <td className="num muted">{t.try_number || ''}</td>
                    <td className="num muted">{duration(t.duration)}</td>
                    <td className="num nowrap">
                      {t.try_number > 0 && (
                        <button className="btn secondary small" onClick={() => setLogOf({ run: run.run_id, task: t })}>Logs</button>
                      )}
                      {t.state && ['running', 'queued', 'up_for_retry', 'scheduled'].includes(t.state) && (
                        <button className="btn secondary small" disabled={busy}
                          onClick={() => void act('Task stopped.', () => api.stopTask(run.run_id, t.task_id, t.map_index))}>Stop</button>
                      )}
                      {t.state && ['failed', 'upstream_failed'].includes(t.state) && (
                        <button className="btn secondary small" disabled={busy}
                          onClick={() => void act('Task restarted.', () => api.retryTask(run.run_id, t.task_id))}>Retry</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="card flush">
        <h3 style={{ padding: '18px 20px 0' }}>Live activity</h3>
        <ul className="feed">
          {feed.length === 0 && <li className="muted">Events appear here while a batch runs.</li>}
          {feed.map((ev, i) => (
            <li key={i}>
              <time>{clockTime(ev.at)}</time>
              <span>{ev.type === 'pipeline.task'
                ? `${ev.task} ${ev.state}${ev.book_id ? ` · ${ev.book_id}` : ''}`
                : `${ev.message}`}</span>
            </li>
          ))}
        </ul>
      </div>

      {logOf && <LogSheet run={logOf.run} task={logOf.task} onClose={() => setLogOf(null)} />}
    </div>
  )
}

/** What the next batch would do, with buttons to do all of it now or only some books. */
export function WaitingCard({ waiting, busy, onRun }: { waiting: Waiting; busy: boolean; onRun: (only: string[]) => void }) {
  const [chosen, setChosen] = useState<string[]>([])
  const ids = waiting.books.map((b) => b.book_id)
  useEffect(() => {
    setChosen((c) => c.filter((id) => ids.includes(id)))
  }, [ids.join()])     // eslint-disable-line react-hooks/exhaustive-deps
  const nothing = waiting.books.length === 0 && waiting.reviews.length === 0
  const toggle = (id: string) => setChosen((c) => (c.includes(id) ? c.filter((x) => x !== id) : [...c, id]))

  return (
    <div className="card flush">
      <div className="spread" style={{ padding: '18px 20px' }}>
        <div>
          <h3 style={{ margin: 0 }}>Waiting for the next batch</h3>
          <span className="muted small">
            {nothing ? 'Nothing is waiting.' : `${waiting.books.length} book(s), ${waiting.reviews.length} prompt review(s)`}
          </span>
        </div>
        <div className="row">
          {chosen.length > 0 && (
            <button className="btn secondary" disabled={busy} onClick={() => onRun(chosen)}>Run {chosen.length} selected</button>
          )}
          <button className="btn" disabled={busy} onClick={() => onRun([])}>{nothing ? 'Run a batch' : 'Run all now'}</button>
        </div>
      </div>
      {!nothing && (
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th style={{ width: 36 }} /><th>Book</th><th>Needs</th><th className="num">Readers</th><th /></tr></thead>
            <tbody>
              {waiting.books.map((b) => (
                <tr key={b.book_id}>
                  <td><input type="checkbox" aria-label={`Choose ${b.title}`} checked={chosen.includes(b.book_id)}
                    onChange={() => toggle(b.book_id)} /></td>
                  <td>
                    <div style={{ fontWeight: 600 }}>{b.title}</div>
                    <div className="muted small">{b.book_id}{b.ready ? ' · answering questions meanwhile' : ''}</div>
                  </td>
                  <td>{b.steps.map((s) => <span key={s} className="pill" style={{ marginRight: 4 }}>{STEP_NAMES[s] ?? s}</span>)}</td>
                  <td className="num">{b.readers}</td>
                  <td className="num"><button className="btn secondary small" disabled={busy} onClick={() => onRun([b.book_id])}>Process now</button></td>
                </tr>
              ))}
              {waiting.reviews.map((r) => (
                <tr key={r.user_book_id}>
                  <td />
                  <td><div>{r.title ?? r.book_id}</div><div className="muted small">{r.user}</div></td>
                  <td><span className="pill">Review prompts</span></td>
                  <td />
                  <td className="num">{r.book_id && (
                    <button className="btn secondary small" disabled={busy} onClick={() => onRun([r.book_id!])}>Process now</button>
                  )}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

const LEVEL_TONES: Record<string, string> = { error: 'red', critical: 'red', warning: 'orange' }

export function LogSheet({ run, task, onClose }: { run: string; task: TaskInstance; onClose: () => void }) {
  const [lines, setLines] = useState<LogLine[] | null>(null)
  const [tryNumber, setTryNumber] = useState(task.try_number || 1)
  const [error, setError] = useState<string | null>(null)
  const end = useRef<HTMLDivElement>(null)
  const running = task.state === 'running' || task.state === 'queued'

  const load = useCallback(async () => {
    try {
      setLines((await api.taskLogs(run, task.task_id, task.map_index, tryNumber)).lines)
      setError(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }, [run, task.task_id, task.map_index, tryNumber])

  useEffect(() => void load(), [load])
  useEffect(() => {
    if (!running) return
    const t = setInterval(() => void load(), 3000)
    return () => clearInterval(t)
  }, [running, load])
  // A block body: Chromium's scrollIntoView returns a promise, which React would take for a cleanup.
  useEffect(() => {
    end.current?.scrollIntoView?.({ block: 'end' })
  }, [lines])

  const title = `${TASK_NAMES[task.task_id] ?? task.task_id}${task.label ? ` · ${task.label}` : ''}`
  return (
    <Sheet title={title} onClose={onClose}>
      <div className="spread small muted">
        <span>{run}</span>
        <span className="row">
          {task.try_number > 1 && (
            <select className="input small" aria-label="Try" value={tryNumber} onChange={(e) => setTryNumber(Number(e.target.value))}>
              {Array.from({ length: task.try_number }, (_, i) => i + 1).map((n) => <option key={n} value={n}>Try {n}</option>)}
            </select>
          )}
          <button className="btn link small" onClick={() => void load()}>Refresh</button>
        </span>
      </div>
      {error && <div className="banner red" style={{ marginTop: 12 }}>{error}</div>}
      <div className="log" role="log" aria-label="Task log">
        {lines === null && !error && <div className="muted"><span className="spinner" /> Loading the log…</div>}
        {lines?.length === 0 && <div className="muted">The log is empty.</div>}
        {lines?.map((l, i) => (
          <div key={i} className={`line ${LEVEL_TONES[l.level ?? ''] ?? ''}`}>
            <time>{l.at ? l.at.slice(11, 19) : ''}</time>
            <span className="level">{l.level ?? ''}</span>
            <span className="msg">{l.message}</span>
          </div>
        ))}
        <div ref={end} />
      </div>
    </Sheet>
  )
}

function runLabel(r: PipelineRun) {
  return r.run_type === 'scheduled' ? 'Scheduled batch' : 'Manual batch'
}

function runScope(r: PipelineRun) {
  const only = r.conf?.only?.length ?? 0
  const force = r.conf?.force?.length ?? 0
  return [only ? ` · ${only} chosen book(s)` : '', force ? ` · redo ${force}` : ''].join('')
}

function runDuration(r: PipelineRun) {
  if (r.duration != null) return duration(r.duration)
  if (!r.start_date) return ''
  const end = r.end_date ? Date.parse(r.end_date) : Date.now()
  return duration((end - Date.parse(r.start_date)) / 1000)
}

const TASK_ORDER = ['plan', 'reviews', 'books', 'review', 'process.structure', 'process.cover', 'process.summarize',
  'process.index', 'finalize']
const TASK_NAMES: Record<string, string> = {
  plan: 'Plan', reviews: 'List reviews', books: 'List books', review: 'Review prompts',
  'process.structure': 'Find chapters', 'process.cover': 'Find a cover', 'process.summarize': 'Summarize',
  'process.index': 'Index', finalize: 'Finalize',
}
const STEP_NAMES: Record<string, string> = {
  structure: 'Chapters', cover: 'Cover', summarize: 'Summaries', index: 'Index',
}

function orderTasks(tasks: TaskInstance[]) {
  return [...tasks].sort((a, b) => (TASK_ORDER.indexOf(a.task_id) - TASK_ORDER.indexOf(b.task_id)) || a.map_index - b.map_index)
}
