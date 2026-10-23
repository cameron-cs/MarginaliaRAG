// Turning live events into what the pages show. Pure functions, so they are easy to test.

import type { Book, BookStatus, Question } from './api'
import type { LiveEvent } from './events'

const BOOK_STATUSES: BookStatus[] = ['queued', 'checking', 'ready', 'rejected', 'failed']

/** A book.status event applied to the shelf: new status, and the latest progress message. */
export function applyBookEvent(books: Book[], progress: Record<string, string>, ev: LiveEvent) {
  if (ev.type !== 'book.status' || !ev.user_book_id) return { books, progress }
  const status = BOOK_STATUSES.includes(ev.status as BookStatus) ? (ev.status as BookStatus) : undefined
  return {
    books: books.map((b) => (b.id === ev.user_book_id && status ? { ...b, status } : b)),
    progress: { ...progress, [ev.user_book_id]: ev.message ?? '' },
  }
}

export interface Step {
  key: string
  label: string
  detail?: string
  state: 'done' | 'active'
}

const STEP_LABELS: Record<string, string> = {
  screening: 'Checking the question',
  queued: 'Waiting for a free slot',
  thinking: 'Thinking',
  searching: 'Searching the book',
  reading: 'Reading passages',
  checking: 'Checking the answer against the book',
  revising: 'Double-checking',
  answering: 'Writing the answer',
}

/** The steps of one question so far, from its question.status events (in order). */
export function questionSteps(events: LiveEvent[]): Step[] {
  const steps: Step[] = []
  for (const ev of events) {
    if (ev.type !== 'question.status' || !ev.status || !STEP_LABELS[ev.status]) continue
    const last = steps[steps.length - 1]
    if (last) last.state = 'done'
    // Repeated searches each get a line; repeated reading or thinking updates the last one.
    if (last && last.key === ev.status && ev.status !== 'searching') {
      last.detail = ev.message
      last.state = 'active'
      continue
    }
    steps.push({ key: ev.status, label: STEP_LABELS[ev.status], detail: ev.message, state: 'active' })
  }
  return steps
}

const FINAL = ['question.done', 'question.failed', 'question.refused']

/** A finished, failed or refused question event applied to the question list. */
export function applyQuestionEvent(questions: Question[], ev: LiveEvent): Question[] {
  if (!ev.question_id || !FINAL.includes(ev.type)) return questions
  return questions.map((q): Question => {
    if (q.id !== ev.question_id) return q
    if (ev.type === 'question.done')
      return { ...q, status: 'done', answer: ev.answer ?? q.answer, citations: ev.citations ?? q.citations,
               details: { ...q.details, supported: ev.supported ?? q.details.supported, cached: ev.cached } }
    if (ev.type === 'question.refused')
      return { ...q, status: 'refused', error: ev.message ?? 'This question was not sent.',
               details: { ...q.details, refused: ev.code as Question['details']['refused'] } }
    return { ...q, status: 'failed', error: ev.message ?? 'Failed' }
  })
}

export interface AnswerPart {
  text?: string
  citation?: string
}

/** Split an answer into text and its [Part I, Chapter VII, passage 3] citations. */
export function splitCitations(answer: string): AnswerPart[] {
  const parts: AnswerPart[] = []
  const rx = /\[([^[\]]*?(?:Chapter|Part|Book|Prologue|Epilogue)[^[\]]*)\]/g
  let at = 0
  for (const m of answer.matchAll(rx)) {
    if (m.index! > at) parts.push({ text: answer.slice(at, m.index) })
    for (const c of m[1].split(';').map((s) => s.trim()).filter(Boolean)) parts.push({ citation: c })
    at = m.index! + m[0].length
  }
  if (at < answer.length) parts.push({ text: answer.slice(at) })
  return parts
}
