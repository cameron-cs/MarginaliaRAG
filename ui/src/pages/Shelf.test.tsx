import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { api, ApiError } from '../api'
import { Cover, StatusPill } from '../components/ui'
import { UploadSheet } from './Shelf'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

const pdf = () => new File(['%PDF-1.7'], 'harry-potter.pdf', { type: 'application/pdf' })

async function fillAndSubmit(onAdded = vi.fn()) {
  const user = userEvent.setup()
  render(<UploadSheet onClose={() => {}} onAdded={onAdded} />)
  await user.upload(screen.getByTestId('file-input'), pdf())
  await user.type(screen.getByLabelText('Assistant persona'), 'Answer like a teacher.')
  await user.click(screen.getByRole('button', { name: 'Add to shelf' }))
  return onAdded
}

describe('UploadSheet', () => {
  it('needs a file before it can submit', () => {
    render(<UploadSheet onClose={() => {}} onAdded={() => {}} />)
    expect(screen.getByRole<HTMLButtonElement>('button', { name: 'Add to shelf' }).disabled).toBe(true)
  })

  it('uploads the file with its prompts and reports the message', async () => {
    const upload = vi.spyOn(api, 'upload').mockResolvedValue({ message: 'Processed at 14:00 UTC.', next_batch_at: null, book: {} as never })
    const onAdded = await fillAndSubmit()
    expect(upload).toHaveBeenCalledWith(expect.any(File), { system_prompt: 'Answer like a teacher.', user_prompt: '' })
    expect(onAdded).toHaveBeenCalledWith('Processed at 14:00 UTC.')
  })

  it('shows each problem next to its field', async () => {
    vi.spyOn(api, 'upload').mockRejectedValue(new ApiError(422, 'Not accepted', 'invalid_upload', [
      { field: 'file', code: 'no_chapters', message: 'No chapters were found.' },
      { field: 'system_prompt', code: 'injection', message: 'Prompts cannot change how the assistant works.' },
    ]))
    const onAdded = await fillAndSubmit()
    const alerts = screen.getAllByRole('alert').map((a) => a.textContent)
    expect(alerts).toEqual(['No chapters were found.', 'Prompts cannot change how the assistant works.'])
    expect(screen.getByLabelText('Assistant persona').classList.contains('invalid')).toBe(true)
    expect(onAdded).not.toHaveBeenCalled()
  })

  it('explains a duplicate upload', async () => {
    vi.spyOn(api, 'upload').mockRejectedValue(new ApiError(409, 'Duplicate', 'duplicate'))
    await fillAndSubmit()
    expect(screen.getByRole('alert').textContent).toBe('This book is already on your shelf.')
  })
})

describe('Cover', () => {
  it('shows the cover image when there is one', () => {
    const { container } = render(<Cover title="Dune" src="/api/covers/dune/cover.jpg?sig=x" />)
    expect(container.querySelector('img')?.getAttribute('src')).toBe('/api/covers/dune/cover.jpg?sig=x')
    expect(screen.queryByText('Dune')).toBeNull()
  })

  it('falls back to the generated look when the image fails or is missing', () => {
    const { container } = render(<Cover title="Dune" author="Frank Herbert" src="/broken.jpg" />)
    fireEvent.error(container.querySelector('img')!)
    expect(container.querySelector('img')).toBeNull()
    expect(screen.getByText('Dune').className).toBe('ct')
    cleanup()
    render(<Cover title="Dune" />)
    expect(screen.getByText('Dune').className).toBe('ct')
  })
})

describe('StatusPill', () => {
  it.each([
    ['queued', 'Queued'], ['checking', 'Processing'], ['ready', 'Ready'], ['rejected', 'Not accepted'], ['failed', 'Failed'],
  ] as const)('shows %s as %s', (status, label) => {
    render(<StatusPill status={status} />)
    expect(screen.getByText(label).classList.contains('pill')).toBe(true)
  })
})
