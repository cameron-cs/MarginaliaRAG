// The backend API (/api/...). The access token lives only in memory; the refresh token is an
// HttpOnly cookie the browser sends to /api/auth/refresh, so a reload signs the user back in
// without the token ever being readable by scripts.

export type Role = 'user' | 'admin'
export type BookStatus = 'queued' | 'checking' | 'ready' | 'rejected' | 'failed'

export interface User {
  id: string
  email: string
  role: Role
  is_active: boolean
  created_at: string
}

export interface ChapterInfo {
  label: string
  title: string | null
  part: number
  chapter: number
}

export interface Book {
  id: string
  book_id: string
  title: string
  author: string | null
  filename: string
  status: BookStatus
  library_status: string
  prompt_status: string
  error_code: string | null
  error_message: string | null
  prompt_feedback: string | null
  system_prompt: string | null
  user_prompt: string | null
  chapters: number
  created_at: string
  structure?: { parts: { part: number; label: string; chapters: number }[]; chapters: ChapterInfo[] } | null
  cover_url?: string | null
  cover_source?: 'pdf' | 'openlibrary' | 'generated' | null
}

/** The reader's sampling settings for one question; anything left out is the server's default. */
export interface Sampling {
  temperature?: number
  top_p?: number
  top_k?: number
  max_searches?: number
  seed?: number
}

export interface SamplingOptions {
  defaults: { temperature: number; top_p: number; top_k: number; max_searches: number }
  limits: Record<keyof Sampling, { min: number | null; max: number | null; description: string }>
}

export interface Question {
  id: string
  book: string
  text: string
  // refused: the question guard stopped it (error says why); done may come from the answer cache
  status: 'queued' | 'running' | 'done' | 'failed' | 'refused'
  answer: string | null
  citations: string[]
  details: {
    supported?: boolean | null
    revisions?: number
    tool_calls?: { name: string }[]
    cached?: { kind: 'exact' | 'semantic'; similarity: number }
    refused?: 'injection' | 'not_in_book' | 'off_topic'
    screened?: boolean
    sampling?: Sampling
  }
  error: string | null
  created_at: string
  finished_at: string | null
}

export interface LibraryBook {
  book_id: string
  title: string
  author: string | null
  status: string
  chapters: number
  error_code: string | null
  error_message: string | null
  readers: number
  questions: number
  refused: number
  cover_url: string | null
  cover_source: string | null
  updated_at: string
}

export interface PipelineRun {
  run_id: string
  state: string
  run_type?: string
  start_date?: string | null
  end_date?: string | null
  duration?: number | null
  note?: string | null
  conf?: { force?: string[]; only?: string[] }
}

export interface Pipeline {
  paused: boolean
  schedule: string | null
  next_run_at: string | null
  import_errors: boolean
  runs: PipelineRun[]
  total: number
  summary: { by_state: Record<string, number>; mean_duration: number | null; longest: number | null }
}

export interface TaskInstance {
  task_id: string
  map_index: number
  state: string | null
  try_number: number
  start_date: string | null
  end_date: string | null
  duration: number | null
  label?: string          // the book (or reader and book) a mapped task works on
  book_id?: string
}

export interface WaitingBook {
  book_id: string
  title: string
  readers: number
  force: boolean
  ready: boolean
  steps: string[]
}

export interface Waiting {
  books: WaitingBook[]
  reviews: { user_book_id: string; user?: string; book_id?: string; title?: string }[]
}

export interface LogLine {
  at: string | null
  level: string | null
  logger: string | null
  message: string
}

export interface AdminQuestion {
  id: string
  created_at: string
  finished_at: string | null
  seconds: number | null
  user: string
  book_id: string
  book: string
  text: string
  status: Question['status']
  answered_by: 'agent' | 'cache' | 'guard' | null
  error: string | null
  supported: boolean | null
  refused: string | null
  cached: string | null
  searches: number
  revisions: number | null
  sampling: Sampling
  tokens: number | null
}

export interface AdminQuestionDetail extends AdminQuestion {
  answer: string | null
  citations: string[]
  details: Record<string, unknown>
}

export interface QuestionStats {
  hours: number
  total: number
  readers: number
  outcomes: { agent: number; cache: number; refused: number; failed: number; active: number }
  refused: Record<string, number>
  cache_hit_rate: number | null
  supported_rate: number | null
  agent_seconds: { p50: number | null; p95: number | null; max: number | null }
  mean_searches: number | null
  tokens: { total: number; mean: number | null; max: number | null; min: number | null; per_second: number | null }
}

export interface Problem {
  field: string
  code: string
  message: string
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly code: string | null = null,
    readonly problems: Problem[] = [],
  ) {
    super(message)
  }

  // The API answers {"error": {code, message, problems?}} for its own checks and
  // {"detail": ...} for framework-level errors (validation, auth).
  static async from(res: Response): Promise<ApiError> {
    let body: unknown = null
    try {
      body = await res.json()
    } catch {
      /* not JSON */
    }
    const b = body as { error?: { code?: string; message?: string; problems?: Problem[] }; detail?: unknown }
    if (b?.error) return new ApiError(res.status, b.error.message ?? 'Request failed', b.error.code ?? null, b.error.problems ?? [])
    if (typeof b?.detail === 'string') return new ApiError(res.status, b.detail)
    if (Array.isArray(b?.detail)) {
      const first = b.detail[0] as { msg?: string; loc?: string[] }
      return new ApiError(res.status, first?.msg ?? 'Invalid request', 'invalid')
    }
    return new ApiError(res.status, res.statusText || 'Request failed')
  }
}

interface TokenResponse {
  access_token: string
  expires_in: number
  user: User
}

type AuthListener = (user: User | null) => void

export class Api {
  private token: string | null = null
  private refreshing: Promise<boolean> | null = null
  private timer: ReturnType<typeof setTimeout> | null = null
  private listeners = new Set<AuthListener>()
  user: User | null = null

  constructor(private readonly fetchImpl: typeof fetch = (...a) => fetch(...a)) {}

  get accessToken() {
    return this.token
  }

  onAuthChange(fn: AuthListener) {
    this.listeners.add(fn)
    return () => this.listeners.delete(fn)
  }

  private setSession(t: TokenResponse | null) {
    if (this.timer) clearTimeout(this.timer)
    this.token = t?.access_token ?? null
    this.user = t?.user ?? null
    if (t) {
      // Renew a minute before the access token expires.
      this.timer = setTimeout(() => void this.refresh(), Math.max(10, t.expires_in - 60) * 1000)
    }
    this.listeners.forEach((fn) => fn(this.user))
  }

  async request<T>(method: string, path: string, body?: unknown, retry = true): Promise<T> {
    const headers: Record<string, string> = {}
    if (this.token) headers.Authorization = `Bearer ${this.token}`
    let payload: BodyInit | undefined
    if (body instanceof FormData) payload = body
    else if (body !== undefined) {
      headers['Content-Type'] = 'application/json'
      payload = JSON.stringify(body)
    }
    const res = await this.fetchImpl(`/api${path}`, { method, headers, body: payload, credentials: 'same-origin' })
    if (res.status === 401 && retry && !path.startsWith('/auth/')) {
      if (await this.refresh()) return this.request<T>(method, path, body, false)
    }
    if (!res.ok) throw await ApiError.from(res)
    if (res.status === 204) return undefined as T
    return (await res.json()) as T
  }

  /** Exchange the refresh cookie for a new access token; one request at a time. */
  refresh(): Promise<boolean> {
    this.refreshing ??= (async () => {
      try {
        this.setSession(await this.request<TokenResponse>('POST', '/auth/refresh', undefined, false))
        return true
      } catch {
        this.setSession(null)
        return false
      } finally {
        this.refreshing = null
      }
    })()
    return this.refreshing
  }

  async login(email: string, password: string) {
    this.setSession(await this.request<TokenResponse>('POST', '/auth/login', { email, password }, false))
    return this.user!
  }

  signup(email: string, password: string) {
    return this.request<User>('POST', '/auth/signup', { email, password }, false)
  }

  async logout() {
    try {
      await this.request('POST', '/auth/logout', undefined, false)
    } finally {
      this.setSession(null)
    }
  }

  // --- the reader's shelf ---------------------------------------------------------------------

  books = () => this.request<Book[]>('GET', '/books')
  book = (id: string) => this.request<Book>('GET', `/books/${id}`)

  upload(file: File, prompts: { system_prompt?: string; user_prompt?: string }) {
    const form = new FormData()
    form.append('file', file)
    if (prompts.system_prompt?.trim()) form.append('system_prompt', prompts.system_prompt.trim())
    if (prompts.user_prompt?.trim()) form.append('user_prompt', prompts.user_prompt.trim())
    return this.request<{ message: string; next_batch_at: string | null; book: Book }>('POST', '/books', form)
  }

  setPrompts = (id: string, system_prompt: string | null, user_prompt: string | null) =>
    this.request<{ message: string; book: Book }>('PUT', `/books/${id}/prompts`, { system_prompt, user_prompt })
  removeBook = (id: string) => this.request<void>('DELETE', `/books/${id}`)
  /** `fresh` asks the agent even if an earlier answer to the same question would do; `sampling`
   *  holds the reader's own settings (only the ones they changed). */
  ask = (id: string, text: string, fresh = false, sampling?: Sampling) =>
    this.request<Question>('POST', `/books/${id}/questions`,
      { text, fresh, ...(sampling && Object.keys(sampling).length ? { sampling } : {}) })
  samplingOptions = () => this.request<SamplingOptions>('GET', '/questions/sampling')
  questions = (id: string) => this.request<Question[]>('GET', `/books/${id}/questions`)
  question = (id: string) => this.request<Question>('GET', `/questions/${id}`)

  // --- administration ------------------------------------------------------------------------

  adminQuestions = (params: { outcome?: string; search?: string; before?: string; limit?: number }) => {
    const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== '')
      .map(([k, v]) => [k, String(v)]))
    return this.request<{ items: AdminQuestion[]; next: string | null }>('GET', `/admin/questions?${q}`)
  }
  adminQuestion = (id: string) => this.request<AdminQuestionDetail>('GET', `/admin/questions/${id}`)
  questionStats = (hours = 24) => this.request<QuestionStats>('GET', `/admin/questions/stats?hours=${hours}`)
  observability = () =>
    this.request<{ grafana_url: string | null; evidently_url: string | null }>('GET', '/admin/observability')
  users = () => this.request<User[]>('GET', '/admin/users')
  patchUser = (id: string, patch: { role?: Role; is_active?: boolean }) =>
    this.request<User>('PATCH', `/admin/users/${id}`, patch)
  libraryBooks = () => this.request<LibraryBook[]>('GET', '/admin/books')
  reingest = (bookId: string) => this.request<{ message: string; run: PipelineRun }>('POST', `/admin/books/${bookId}/reingest`)
  pipeline = (params: { state?: string; limit?: number; offset?: number } = {}) => {
    const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== '')
      .map(([k, v]) => [k, String(v)]))
    return this.request<Pipeline>('GET', `/admin/pipeline?${q}`)
  }
  waiting = () => this.request<Waiting>('GET', '/admin/pipeline/waiting')
  /** A batch now: everything waiting, or with `only` just those books; `force` redoes books. */
  startRun = (force: string[] = [], only: string[] = []) =>
    this.request<PipelineRun>('POST', '/admin/pipeline/runs', { force, only })
  taskLogs = (runId: string, taskId: string, mapIndex = -1, tryNumber?: number) =>
    this.request<{ try_number: number; lines: LogLine[] }>('GET',
      `/admin/pipeline/runs/${encodeURIComponent(runId)}/tasks/${taskId}/logs?map_index=${mapIndex}` +
      (tryNumber ? `&try_number=${tryNumber}` : ''))
  runTasks = (runId: string) => this.request<TaskInstance[]>('GET', `/admin/pipeline/runs/${encodeURIComponent(runId)}/tasks`)
  stopRun = (runId: string) => this.request<PipelineRun>('POST', `/admin/pipeline/runs/${encodeURIComponent(runId)}/stop`)
  stopTask = (runId: string, taskId: string, mapIndex: number) =>
    this.request('POST', `/admin/pipeline/runs/${encodeURIComponent(runId)}/tasks/${taskId}/stop?map_index=${mapIndex}`)
  retryTask = (runId: string, taskId: string) =>
    this.request('POST', `/admin/pipeline/runs/${encodeURIComponent(runId)}/tasks/${taskId}/retry`)
  setPaused = (paused: boolean) => this.request<{ paused: boolean }>('POST', `/admin/pipeline/${paused ? 'pause' : 'resume'}`)
}

export const api = new Api()
