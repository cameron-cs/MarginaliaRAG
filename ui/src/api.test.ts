import { afterEach, describe, expect, it, vi } from 'vitest'

import { Api, ApiError } from './api'

const json = (status: number, body: unknown) =>
  new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

const session = (token: string) => ({
  access_token: token, expires_in: 900,
  user: { id: 'u1', email: 'a@b.c', role: 'user', is_active: true, created_at: '2026-01-01T00:00:00Z' },
})

afterEach(() => vi.useRealTimers())

describe('ApiError.from', () => {
  it('reads the API error envelope with its problems', async () => {
    const e = await ApiError.from(json(422, {
      error: { code: 'invalid_upload', message: 'Not accepted', problems: [{ field: 'file', code: 'not_pdf', message: 'Only PDF' }] },
    }))
    expect(e.status).toBe(422)
    expect(e.code).toBe('invalid_upload')
    expect(e.message).toBe('Not accepted')
    expect(e.problems).toEqual([{ field: 'file', code: 'not_pdf', message: 'Only PDF' }])
  })

  it('reads a string detail', async () => {
    const e = await ApiError.from(json(401, { detail: 'Invalid token' }))
    expect([e.status, e.message, e.code]).toEqual([401, 'Invalid token', null])
  })

  it('reads the first validation error', async () => {
    const e = await ApiError.from(json(422, { detail: [{ msg: 'field required', loc: ['body', 'text'] }] }))
    expect([e.message, e.code]).toEqual(['field required', 'invalid'])
  })

  it('falls back to the status text when the body is not JSON', async () => {
    const e = await ApiError.from(new Response('oops', { status: 502, statusText: 'Bad Gateway' }))
    expect([e.status, e.message]).toEqual([502, 'Bad Gateway'])
  })
})

describe('Api', () => {
  it('sends the bearer token and JSON body under /api', async () => {
    const fetch = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/api/auth/login') return json(200, session('t1'))
      return json(200, { url, init })
    })
    const api = new Api(fetch as unknown as typeof globalThis.fetch)
    await api.login('a@b.c', 'secret123')
    await api.ask('book-1', 'Who is Raskolnikov?')
    const [url, init] = fetch.mock.calls[1]
    expect(url).toBe('/api/books/book-1/questions')
    expect(init!.method).toBe('POST')
    expect((init!.headers as Record<string, string>).Authorization).toBe('Bearer t1')
    expect(JSON.parse(String(init!.body))).toEqual({ text: 'Who is Raskolnikov?', fresh: false })
  })

  it('refreshes once on 401 and retries the request with the new token', async () => {
    let token = 'old'
    const fetch = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/api/auth/login') return json(200, session('old'))
      if (url === '/api/auth/refresh') { token = 'new'; return json(200, session('new')) }
      const auth = (init!.headers as Record<string, string>).Authorization
      return auth === `Bearer ${token}` && token === 'new' ? json(200, []) : json(401, { detail: 'expired' })
    })
    const api = new Api(fetch as unknown as typeof globalThis.fetch)
    await api.login('a@b.c', 'secret123')
    expect(await api.books()).toEqual([])
    expect(fetch.mock.calls.map((c) => c[0])).toEqual(['/api/auth/login', '/api/books', '/api/auth/refresh', '/api/books'])
    expect(api.accessToken).toBe('new')
  })

  it('signs out when the refresh fails', async () => {
    const fetch = vi.fn(async (url: string) => (url === '/api/auth/refresh' ? json(401, { detail: 'no cookie' }) : json(401, { detail: 'expired' })))
    const api = new Api(fetch as unknown as typeof globalThis.fetch)
    const changes: unknown[] = []
    api.onAuthChange((u) => changes.push(u))
    await expect(api.books()).rejects.toMatchObject({ status: 401 })
    expect(changes).toEqual([null])
    expect(api.user).toBeNull()
  })

  it('shares one refresh between concurrent callers', async () => {
    let release!: () => void
    const gate = new Promise<void>((r) => (release = r))
    const fetch = vi.fn(async () => {
      await gate
      return json(200, session('t'))
    })
    const api = new Api(fetch as unknown as typeof globalThis.fetch)
    const both = Promise.all([api.refresh(), api.refresh()])
    release()
    expect(await both).toEqual([true, true])
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('does not retry auth endpoints on 401', async () => {
    const fetch = vi.fn(async () => json(401, { detail: 'Wrong email or password' }))
    const api = new Api(fetch as unknown as typeof globalThis.fetch)
    await expect(api.login('a@b.c', 'nope')).rejects.toMatchObject({ message: 'Wrong email or password' })
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('renews the token a minute before it expires', async () => {
    vi.useFakeTimers()
    const fetch = vi.fn(async (url: string) => json(200, session(url.endsWith('login') ? 'a' : 'b')))
    const api = new Api(fetch as unknown as typeof globalThis.fetch)
    await api.login('a@b.c', 'secret123')
    await vi.advanceTimersByTimeAsync(839_000)
    expect(fetch).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(2_000)
    expect(fetch.mock.calls[1][0]).toBe('/api/auth/refresh')
    expect(api.accessToken).toBe('b')
  })

  it('uploads the file and only the prompts that were given', async () => {
    const fetch = vi.fn(async () => json(201, { message: 'ok', next_batch_at: null, book: {} }))
    const api = new Api(fetch as unknown as typeof globalThis.fetch)
    await api.upload(new File(['%PDF'], 'b.pdf', { type: 'application/pdf' }), { system_prompt: '  ', user_prompt: ' Be brief. ' })
    const form = (fetch.mock.calls[0] as unknown as [string, RequestInit])[1].body as FormData
    expect((form.get('file') as File).name).toBe('b.pdf')
    expect(form.has('system_prompt')).toBe(false)
    expect(form.get('user_prompt')).toBe('Be brief.')
  })
})
