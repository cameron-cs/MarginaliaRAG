import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { api, type Sampling, type SamplingOptions } from '../api'
import { describeSampling, loadSampling, SamplingPanel } from './SamplingPanel'

const OPTIONS: SamplingOptions = {
  defaults: { temperature: 0.6, top_p: 0.95, top_k: 40, max_searches: 6 },
  limits: {
    temperature: { min: 0.1, max: 1.5, description: 'Randomness' },
    top_p: { min: 0.05, max: 1, description: 'Nucleus' },
    top_k: { min: 1, max: 200, description: 'Top words' },
    max_searches: { min: 1, max: 10, description: 'Tool calls' },
    seed: { min: 0, max: 2147483647, description: 'Seed' },
  },
}

beforeEach(() => {
  localStorage.clear()
  vi.spyOn(api, 'samplingOptions').mockResolvedValue(OPTIONS)
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

function renderPanel(value: Sampling = {}) {
  const changes: Sampling[] = []
  render(<SamplingPanel userId="u1" value={value} onChange={(s) => changes.push(s)} />)
  return changes
}

describe('SamplingPanel', () => {
  it('shows the server defaults until the reader changes something', async () => {
    renderPanel()
    const temperature = await screen.findByLabelText<HTMLInputElement>('Temperature')
    expect(temperature.value).toBe('0.6')
    expect(temperature.min).toBe('0.1')
    expect(temperature.max).toBe('1.5')
    expect(screen.getByRole<HTMLButtonElement>('button', { name: 'Reset to defaults' }).disabled).toBe(true)
  })

  it('keeps only the values that differ from the defaults, and remembers them', async () => {
    const changes = renderPanel()
    fireEvent.change(await screen.findByLabelText('Temperature'), { target: { value: '0.9' } })
    expect(changes.at(-1)).toEqual({ temperature: 0.9 })
    expect(loadSampling('u1')).toEqual({ temperature: 0.9 })
    expect(loadSampling('someone-else')).toEqual({})
  })

  it('drops a value set back to its default, and an emptied seed', async () => {
    const changes = renderPanel({ temperature: 0.9, seed: 7 })
    fireEvent.change(await screen.findByLabelText('Temperature'), { target: { value: '0.6' } })
    expect(changes.at(-1)).toEqual({ seed: 7 })
    fireEvent.change(screen.getByLabelText('Seed'), { target: { value: '' } })
    expect(changes.at(-1)).toEqual({ temperature: 0.9 })
  })

  it('resets to the defaults', async () => {
    const changes = renderPanel({ top_k: 10 })
    fireEvent.click(await screen.findByRole('button', { name: 'Reset to defaults' }))
    expect(changes.at(-1)).toEqual({})
    expect(loadSampling('u1')).toEqual({})
  })
})

describe('describeSampling', () => {
  it('names what was changed', () => {
    expect(describeSampling(undefined)).toBe('')
    expect(describeSampling({})).toBe('')
    expect(describeSampling({ temperature: 0.9, max_searches: 3 })).toBe('temperature 0.9, searches 3')
  })
})
