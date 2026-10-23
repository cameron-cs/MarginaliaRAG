import { useCallback, useEffect, useRef, useState, type DragEvent, type FormEvent } from 'react'
import { Link } from 'react-router'

import { api, ApiError, type Book, type Problem } from '../api'
import { Cover, Field, Sheet, StatusPill, useToast } from '../components/ui'
import { useLiveEvents } from '../events'
import { applyBookEvent } from '../progress'
import { stream } from '../session'

export function ShelfPage() {
  const [books, setBooks] = useState<Book[] | null>(null)
  const [progress, setProgress] = useState<Record<string, string>>({})
  const [adding, setAdding] = useState(false)
  const toast = useToast()

  const load = useCallback(() => {
    api.books().then(setBooks).catch((e) => toast(e.message, true))
  }, [toast])
  useEffect(load, [load])

  useLiveEvents(stream, (ev) => {
    if (ev.type !== 'book.status' || !books) return
    const next = applyBookEvent(books, progress, ev)
    setBooks(next.books)
    setProgress(next.progress)
    if (ev.status === 'ready') toast(`${books.find((b) => b.id === ev.user_book_id)?.title ?? 'A book'} is ready.`)
  })

  return (
    <main className="page">
      <header className="hero">
        <div>
          <h1>Your shelf.</h1>
          <p>Add a book, then ask it anything.</p>
        </div>
        {books && books.length > 0 && <button className="btn" onClick={() => setAdding(true)}>Add a book</button>}
      </header>

      {books === null ? <div className="row muted"><span className="spinner" /> Loading your books…</div>
        : books.length === 0 ? (
          <div className="card empty">
            <h2>No books yet.</h2>
            <p className="muted">Upload a PDF with chapters. It is processed in the next batch, and then it is ready for questions.</p>
            <button className="btn large" style={{ marginTop: 20 }} onClick={() => setAdding(true)}>Add your first book</button>
          </div>
        ) : (
          <div className="shelf">
            {books.map((b) => (
              <Link key={b.id} to={`/books/${b.id}`} className="book-tile">
                <Cover title={b.title} author={b.author} src={b.cover_url} />
                <div className="meta">
                  <div className="title">{b.title}</div>
                  {b.author && <div className="author">{b.author}</div>}
                  <div className="row" style={{ marginTop: 8 }}><StatusPill status={b.status} /></div>
                  <div className="progress">{statusLine(b, progress[b.id])}</div>
                </div>
              </Link>
            ))}
            <button className="add-tile" onClick={() => setAdding(true)} aria-label="Add a book">
              <span className="plus">+</span>
              <span className="small">Add a book</span>
            </button>
          </div>
        )}

      {adding && (
        <UploadSheet
          onClose={() => setAdding(false)}
          onAdded={(message) => {
            setAdding(false)
            toast(message)
            load()
          }}
        />
      )}
    </main>
  )
}

function statusLine(b: Book, live?: string) {
  if (live && (b.status === 'checking' || b.status === 'queued')) return live
  if (b.status === 'queued') return 'Waiting for the next batch'
  if (b.status === 'ready') return `${b.chapters} chapters`
  if (b.status === 'rejected') return b.error_code === 'prompt_rejected' ? 'Prompts not accepted' : 'Could not be used'
  return live ?? ''
}

export function UploadSheet({ onClose, onAdded }: { onClose: () => void; onAdded: (message: string) => void }) {
  const [file, setFile] = useState<File | null>(null)
  const [systemPrompt, setSystemPrompt] = useState('')
  const [userPrompt, setUserPrompt] = useState('')
  const [problems, setProblems] = useState<Problem[]>([])
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [over, setOver] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const problem = (field: string) => problems.find((p) => p.field === field)?.message

  function choose(f: File | undefined | null) {
    if (!f) return
    setFile(f)
    setProblems((p) => p.filter((x) => x.field !== 'file'))
  }

  function drop(e: DragEvent) {
    e.preventDefault()
    setOver(false)
    choose(e.dataTransfer.files[0])
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!file) return
    setBusy(true)
    setError(null)
    setProblems([])
    try {
      const res = await api.upload(file, { system_prompt: systemPrompt, user_prompt: userPrompt })
      onAdded(res.message)
    } catch (err) {
      if (err instanceof ApiError && err.problems.length) setProblems(err.problems)
      else if (err instanceof ApiError && err.code === 'duplicate') setError('This book is already on your shelf.')
      else setError(err instanceof Error ? err.message : 'Upload failed.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Sheet title="Add a book" onClose={onClose}>
      <p className="muted small">A PDF with chapters. It is checked now and processed in the next batch.</p>
      <form onSubmit={submit} className="stack" style={{ marginTop: 20 }}>
        <div>
          <div
            className={`drop ${over ? 'over' : ''} ${problem('file') ? 'invalid' : ''}`}
            onClick={() => input.current?.click()}
            onDragOver={(e) => { e.preventDefault(); setOver(true) }}
            onDragLeave={() => setOver(false)}
            onDrop={drop}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && input.current?.click()}
          >
            {file ? <><div className="file">{file.name}</div><div className="muted small">{(file.size / 1048576).toFixed(1)} MB · choose another</div></>
              : <><div className="file">Drop a PDF here</div><div className="muted small">or click to choose one</div></>}
          </div>
          <input ref={input} type="file" accept="application/pdf,.pdf" hidden data-testid="file-input"
            onChange={(e) => choose(e.target.files?.[0])} />
          {problem('file') && <span className="field"><span className="error" role="alert">{problem('file')}</span></span>}
        </div>
        <Field label="Assistant persona" hint="Optional. How answers should sound, for example: Answer like a friendly literature teacher."
          error={problem('system_prompt')}>
          <textarea className={`textarea ${problem('system_prompt') ? 'invalid' : ''}`} value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)} maxLength={2000} />
        </Field>
        <Field label="Instructions for every answer" hint="Optional. For example: Reply in Spanish, in two or three sentences."
          error={problem('user_prompt')}>
          <textarea className={`textarea ${problem('user_prompt') ? 'invalid' : ''}`} value={userPrompt}
            onChange={(e) => setUserPrompt(e.target.value)} maxLength={2000} />
        </Field>
        {error && <div className="banner red" role="alert">{error}</div>}
        <div className="actions">
          <button type="button" className="btn secondary" onClick={onClose}>Cancel</button>
          <button className="btn" disabled={!file || busy}>{busy ? 'Checking…' : 'Add to shelf'}</button>
        </div>
      </form>
    </Sheet>
  )
}
