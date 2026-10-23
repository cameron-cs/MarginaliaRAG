// Who is signed in, plus the one live event stream. On load the refresh cookie (if any) signs
// the user back in silently; until then `ready` is false and pages wait.

import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router'

import { api, type User } from './api'
import { EventStream } from './events'

export const stream = new EventStream(() => api.accessToken, () => api.refresh())

interface Session {
  user: User | null
  ready: boolean
  signIn: (email: string, password: string) => Promise<User>
  signUp: (email: string, password: string) => Promise<User>
  signOut: () => Promise<void>
}

const SessionContext = createContext<Session | null>(null)

export function SessionProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [ready, setReady] = useState(false)

  useEffect(() => {
    const off = api.onAuthChange((u) => {
      setUser(u)
      if (u) stream.restart()
      else stream.stop()
    })
    void api.refresh().finally(() => setReady(true))
    return () => {
      off()
    }
  }, [])

  const value = useMemo<Session>(() => ({
    user,
    ready,
    signIn: (email, password) => api.login(email, password),
    signUp: async (email, password) => {
      await api.signup(email, password)
      return api.login(email, password)
    },
    signOut: () => api.logout(),
  }), [user, ready])

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
}

export function useSession() {
  const s = useContext(SessionContext)
  if (!s) throw new Error('useSession outside SessionProvider')
  return s
}

/** Pages for signed-in users; admins only with `admin`. */
export function Protected({ children, admin = false }: { children: ReactNode; admin?: boolean }) {
  const { user, ready } = useSession()
  const location = useLocation()
  if (!ready) return <div className="page-loading" aria-busy="true" />
  if (!user) return <Navigate to="/signin" replace state={{ from: location.pathname }} />
  if (admin && user.role !== 'admin') return <Navigate to="/" replace />
  return <>{children}</>
}
