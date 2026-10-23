// Live events over the websocket (/api/ws). The first message authenticates; the server then
// pushes book.status, question.* and (for admins) pipeline.task events. The connection comes
// back on its own after drops, with backoff, and renews the token if the server refuses it.

import { useEffect, useRef } from 'react'

export interface LiveEvent {
  type: string
  status?: string
  state?: string
  message?: string
  user_book_id?: string
  question_id?: string
  book_id?: string
  run_id?: string
  task?: string
  answer?: string
  citations?: string[]
  supported?: boolean | null
  cached?: { kind: 'exact' | 'semantic'; similarity: number }
  code?: string
  error_code?: string
  at: string
  [key: string]: unknown
}

type Listener = (event: LiveEvent) => void
export type StreamState = 'idle' | 'connecting' | 'open' | 'closed'

const UNAUTHORIZED = 4401

export class EventStream {
  private ws: WebSocket | null = null
  private listeners = new Set<Listener>()
  private stateListeners = new Set<(s: StreamState) => void>()
  private retry = 0
  private timer: ReturnType<typeof setTimeout> | null = null
  private wanted = false
  state: StreamState = 'idle'

  constructor(
    private readonly getToken: () => string | null,
    private readonly renew: () => Promise<boolean>,
    private readonly url = () => `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/api/ws`,
    private readonly Socket: typeof WebSocket = WebSocket,
  ) {}

  subscribe(fn: Listener) {
    this.listeners.add(fn)
    return () => {
      this.listeners.delete(fn)
    }
  }

  onState(fn: (s: StreamState) => void) {
    this.stateListeners.add(fn)
    return () => {
      this.stateListeners.delete(fn)
    }
  }

  private setState(s: StreamState) {
    this.state = s
    this.stateListeners.forEach((fn) => fn(s))
  }

  start() {
    this.wanted = true
    if (!this.ws) this.connect()
  }

  stop() {
    this.wanted = false
    if (this.timer) clearTimeout(this.timer)
    this.ws?.close()
    this.ws = null
    this.setState('idle')
  }

  /** Reconnect now, for example after signing in as someone else. */
  restart() {
    this.stop()
    this.start()
  }

  private connect() {
    const token = this.getToken()
    if (!token) return
    this.setState('connecting')
    const ws = new this.Socket(this.url())
    this.ws = ws
    ws.onopen = () => ws.send(JSON.stringify({ type: 'auth', token }))
    ws.onmessage = (msg) => {
      const event = JSON.parse(String(msg.data)) as LiveEvent
      if (event.type === 'ready') {
        this.retry = 0
        this.setState('open')
        return
      }
      this.listeners.forEach((fn) => fn(event))
    }
    ws.onclose = async (ev) => {
      if (this.ws !== ws) return
      this.ws = null
      this.setState('closed')
      if (!this.wanted) return
      if (ev.code === UNAUTHORIZED && !(await this.renew())) return // signed out
      const delay = Math.min(30_000, 1000 * 2 ** this.retry++)
      this.timer = setTimeout(() => this.connect(), ev.code === UNAUTHORIZED ? 0 : delay)
    }
  }
}

/** Call `listener` for every live event while the component is mounted. */
export function useLiveEvents(stream: EventStream, listener: Listener) {
  const ref = useRef(listener)
  ref.current = listener
  useEffect(() => stream.subscribe((e) => ref.current(e)), [stream])
}
