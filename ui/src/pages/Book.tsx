import { useCallback, useEffect, useState, type FormEvent, type KeyboardEvent } from 'react'
import { useNavigate, useParams } from 'react-router'

import { api, ApiError, type Book, type Question } from '../api'
import { Cover, Field, Sheet, StatusPill, useToast } from '../components/ui'
import { useLiveEvents, type LiveEvent } from '../events'
import { relativeTime } from '../format'
import { applyQuestionEvent, questionSteps, splitCitations } from '../progress'
import { stream, useSession } from '../session'
import { describeSampling, loadSampling, SamplingPanel } from '../components/SamplingPanel'

export function BookPage() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const toast = useToast()
  const [book, setBook] = useState<Book | null>(null)
  const [questions, setQuestions] = useState<Question[]>([])
  const [live, setLive] = useState<Record<string, LiveEvent[]>>({})   // question id -> its progress events
  const [bookMessage, setBookMessage] = useState<string>('')
  const [editing, setEditing] = useState(false)
  const [missing, setMissing] = useState(false)

  const load = useCallback(async () => {
    try {
      const [b, q] = await Promise.all([api.book(id), api.questions(id)])
      setBook(b)
      setQuestions(q)
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) setMissing(true)
      else toast((e as Error).message, true)
    }
  }, [id, toast])
  useEffect(() => void load(), [load])

  useLiveEvents(stream, (ev) => {
    if (ev.type === 'book.status' && ev.user_book_id === id) {
      setBookMessage(ev.message ?? '')
      if (['ready', 'rejected', 'failed'].includes(ev.status ?? '')) void load()
      else if (ev.status && book) setBook({ ...book, status: ev.status as Book['status'] })
    }
    if (ev.question_id && questions.some((q) => q.id === ev.question_id)) {
      setLive((l) => ({ ...l, [ev.question_id!]: [...(l[ev.question_id!] ?? []), ev] }))
      setQuestions((qs) => applyQuestionEvent(qs, ev))
    }
  })

  if (missing) return <main className="page page-narrow"><div className="card empty"><h2>Book not found.</h2></div></main>
  if (!book) return <main className="page"><div className="row muted"><span className="spinner" /> Loading…</div></main>

  async function remove() {
    if (!confirm(`Remove “${book!.title}” from your shelf?`)) return
    await api.removeBook(book!.id)
    toast('Removed from your shelf.')
    navigate('/')
  }

  return (
    <main className="page">
      <header className="book-head">
        <Cover title={book.title} author={book.author} src={book.cover_url} large />
        <div style={{ flex: 1, minWidth: 260 }}>
          <StatusPill status={book.status} />
          <h1>{book.title}</h1>
          {book.author && <div className="by">{book.author}</div>}
          <p className="muted small" style={{ marginTop: 12 }}>{bookMessage || describe(book)}</p>
        </div>
      </header>

      {book.status === 'ready'
        ? <Ask book={book} onAsked={(q) => setQuestions((qs) => [q, ...qs])} />
        : <StatusCard book={book} onEdit={() => setEditing(true)} />}

      {questions.length > 0 && (
        <section className="thread" aria-label="Questions">
          {questions.map((q) => (
            <QA key={q.id} q={q} events={live[q.id] ?? []} onAskAgain={async () => {
              try {
                const fresh = await api.ask(book.id, q.text, true, q.details.sampling)
                setQuestions((qs) => [fresh, ...qs])
              } catch (e) {
                toast((e as Error).message, true)
              }
            }} />
          ))}
        </section>
      )}

      {book.structure && book.structure.chapters.length > 0 && <Contents book={book} />}

      <section className="card" style={{ marginTop: 28 }}>
        <div className="spread">
          <div>
            <h3>Assistant</h3>
            <p className="muted small" style={{ margin: 0 }}>{promptSummary(book)}</p>
          </div>
          <button className="btn secondary small" onClick={() => setEditing(true)}>Edit prompts</button>
        </div>
        {(book.system_prompt || book.user_prompt) && (
          <div className="stack" style={{ marginTop: 18 }}>
            {book.system_prompt && <blockquote className="muted" style={{ margin: 0 }}>{book.system_prompt}</blockquote>}
            {book.user_prompt && <blockquote className="muted" style={{ margin: 0 }}>{book.user_prompt}</blockquote>}
          </div>
        )}
      </section>

      <div style={{ marginTop: 28, textAlign: 'center' }}>
        <button className="btn danger small" onClick={() => void remove()}>Remove from shelf</button>
      </div>

      {editing && (
        <PromptsSheet book={book} onClose={() => setEditing(false)}
          onSaved={(b, message) => { setBook(b); setEditing(false); toast(message) }} />
      )}
    </main>
  )
}

function describe(b: Book) {
  if (b.status === 'queued') return 'Waiting for the next processing batch.'
  if (b.status === 'checking') return 'Being processed now.'
  if (b.status === 'ready') return `${b.chapters} chapters, ready for your questions.`
  return b.error_message ?? 'This book could not be used.'
}

function promptSummary(b: Book) {
  if (b.prompt_status === 'generated') return 'No prompts were given, so one was written for this book.'
  if (b.prompt_status === 'approved') return 'Your prompts were checked and accepted.'
  if (b.prompt_status === 'rejected') return `Your prompts were not accepted: ${b.prompt_feedback ?? ''}`
  if (b.prompt_status === 'error') return 'Your prompts could not be checked. Save them again to retry.'
  return 'Prompts are checked in the next batch.'
}

function StatusCard({ book, onEdit }: { book: Book; onEdit: () => void }) {
  const tone = book.status === 'rejected' || book.status === 'failed' ? 'red' : 'blue'
  return (
    <div className={`banner ${tone}`}>
      {describe(book)}
      {book.error_code === 'prompt_rejected' && <> <button className="btn link" onClick={onEdit}>Change the prompts</button></>}
    </div>
  )
}

function Ask({ book, onAsked }: { book: Book; onAsked: (q: Question) => void }) {
  const { user } = useSession()
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [sampling, setSampling] = useState(() => (user ? loadSampling(user.id) : {}))
  const [tuning, setTuning] = useState(false)
  const custom = describeSampling(sampling)

  async function submit(e?: FormEvent) {
    e?.preventDefault()
    if (!text.trim()) return
    setBusy(true)
    setError(null)
    try {
      onAsked(await api.ask(book.id, text.trim(), false, sampling))
      setText('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not ask.')
    } finally {
      setBusy(false)
    }
  }

  function onKey(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void submit()
    }
  }

  return (
    <form onSubmit={submit}>
      <div className="composer">
        <textarea className="textarea" rows={1} placeholder={`Ask anything about ${book.title}…`} value={text}
          onChange={(e) => setText(e.target.value)} onKeyDown={onKey} aria-label="Your question" maxLength={1000} />
        <button className="btn" disabled={busy || text.trim().length < 3}>Ask</button>
      </div>
      <div className="row" style={{ marginTop: 8, gap: 8 }}>
        <button type="button" className="btn link small" aria-expanded={tuning} onClick={() => setTuning(!tuning)}>
          {tuning ? 'Hide answer settings' : 'Answer settings'}
        </button>
        {custom && !tuning && <span className="muted small">Custom: {custom}</span>}
      </div>
      {tuning && user && <SamplingPanel userId={user.id} value={sampling} onChange={setSampling} />}
      {error && <div className="banner red" style={{ marginTop: 12 }} role="alert">{error}</div>}
    </form>
  )
}

/** Where a question stands when no live events have arrived for it (say, after a reload). */
function stage(q: Question) {
  if (q.status === 'running') return 'Working on the answer'
  return q.details.screened ? 'Waiting for a free slot' : 'Checking the question'
}

function QA({ q, events, onAskAgain }: { q: Question; events: LiveEvent[]; onAskAgain: () => void }) {
  const steps = questionSteps(events)
  const working = q.status === 'queued' || q.status === 'running'
  const cached = q.status === 'done' && q.details.cached
  return (
    <article className="qa">
      <div className="q">{q.text}</div>
      {working && (
        <ul className="steps" aria-live="polite">
          {(steps.length ? steps : [{ key: 'now', label: stage(q), state: 'active' as const }]).map((s, i) => (
            <li key={i} className={s.state}>
              {s.state === 'active' && <span className="spinner" />}
              {s.key === 'searching' && s.detail ? s.detail.replace(/\.\.\.$/, '') : s.label}
            </li>
          ))}
        </ul>
      )}
      {q.status === 'done' && q.answer && (
        <div className="a">
          {splitCitations(q.answer).map((p, i) => p.citation ? <span key={i} className="cite">{p.citation}</span> : <span key={i}>{p.text}</span>)}
        </div>
      )}
      {q.status === 'failed' && <div className="banner red" style={{ marginTop: 12 }}>{q.error ?? 'Something went wrong. Please ask again.'}</div>}
      {q.status === 'refused' && <div className="banner" style={{ marginTop: 12 }} role="note">{q.error}</div>}
      <div className="when">
        {relativeTime(q.created_at)}
        {q.status === 'done' && q.details.supported === true && ' · Checked against the book'}
        {q.status === 'done' && q.details.supported === false && ' · Some of this may not be in the book'}
        {cached && <> · Answered earlier <button className="btn link small" onClick={onAskAgain}>Ask again</button></>}
        {describeSampling(q.details.sampling) && ` · ${describeSampling(q.details.sampling)}`}
      </div>
    </article>
  )
}

function Contents({ book }: { book: Book }) {
  const s = book.structure!
  const byPart = s.parts.map((p) => ({ ...p, chapters: s.chapters.filter((c) => c.part === p.part) }))
  return (
    <section className="card" style={{ marginTop: 28 }}>
      <h3>Contents</h3>
      <ul className="chapters">
        {byPart.map((p) => (
          <li key={p.part}>
            {p.label && <div className="part">{p.label}</div>}
            {p.chapters.map((c) => (
              <div key={c.label}>{c.label.replace(`${p.label}, `, '')}{c.title && <span className="muted"> · {c.title}</span>}</div>
            ))}
          </li>
        ))}
      </ul>
    </section>
  )
}

function PromptsSheet({ book, onClose, onSaved }: { book: Book; onClose: () => void; onSaved: (b: Book, message: string) => void }) {
  const [systemPrompt, setSystemPrompt] = useState(book.prompt_status === 'generated' ? '' : book.system_prompt ?? '')
  const [userPrompt, setUserPrompt] = useState(book.user_prompt ?? '')
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setErrors({})
    try {
      const res = await api.setPrompts(book.id, systemPrompt.trim() || null, userPrompt.trim() || null)
      onSaved(res.book, res.message)
    } catch (err) {
      if (err instanceof ApiError && err.problems.length) setErrors(Object.fromEntries(err.problems.map((p) => [p.field, p.message])))
      else setErrors({ form: err instanceof Error ? err.message : 'Could not save.' })
    } finally {
      setBusy(false)
    }
  }

  return (
    <Sheet title="Prompts" onClose={onClose}>
      <p className="muted small">They shape how answers sound. Leave both empty and one will be written for this book.</p>
      <form onSubmit={submit} className="stack" style={{ marginTop: 20 }}>
        <Field label="Assistant persona" error={errors.system_prompt}>
          <textarea className="textarea" value={systemPrompt} onChange={(e) => setSystemPrompt(e.target.value)} maxLength={2000} />
        </Field>
        <Field label="Instructions for every answer" error={errors.user_prompt}>
          <textarea className="textarea" value={userPrompt} onChange={(e) => setUserPrompt(e.target.value)} maxLength={2000} />
        </Field>
        {errors.form && <div className="banner red">{errors.form}</div>}
        <div className="actions">
          <button type="button" className="btn secondary" onClick={onClose}>Cancel</button>
          <button className="btn" disabled={busy}>{busy ? 'Saving…' : 'Save'}</button>
        </div>
      </form>
    </Sheet>
  )
}
