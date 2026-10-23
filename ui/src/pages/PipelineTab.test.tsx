import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { api, type Pipeline, type TaskInstance } from '../api'
import { ToastProvider } from '../components/ui'
import { PipelineTab } from './AdminPipeline'

const PIPELINE: Pipeline = {
  paused: false, schedule: '0 * * * *', next_run_at: '2026-10-23T11:00:00Z', import_errors: false, total: 1,
  summary: { by_state: { success: 1 }, mean_duration: 27, longest: 27 },
  runs: [{ run_id: 'manual__1', state: 'success', run_type: 'manual', duration: 27, conf: { only: ['book-1'] } }],
}
const TASKS: TaskInstance[] = [
  { task_id: 'plan', map_index: -1, state: 'success', try_number: 1, start_date: null, end_date: null, duration: 4 },
  { task_id: 'review', map_index: 0, state: 'success', try_number: 1, start_date: null, end_date: null, duration: 18,
    label: 'reader@example.com · Book One' },
]

beforeEach(() => {
  // As in Chromium, where scroll methods return a promise (an effect returning it crashed the page).
  Element.prototype.scrollIntoView = vi.fn(() => Promise.resolve()) as unknown as Element['scrollIntoView']
  vi.spyOn(api, 'pipeline').mockResolvedValue(PIPELINE)
  vi.spyOn(api, 'waiting').mockResolvedValue({ books: [], reviews: [] })
  vi.spyOn(api, 'runTasks').mockResolvedValue(TASKS)
  vi.spyOn(api, 'taskLogs').mockResolvedValue({ try_number: 1, lines: [
    { at: '2026-10-23T07:55:50Z', level: 'info', logger: 'task', message: 'approved' }] })
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('PipelineTab', () => {
  it('lists the run with its labelled tasks and opens and closes a task log', async () => {
    const errors: unknown[] = []
    const onError = (e: ErrorEvent) => errors.push(e.error)
    window.addEventListener('error', onError)
    vi.spyOn(console, 'error').mockImplementation((...args) => { errors.push(args[0]) })
    render(<ToastProvider><PipelineTab /></ToastProvider>)
    expect(await screen.findByText('reader@example.com · Book One')).toBeTruthy()
    expect(screen.getByText(/1 chosen book/)).toBeTruthy()
    fireEvent.click(screen.getAllByRole('button', { name: 'Logs' })[1])
    expect(await screen.findByText('approved')).toBeTruthy()
    expect(api.taskLogs).toHaveBeenCalledWith('manual__1', 'review', 0, 1)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByText('approved')).toBeNull()
    window.removeEventListener('error', onError)
    expect(errors).toEqual([])
  })
})
