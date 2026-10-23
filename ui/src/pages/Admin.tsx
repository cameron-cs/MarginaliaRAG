import { useCallback, useEffect, useState } from 'react'
import { NavLink, useParams } from 'react-router'

import { api, type LibraryBook, type User } from '../api'
import { Cover, StatePill, Switch, useToast } from '../components/ui'
import { useLiveEvents } from '../events'
import { relativeTime } from '../format'
import { stream, useSession } from '../session'
import { PipelineTab } from './AdminPipeline'
import { QuestionsTab } from './AdminQuestions'

export function AdminPage() {
  const { tab = 'pipeline' } = useParams()
  const [links, setLinks] = useState<{ grafana_url: string | null; evidently_url: string | null }>(
    { grafana_url: null, evidently_url: null })
  useEffect(() => {
    api.observability().then(setLinks).catch(() => undefined)
  }, [])
  return (
    <main className="page">
      <header className="hero">
        <div>
          <h1>Admin.</h1>
          <p>Batches, books, questions and people.</p>
        </div>
        <div className="row">
          {links.grafana_url && (
            <a className="btn secondary small" href={links.grafana_url} target="_blank" rel="noopener noreferrer">Dashboards ↗</a>
          )}
          {links.evidently_url && (
            <a className="btn secondary small" href={links.evidently_url} target="_blank" rel="noopener noreferrer">LLM monitoring ↗</a>
          )}
        </div>
        <nav className="segmented" aria-label="Admin sections">
          <NavLink to="/admin" end className={tab === 'pipeline' ? 'on' : ''}>Pipeline</NavLink>
          <NavLink to="/admin/books" className={tab === 'books' ? 'on' : ''}>Books</NavLink>
          <NavLink to="/admin/questions" className={tab === 'questions' ? 'on' : ''}>Questions</NavLink>
          <NavLink to="/admin/users" className={tab === 'users' ? 'on' : ''}>Users</NavLink>
        </nav>
      </header>
      {tab === 'books' ? <BooksTab /> : tab === 'users' ? <UsersTab /> : tab === 'questions' ? <QuestionsTab />
        : <PipelineTab />}
    </main>
  )
}

// --- books -----------------------------------------------------------------------------------

const COVER_SOURCES: Record<string, string> = { pdf: 'from the PDF', openlibrary: 'Open Library', generated: 'generated' }

function BooksTab() {
  const toast = useToast()
  const [books, setBooks] = useState<LibraryBook[] | null>(null)
  const load = useCallback(() => void api.libraryBooks().then(setBooks).catch((e) => toast(e.message, true)), [toast])
  useEffect(load, [load])
  useLiveEvents(stream, (ev) => ev.type === 'pipeline.task' && ev.task === 'finalize' && load())

  async function redo(b: LibraryBook) {
    if (!confirm(`Redo “${b.title}” from scratch in a batch started now?`)) return
    try {
      const r = await api.reingest(b.book_id)
      toast(r.message)
    } catch (e) {
      toast((e as Error).message, true)
    }
  }

  if (!books) return <div className="row muted"><span className="spinner" /> Loading books…</div>
  return (
    <div className="card flush">
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>Book</th><th>Status</th><th className="num">Chapters</th><th className="num">Readers</th>
            <th className="num">Questions</th><th>Updated</th><th /></tr></thead>
          <tbody>
            {books.length === 0 && <tr><td colSpan={7} className="muted">No books uploaded yet.</td></tr>}
            {books.map((b) => (
              <tr key={b.book_id}>
                <td>
                  <div className="row" style={{ alignItems: 'center', gap: 12 }}>
                    <div className="thumb"><Cover title={b.title} src={b.cover_url} /></div>
                    <div>
                      <div style={{ fontWeight: 600 }}>{b.title}</div>
                      <div className="muted small">{b.author ?? b.book_id}{b.cover_source && ` · cover: ${COVER_SOURCES[b.cover_source] ?? b.cover_source}`}</div>
                      {b.error_message && <div className="small" style={{ color: 'var(--red)' }}>{b.error_message}</div>}
                    </div>
                  </div>
                </td>
                <td><StatePill state={b.status} /></td>
                <td className="num">{b.chapters || ''}</td>
                <td className="num">{b.readers}</td>
                <td className="num">{b.questions}{b.refused > 0 && <div className="muted small">{b.refused} refused</div>}</td>
                <td className="muted small">{relativeTime(b.updated_at)}</td>
                <td className="num"><button className="btn secondary small" onClick={() => void redo(b)}>Redo</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// --- users -----------------------------------------------------------------------------------

function UsersTab() {
  const toast = useToast()
  const { user: me } = useSession()
  const [users, setUsers] = useState<User[] | null>(null)
  useEffect(() => void api.users().then(setUsers).catch((e) => toast(e.message, true)), [toast])

  async function patch(u: User, change: { role?: User['role']; is_active?: boolean }) {
    try {
      const updated = await api.patchUser(u.id, change)
      setUsers((list) => list!.map((x) => (x.id === u.id ? updated : x)))
    } catch (e) {
      toast((e as Error).message, true)
    }
  }

  if (!users) return <div className="row muted"><span className="spinner" /> Loading users…</div>
  return (
    <div className="card flush">
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>Email</th><th>Joined</th><th>Admin</th><th>Active</th></tr></thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id}>
                <td>{u.email}{u.id === me?.id && <span className="muted small"> · you</span>}</td>
                <td className="muted small">{relativeTime(u.created_at)}</td>
                <td><Switch label={`Admin: ${u.email}`} checked={u.role === 'admin'} disabled={u.id === me?.id}
                  onChange={(on) => void patch(u, { role: on ? 'admin' : 'user' })} /></td>
                <td><Switch label={`Active: ${u.email}`} checked={u.is_active} disabled={u.id === me?.id}
                  onChange={(on) => void patch(u, { is_active: on })} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
