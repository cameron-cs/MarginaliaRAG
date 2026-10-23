import { useState, type FormEvent } from 'react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router'

import { ApiError } from '../api'
import { Field, Logo } from '../components/ui'
import { useSession } from '../session'

export function AuthPage({ mode }: { mode: 'signin' | 'signup' }) {
  const { user, ready, signIn, signUp } = useSession()
  const navigate = useNavigate()
  const location = useLocation()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const signingUp = mode === 'signup'

  if (ready && user) return <Navigate to="/" replace />

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await (signingUp ? signUp : signIn)(email, password)
      navigate((location.state as { from?: string } | null)?.from ?? '/', { replace: true })
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) setError('An account with this email already exists.')
      else if (err instanceof ApiError && err.status === 422) setError('Enter a valid email and a password of at least 8 characters.')
      else if (err instanceof ApiError && err.status === 429) setError('Too many attempts. Try again in a minute.')
      else setError(err instanceof Error ? err.message : 'Something went wrong.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="auth">
      <div className="card">
        <div style={{ display: 'flex', justifyContent: 'center', marginBottom: 18 }}><Logo size={44} /></div>
        <h1>{signingUp ? 'Create your account' : 'Sign in'}</h1>
        <p className="sub">{signingUp ? 'Add your books and ask them anything.' : 'Welcome back to your shelf.'}</p>
        <form onSubmit={submit} noValidate>
          <Field label="Email">
            <input className="input" type="email" autoComplete="email" required value={email}
              onChange={(e) => setEmail(e.target.value)} />
          </Field>
          <Field label="Password" hint={signingUp ? 'At least 8 characters.' : undefined}>
            <input className="input" type="password" autoComplete={signingUp ? 'new-password' : 'current-password'}
              required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} />
          </Field>
          {error && <div className="banner red" role="alert">{error}</div>}
          <button className="btn large" disabled={busy || !email || !password}>
            {busy ? 'One moment…' : signingUp ? 'Create account' : 'Sign in'}
          </button>
        </form>
        <p className="switch-link muted">
          {signingUp ? <>Already have an account? <Link to="/signin">Sign in</Link></>
            : <>New here? <Link to="/signup">Create an account</Link></>}
        </p>
      </div>
    </main>
  )
}
