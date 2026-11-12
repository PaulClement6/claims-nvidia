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

const emptyClaim = { documents: [], signals: [], score: null, outcome: null, outcome_reasons: [] }

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

test('shows a small thumbnail for an image document and none for a PDF', async () => {
  const photo = {
    id: 'D-2',
    file: 'CL-1/D-2.jpg',
    document_type: 'damage_photo',
    file_metadata: { original_name: 'damage-photo-1.jpg' },
    fields: [{ name: 'damage_description', value: 'water stain', source: { document_id: 'D-2', page: 1, snippet: null, box: [1, 2, 3, 4] } }],
  }
  const pdf = { ...photo, id: 'D-1', file: 'CL-1/D-1.pdf', document_type: 'repair_invoice', file_metadata: { original_name: 'repair-invoice.pdf' }, fields: [] }
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(Response.json({ ...emptyClaim, documents: [pdf, photo] }))))
  render(<PipelinePanel claimId="CL-1" />)
  FakeEventSource.all[0].emit(event(1, { stage: 'extract_document' }))

  const img = await screen.findByRole('img', { name: 'damage-photo-1.jpg' })
  expect(img).toHaveAttribute('src', '/claims/CL-1/documents/D-2/file')
  expect(img).toHaveStyle({ maxHeight: '96px' })
  expect(screen.getAllByRole('img')).toHaveLength(1)
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
  const fetchMock = vi.fn(() => Promise.resolve(Response.json({ ...emptyClaim, documents: [doc] })))
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

const booking = {
  id: 'D-1',
  file: 'CL-1/D-1.pdf',
  document_type: 'booking_confirmation',
  file_metadata: { original_name: 'booking-confirmation.pdf' },
  fields: [],
}
const c003 = {
  code: 'C003',
  title: 'Claimed amount differs from the documented amount',
  severity: 'medium',
  points: 15,
  detail: 'Claimed CHF 1800',
  evidence: [{ document_id: 'D-1', page: 1, snippet: 'CHF 1,840.00' }],
}
const r001 = { ...c003, code: 'R001', title: 'A required document is missing', severity: 'low', points: 25, detail: 'Missing: a medical certificate.', evidence: [] }

const serve = (claim: object) => {
  const fetchMock = vi.fn(() => Promise.resolve(Response.json(claim)))
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

test('shows no checks and no outcome before the checks ran, and no fetch for other events', () => {
  const fetchMock = serve(emptyClaim)
  render(<PipelinePanel claimId="CL-1" />)
  FakeEventSource.all[0].emit(event(1, { stage: 'get_claim_summary' }))
  expect(fetchMock).not.toHaveBeenCalled()
  expect(screen.queryByText('Checks and signals')).not.toBeInTheDocument()
  expect(screen.queryByText('Outcome')).not.toBeInTheDocument()
})

test('a run_checks event loads the claim and shows the signals with their evidence', async () => {
  const fetchMock = serve({ ...emptyClaim, documents: [booking], signals: [c003, r001] })
  render(<PipelinePanel claimId="CL-1" />)
  FakeEventSource.all[0].emit(event(1, { stage: 'run_checks' }))

  expect(await screen.findByText('Checks and signals')).toBeInTheDocument()
  expect(fetchMock).toHaveBeenCalledWith('/claims/CL-1')
  const rows = screen.getAllByRole('row').map((r) => r.textContent)
  expect(rows[1]).toContain('C003')
  expect(rows[1]).toContain('Claimed amount differs from the documented amount')
  expect(rows[1]).toContain('medium')
  expect(rows[1]).toContain('15')
  expect(rows[1]).toContain('booking-confirmation.pdf · page 1')
  // R001 has no page: its detail names the missing document
  expect(rows[2]).toContain('A required document is missing')
  expect(rows[2]).toContain('Missing: a medical certificate.')
  expect(screen.getByText('medium')).toHaveClass('pill-warn')
  expect(screen.getByText('low')).toHaveClass('pill-idle')
})

test('checks that ran clean show "No signal."', async () => {
  serve(emptyClaim)
  render(<PipelinePanel claimId="CL-1" />)
  FakeEventSource.all[0].emit(event(1, { stage: 'run_checks' }))
  expect(await screen.findByText('No signal.')).toBeInTheDocument()
})

test.each([
  ['straight_through', 'pill-ok'],
  ['handler_review', 'pill-warn'],
  ['investigation', 'pill-bad'],
])('an assess_and_route event shows the score, the %s pill and the reasons', async (outcome, pill) => {
  serve({ ...emptyClaim, score: 65, outcome, outcome_reasons: ['Score 65 is 60 or more.', 'A required document is missing.'] })
  render(<PipelinePanel claimId="CL-1" />)
  FakeEventSource.all[0].emit(event(1, { stage: 'assess_and_route' }))

  expect(await screen.findByRole('heading', { name: 'Outcome' })).toBeInTheDocument()
  expect(screen.getByText('Score 65')).toBeInTheDocument()
  expect(screen.getByText(outcome)).toHaveClass(pill)
  expect(screen.getAllByRole('listitem').map((l) => l.textContent)).toEqual(
    expect.arrayContaining(['Score 65 is 60 or more.', 'A required document is missing.']),
  )
})

test('the sections come in the order Outcome, Checks and signals, Extracted data, Events', async () => {
  serve({ ...emptyClaim, documents: [{ ...booking, fields: [{ name: 'trip_price', value: 1, source: { page: 1, snippet: 'x' } }] }], signals: [c003], score: 15, outcome: 'straight_through' })
  render(<PipelinePanel claimId="CL-1" />)
  FakeEventSource.all[0].emit(event(1, { stage: 'extract_document' }))
  FakeEventSource.all[0].emit(event(2, { stage: 'run_checks' }))
  FakeEventSource.all[0].emit(event(3, { stage: 'assess_and_route' }))
  await screen.findByText('Checks and signals')
  const heads = screen.getAllByRole('heading', { level: 3 }).map((h) => h.textContent)
  expect(heads).toEqual(['Outcome', 'Checks and signals', 'Extracted data', 'Events'])
})
