import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { api, type TaskInstance, type Waiting } from '../api'
import { ToastProvider } from '../components/ui'
import { LogSheet, WaitingCard } from './AdminPipeline'
import { outcomeOf } from './AdminQuestions'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

const WAITING: Waiting = {
  books: [
    { book_id: 'book-1', title: 'Book One', readers: 2, force: false, ready: false, steps: ['structure', 'cover', 'summarize', 'index'] },
    { book_id: 'book-2', title: 'Book Two', readers: 1, force: false, ready: true, steps: ['cover'] },
  ],
  reviews: [{ user_book_id: 'ub-1', user: 'reader@example.com', book_id: 'book-3', title: 'Book Three' }],
}

describe('WaitingCard', () => {
  it('lists the waiting books with the steps they need', () => {
    render(<WaitingCard waiting={WAITING} busy={false} onRun={() => {}} />)
    expect(screen.getByText('2 book(s), 1 prompt review(s)')).toBeTruthy()
    expect(screen.getByText('Book Two').nextSibling?.textContent).toContain('answering questions meanwhile')
    expect(screen.getAllByText('Cover')).toHaveLength(2)
    expect(screen.getByText('Review prompts')).toBeTruthy()
  })

  it('runs everything, one book, or the chosen books', () => {
    const runs: string[][] = []
    render(<WaitingCard waiting={WAITING} busy={false} onRun={(only) => runs.push(only)} />)
    fireEvent.click(screen.getByRole('button', { name: 'Run all now' }))
    fireEvent.click(screen.getAllByRole('button', { name: 'Process now' })[1])
    fireEvent.click(screen.getByLabelText('Choose Book One'))
    fireEvent.click(screen.getByLabelText('Choose Book Two'))
    fireEvent.click(screen.getByRole('button', { name: 'Run 2 selected' }))
    fireEvent.click(screen.getAllByRole('button', { name: 'Process now' })[2])
    expect(runs).toEqual([[], ['book-2'], ['book-1', 'book-2'], ['book-3']])
  })

  it('says when nothing is waiting', () => {
    render(<WaitingCard waiting={{ books: [], reviews: [] }} busy={false} onRun={() => {}} />)
    expect(screen.getByText('Nothing is waiting.')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Run a batch' })).toBeTruthy()
  })
})

describe('LogSheet', () => {
  const task: TaskInstance = { task_id: 'process.index', map_index: 0, state: 'failed', try_number: 2,
    start_date: null, end_date: null, duration: 12, label: 'Book One' }

  it('shows the log of the last try, with errors marked', async () => {
    const logs = vi.spyOn(api, 'taskLogs').mockResolvedValue({ try_number: 2, lines: [
      { at: '2026-10-23T07:03:32.123Z', level: 'info', logger: 'task', message: 'indexed version 3' },
      { at: '2026-10-23T07:03:40Z', level: 'error', logger: 'task', message: 'Qdrant timed out' },
    ] })
    render(<ToastProvider><LogSheet run="manual__1" task={task} onClose={() => {}} /></ToastProvider>)
    expect(await screen.findByText('indexed version 3')).toBeTruthy()
    expect(screen.getByText('Qdrant timed out').parentElement?.className).toContain('red')
    expect(screen.getByRole('dialog', { name: 'Index · Book One' })).toBeTruthy()
    expect(logs).toHaveBeenCalledWith('manual__1', 'process.index', 0, 2)
    fireEvent.change(screen.getByLabelText('Try'), { target: { value: '1' } })
    expect(logs).toHaveBeenLastCalledWith('manual__1', 'process.index', 0, 1)
  })
})

describe('outcomeOf', () => {
  it('names how each question ended', () => {
    const q = { status: 'done' as const, answered_by: 'agent' as const, refused: null, supported: true }
    expect(outcomeOf(q)).toEqual(['Answered', 'green'])
    expect(outcomeOf({ ...q, supported: false })).toEqual(['Answered, unchecked claims', 'orange'])
    expect(outcomeOf({ ...q, answered_by: 'cache' })).toEqual(['From cache', 'green'])
    expect(outcomeOf({ ...q, status: 'refused', answered_by: 'guard', refused: 'not_in_book' }))
      .toEqual(['Refused: not in the book', 'orange'])
    expect(outcomeOf({ ...q, status: 'failed' })).toEqual(['Failed', 'red'])
    expect(outcomeOf({ ...q, status: 'running', answered_by: null })).toEqual(['Answering', 'blue'])
  })
})
