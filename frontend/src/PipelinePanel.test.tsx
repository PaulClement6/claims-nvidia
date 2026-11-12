import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import PipelinePanel from './PipelinePanel'

class FakeEventSource {
  static all: FakeEventSource[] = []
  onmessage: ((e: MessageEvent) => void) | null = null
  closed = false
  url: string
  constructor(url: string) {
    this.url = url
    FakeEventSource.all.push(this)
  }
  close() {
    this.closed = true
  }
  emit(data: unknown) {
    act(() => this.onmessage?.(new MessageEvent('message', { data: JSON.stringify(data) })))
  }
}

beforeEach(() => {
  FakeEventSource.all = []
  vi.stubGlobal('EventSource', FakeEventSource)
})
afterEach(() => vi.unstubAllGlobals())

const event = (sequence: number, extra = {}) => ({
  sequence,
  time: '2026-11-12T10:00:00Z',
  actor: 'tool',
  stage: 'get_customer_policies',
  title: `Get customer policies ${sequence}`,
  inputs: { customer_id: 'C-1001' },
  outputs: { policies: [{ number: 'TRV-2026-0417' }] },
  model: null,
  duration: 0.0123,
  ...extra,
})

test('shows the empty state and opens no stream without a claim', () => {
  render(<PipelinePanel claimId={null} />)
  expect(screen.getByText('No event yet. Start a claim in the customer panel.')).toBeInTheDocument()
  expect(FakeEventSource.all).toHaveLength(0)
})

test('lists live events of the claim, newest at the bottom, inputs and outputs open on click', () => {
  render(<PipelinePanel claimId="CL-1" />)
  expect(FakeEventSource.all.map((s) => s.url)).toEqual(['/claims/CL-1/events'])
  const source = FakeEventSource.all[0]
  expect(screen.getByText('No event yet. Start a claim in the customer panel.')).toBeInTheDocument()

  source.emit(event(1))
  source.emit(event(2, { actor: 'agent', model: 'mock-model' }))

  expect(screen.queryByText('No event yet. Start a claim in the customer panel.')).not.toBeInTheDocument()
  const titles = screen.getAllByText(/^Get customer policies/).map((e) => e.textContent)
  expect(titles).toEqual(['Get customer policies 1', 'Get customer policies 2'])
  expect(screen.getByText('tool')).toBeInTheDocument()
  expect(screen.getByText('agent')).toBeInTheDocument()
  expect(screen.getByText('mock-model')).toBeInTheDocument()
  expect(screen.getAllByText('12 ms')).toHaveLength(2)

  const inputs = screen.getAllByText(/"customer_id": "C-1001"/)[0]
  expect(inputs).not.toBeVisible()
  fireEvent.click(screen.getByText('Get customer policies 1'))
  expect(inputs).toBeVisible()
  expect(inputs.textContent).toBe(JSON.stringify({ customer_id: 'C-1001' }, null, 2))
  expect(screen.getAllByText(/"number": "TRV-2026-0417"/)[0]).toBeVisible()
})

test('an event that arrives twice is shown once', () => {
  render(<PipelinePanel claimId="CL-1" />)
  FakeEventSource.all[0].emit(event(1))
  FakeEventSource.all[0].emit(event(1))
  expect(screen.getAllByText('Get customer policies 1')).toHaveLength(1)
})

test('closes the stream when the claim changes or the panel goes away', () => {
  const { rerender, unmount } = render(<PipelinePanel claimId="CL-1" />)
  FakeEventSource.all[0].emit(event(1))
  rerender(<PipelinePanel claimId="CL-2" />)
  expect(FakeEventSource.all[0].closed).toBe(true)
  expect(FakeEventSource.all[1].url).toBe('/claims/CL-2/events')
  expect(screen.queryByText('Get customer policies 1')).not.toBeInTheDocument()
  unmount()
  expect(FakeEventSource.all[1].closed).toBe(true)
})

test('reloads the claim on an extract_document event and shows fields with their sources', async () => {
  const doc = {
    id: 'D-1',
    file: 'CL-1/D-1.pdf',
    document_type: 'booking_confirmation',
    file_metadata: { original_name: 'booking.pdf' },
    fields: [{ name: 'trip_price', value: 1840, source: { document_id: 'D-1', page: 1, snippet: 'CHF 1,840.00' } }],
  }
  const fetchMock = vi.fn(() => Promise.resolve(Response.json({ documents: [doc] })))
  vi.stubGlobal('fetch', fetchMock)
  render(<PipelinePanel claimId="CL-1" />)

  FakeEventSource.all[0].emit(event(1, { stage: 'classify_document' }))
  expect(fetchMock).not.toHaveBeenCalled()
  FakeEventSource.all[0].emit(event(2, { stage: 'extract_document' }))

  expect(await screen.findByText('booking.pdf')).toBeInTheDocument()
  expect(fetchMock).toHaveBeenCalledWith('/claims/CL-1')
  expect(screen.getByText('booking_confirmation')).toBeInTheDocument()
  expect(screen.getByText('trip_price')).toBeInTheDocument()
  expect(screen.getByText('1840')).toBeInTheDocument()
  expect(screen.getByText('page 1 · “CHF 1,840.00”')).toBeInTheDocument()
})
