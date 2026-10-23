import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Link, NavLink } from 'react-router'

import type { BookStatus } from '../api'
import { coverColors } from '../format'
import { stream, useSession } from '../session'

// --- navigation ------------------------------------------------------------------------------

export function Nav() {
  const { user, signOut } = useSession()
  const [live, setLive] = useState(stream.state)
  useEffect(() => stream.onState(setLive), [])
  return (
    <nav className="nav" aria-label="Main">
      <Link to="/" className="brand"><Logo /> MarginaliaRAG</Link>
      <div className="links">
        {user && <NavLink to="/" end>Shelf</NavLink>}
        {user?.role === 'admin' && <NavLink to="/admin">Admin</NavLink>}
      </div>
      {user && (
        <div className="who">
          <span className={`live-dot ${live === 'open' ? 'open' : ''}`} title={live === 'open' ? 'Live updates on' : 'Reconnecting…'} />
          <span className="email">{user.email}</span>
          <button className="btn link" onClick={() => void signOut()}>Sign out</button>
        </div>
      )}
    </nav>
  )
}

export function Logo({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <rect width="32" height="32" rx="8" fill="var(--accent)" />
      <path d="M9 8h9a5 5 0 0 1 5 5v11H14a5 5 0 0 1-5-5z" fill="#fff" />
    </svg>
  )
}

// --- status ----------------------------------------------------------------------------------

const STATUS: Record<BookStatus, [string, string]> = {
  queued: ['Queued', ''],
  checking: ['Processing', 'blue'],
  ready: ['Ready', 'green'],
  rejected: ['Not accepted', 'red'],
  failed: ['Failed', 'red'],
}

export function StatusPill({ status }: { status: BookStatus }) {
  const [label, tone] = STATUS[status] ?? [status, '']
  return (
    <span className={`pill ${tone}`}>
      {status === 'checking' && <span className="pulse" />}
      {label}
    </span>
  )
}

const RUN_TONES: Record<string, string> = {
  success: 'green', running: 'blue', queued: '', scheduled: '', failed: 'red', upstream_failed: 'orange',
  skipped: '', up_for_retry: 'orange', removed: '', deferred: 'blue', restarting: 'orange',
  // library book statuses, shown with the same pill
  ready: 'green', processing: 'blue', uploaded: '', rejected: 'red',
}

export function StatePill({ state }: { state: string | null }) {
  const s = state ?? 'none'
  return (
    <span className={`pill ${RUN_TONES[s] ?? ''}`}>
      {(s === 'running' || s === 'processing') && <span className="pulse" />}
      {s.replace(/_/g, ' ')}
    </span>
  )
}

// --- covers ----------------------------------------------------------------------------------

/** The book's cover image when it has one (a signed link from the API), else a placeholder in
 *  the same colours the server uses for generated covers. */
export function Cover({ title, author, src, large = false }:
  { title: string; author?: string | null; src?: string | null; large?: boolean }) {
  const [broken, setBroken] = useState(false)
  const [a, b] = coverColors(title)
  if (src && !broken)
    return (
      <div className={`cover image ${large ? 'large' : ''}`} aria-hidden="true">
        <img src={src} alt="" loading="lazy" onError={() => setBroken(true)} />
      </div>
    )
  return (
    <div className={`cover ${large ? 'large' : ''}`} style={{ background: `linear-gradient(150deg, ${a}, ${b})` }} aria-hidden="true">
      <div className="ct">{title}</div>
      {author && <div className="ca">{author}</div>}
    </div>
  )
}

// --- sheets ----------------------------------------------------------------------------------

export function Sheet({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    document.addEventListener('keydown', onKey)
    ref.current?.querySelector<HTMLElement>('input, textarea, button')?.focus()
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])
  // On <body>: an animated or transformed ancestor would make `position: fixed` relative to itself.
  return createPortal(
    <div className="scrim" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="sheet" role="dialog" aria-modal="true" aria-label={title} ref={ref}>
        <h2>{title}</h2>
        {children}
      </div>
    </div>,
    document.body,
  )
}

export function Field({ label, hint, error, children }: { label: string; hint?: string; error?: string; children: ReactNode }) {
  return (
    <div className="field">
      <label>
        <span className="label">{label}</span>
        {children}
      </label>
      {error ? <span className="error" role="alert">{error}</span> : hint && <span className="hint">{hint}</span>}
    </div>
  )
}

export function Switch({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <span className="switch">
      <input type="checkbox" role="switch" aria-label={label} checked={checked} disabled={disabled}
        onChange={(e) => onChange(e.target.checked)} />
      <span />
    </span>
  )
}

// --- toasts ----------------------------------------------------------------------------------

interface Toast { id: number; text: string; error?: boolean }
const ToastContext = createContext<(text: string, error?: boolean) => void>(() => {})

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const show = useCallback((text: string, error = false) => {
    const id = Date.now() + Math.random()
    setToasts((t) => [...t, { id, text, error }])
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), error ? 7000 : 4500)
  }, [])
  return (
    <ToastContext.Provider value={show}>
      {children}
      <div className="toasts" aria-live="polite">
        {toasts.map((t) => <div key={t.id} className={`toast ${t.error ? 'error' : ''}`}>{t.text}</div>)}
      </div>
    </ToastContext.Provider>
  )
}

export const useToast = () => useContext(ToastContext)
