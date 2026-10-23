import { describe, expect, it } from 'vitest'

import type { Book, Question } from './api'
import type { LiveEvent } from './events'
import { describeSchedule, duration, relativeTime } from './format'
import { applyBookEvent, applyQuestionEvent, questionSteps, splitCitations } from './progress'

const ev = (e: Partial<LiveEvent>): LiveEvent => ({ type: 'question.status', at: '2026-01-01T00:00:00Z', ...e })
const book = (id: string, status: Book['status']) => ({ id, status, title: id }) as Book
const question = (id: string) => ({ id, status: 'running', answer: null, citations: [], details: {}, error: null }) as unknown as Question

describe('applyBookEvent', () => {
  it('updates the status and progress message of the matching book', () => {
    const out = applyBookEvent([book('a', 'queued'), book('b', 'queued')], {},
      ev({ type: 'book.status', user_book_id: 'a', status: 'checking', message: 'Finding chapters' }))
    expect(out.books.map((b) => b.status)).toEqual(['checking', 'queued'])
    expect(out.progress).toEqual({ a: 'Finding chapters' })
  })

  it('keeps the status for unknown status values but records the message', () => {
    const out = applyBookEvent([book('a', 'checking')], {}, ev({ type: 'book.status', user_book_id: 'a', status: 'summarizing', message: '3/40' }))
    expect(out.books[0].status).toBe('checking')
    expect(out.progress.a).toBe('3/40')
  })

  it('ignores other events', () => {
    const books = [book('a', 'queued')]
    expect(applyBookEvent(books, {}, ev({ type: 'pipeline.task' })).books).toBe(books)
  })
})

describe('questionSteps', () => {
  it('lists each step, marking only the latest active', () => {
    const steps = questionSteps([ev({ status: 'thinking' }), ev({ status: 'searching', message: 'Searching "axe"...' }),
      ev({ status: 'reading' }), ev({ status: 'searching', message: 'Searching "Sonya"...' })])
    expect(steps.map((s) => [s.key, s.state])).toEqual([
      ['thinking', 'done'], ['searching', 'done'], ['reading', 'done'], ['searching', 'active']])
  })

  it('gives each repeated search its own line but folds other repeats', () => {
    const steps = questionSteps([ev({ status: 'searching', message: 'a' }), ev({ status: 'searching', message: 'b' }),
      ev({ status: 'reading', message: '1' }), ev({ status: 'reading', message: '2' })])
    expect(steps.map((s) => s.detail)).toEqual(['a', 'b', '2'])
    expect(steps[2].state).toBe('active')
  })

  it('starts with checking the question', () => {
    expect(questionSteps([ev({ status: 'screening' }), ev({ status: 'queued' })]).map((s) => s.label))
      .toEqual(['Checking the question', 'Waiting for a free slot'])
  })

  it('skips unknown statuses and other event types', () => {
    expect(questionSteps([ev({ status: 'mystery' }), ev({ type: 'book.status', status: 'thinking' })])).toEqual([])
  })
})

describe('applyQuestionEvent', () => {
  it('fills in a finished answer', () => {
    const [q] = applyQuestionEvent([question('q1')], ev({ type: 'question.done', question_id: 'q1', answer: 'Yes.', citations: ['Part I'], supported: true }))
    expect(q).toMatchObject({ status: 'done', answer: 'Yes.', citations: ['Part I'], details: { supported: true } })
  })

  it('marks a failure with its message', () => {
    const [q] = applyQuestionEvent([question('q1')], ev({ type: 'question.failed', question_id: 'q1', message: 'Timed out' }))
    expect(q).toMatchObject({ status: 'failed', error: 'Timed out' })
  })

  it('marks a cached answer', () => {
    const [q] = applyQuestionEvent([question('q1')], ev({ type: 'question.done', question_id: 'q1', answer: 'Yes.',
      supported: true, cached: { kind: 'exact', similarity: 1 } }))
    expect(q).toMatchObject({ status: 'done', details: { supported: true, cached: { kind: 'exact' } } })
  })

  it('marks a refused question with the reason', () => {
    const [q] = applyQuestionEvent([question('q1')], ev({ type: 'question.refused', question_id: 'q1',
      code: 'not_in_book', message: 'Crime and Punishment never mentions “Hagrid”.' }))
    expect(q).toMatchObject({ status: 'refused', error: 'Crime and Punishment never mentions “Hagrid”.',
      details: { refused: 'not_in_book' } })
  })

  it('leaves the list alone for progress events', () => {
    const qs = [question('q1')]
    expect(applyQuestionEvent(qs, ev({ question_id: 'q1', status: 'thinking' }))).toBe(qs)
  })
})

describe('splitCitations', () => {
  it('splits text and citations, including several in one bracket', () => {
    expect(splitCitations('He confesses [Part VI, Chapter VIII; Epilogue, Chapter II]. Then [1] stays.')).toEqual([
      { text: 'He confesses ' }, { citation: 'Part VI, Chapter VIII' }, { citation: 'Epilogue, Chapter II' }, { text: '. Then [1] stays.' }])
  })

  it('returns plain answers unchanged', () => {
    expect(splitCitations('No citations here.')).toEqual([{ text: 'No citations here.' }])
  })
})

describe('format', () => {
  it('describes schedules', () => {
    expect(describeSchedule('0 * * * *')).toBe('Every hour')
    expect(describeSchedule('*/15 * * * *')).toBe('*/15 * * * *')
    expect(describeSchedule(null)).toBe('Not scheduled')
  })

  it('formats durations', () => {
    expect(duration(null)).toBe('')
    expect(duration(4.4)).toBe('4 s')
    expect(duration(125)).toBe('2 min 5 s')
  })

  it('formats relative times', () => {
    const now = Date.parse('2026-01-01T12:00:00Z')
    expect(relativeTime('2026-01-01T11:00:00Z', now)).toMatch(/hour/)
    expect(relativeTime(null, now)).toBe('')
  })
})
