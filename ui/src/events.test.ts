import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { EventStream, type LiveEvent } from './events'

class FakeSocket {
  static all: FakeSocket[] = []
  sent: string[] = []
  closed = false
  onopen: (() => void) | null = null
  onmessage: ((m: { data: string }) => void) | null = null
  onclose: ((e: { code: number }) => void) | null = null
  constructor(readonly url: string) {
    FakeSocket.all.push(this)
  }
  send(data: string) {
    this.sent.push(data)
  }
  close() {
    this.closed = true
  }
  // test helpers
  open() {
    this.onopen?.()
  }
  push(event: object) {
    this.onmessage?.({ data: JSON.stringify(event) })
  }
  drop(code = 1006) {
    this.onclose?.({ code })
  }
}

let token: string | null
const renew = vi.fn(async () => true)
const make = () => new EventStream(() => token, renew, () => 'ws://test/api/ws', FakeSocket as unknown as typeof WebSocket)
const last = () => FakeSocket.all[FakeSocket.all.length - 1]

beforeEach(() => {
  vi.useFakeTimers()
  FakeSocket.all = []
  token = 't1'
  renew.mockReset().mockResolvedValue(true)
})
afterEach(() => vi.useRealTimers())

describe('EventStream', () => {
  it('authenticates with the first message and then delivers events', () => {
    const stream = make()
    const got: LiveEvent[] = []
    const states: string[] = []
    stream.subscribe((e) => got.push(e))
    stream.onState((s) => states.push(s))
    stream.start()
    last().open()
    expect(JSON.parse(last().sent[0])).toEqual({ type: 'auth', token: 't1' })
    last().push({ type: 'ready' })
    last().push({ type: 'book.status', status: 'ready', at: 'now' })
    expect(got.map((e) => e.type)).toEqual(['book.status'])
    expect(states).toEqual(['connecting', 'open'])
  })

  it('does not connect without a token', () => {
    token = null
    make().start()
    expect(FakeSocket.all).toHaveLength(0)
  })

  it('reconnects with growing backoff after a drop', async () => {
    const stream = make()
    stream.start()
    last().drop()
    await vi.advanceTimersByTimeAsync(999)
    expect(FakeSocket.all).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(FakeSocket.all).toHaveLength(2)
    last().drop()
    await vi.advanceTimersByTimeAsync(1999)
    expect(FakeSocket.all).toHaveLength(2)
    await vi.advanceTimersByTimeAsync(1)
    expect(FakeSocket.all).toHaveLength(3)
  })

  it('resets the backoff once the server says ready', async () => {
    const stream = make()
    stream.start()
    last().drop()
    await vi.advanceTimersByTimeAsync(1000)
    last().push({ type: 'ready' })
    last().drop()
    await vi.advanceTimersByTimeAsync(1000)
    expect(FakeSocket.all).toHaveLength(3)
  })

  it('renews the token when the server refuses it, then reconnects at once', async () => {
    const stream = make()
    stream.start()
    renew.mockImplementation(async () => {
      token = 't2'
      return true
    })
    last().drop(4401)
    await vi.advanceTimersByTimeAsync(0)
    expect(renew).toHaveBeenCalledOnce()
    expect(FakeSocket.all).toHaveLength(2)
    last().open()
    expect(JSON.parse(last().sent[0]).token).toBe('t2')
  })

  it('gives up when the token cannot be renewed', async () => {
    const stream = make()
    stream.start()
    renew.mockResolvedValue(false)
    last().drop(4401)
    await vi.advanceTimersByTimeAsync(60_000)
    expect(FakeSocket.all).toHaveLength(1)
  })

  it('stays closed after stop', async () => {
    const stream = make()
    stream.start()
    const ws = last()
    stream.stop()
    expect(ws.closed).toBe(true)
    ws.drop()
    await vi.advanceTimersByTimeAsync(60_000)
    expect(FakeSocket.all).toHaveLength(1)
    expect(stream.state).toBe('idle')
  })
})
