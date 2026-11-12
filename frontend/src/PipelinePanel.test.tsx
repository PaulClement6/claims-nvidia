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

test('every table of the panel sits in a box that scrolls on its own, so the panel never scrolls sideways', async () => {
  serve({ ...emptyClaim, documents: [booking], signals: [c003] })
  render(<PipelinePanel claimId="CL-1" />)
  FakeEventSource.all[0].emit(event(1, { stage: 'run_checks' }))
  await screen.findByText('Checks and signals')
  const tables = screen.getAllByRole('table')
  expect(tables.length).toBeGreaterThan(0)
  for (const t of tables) expect(t.parentElement).toHaveClass('table-scroll')
})

test.each(['check_tampering', 'check_consistency'])('a %s event reloads the claim', async (stage) => {
  const fetchMock = serve({ ...emptyClaim, signals: [c003] })
  render(<PipelinePanel claimId="CL-1" />)
  FakeEventSource.all[0].emit(event(1, { stage: 'run_checks' }))
  await screen.findByText('Checks and signals')
  fetchMock.mockClear()
  FakeEventSource.all[0].emit(event(2, { stage }))
  await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/claims/CL-1'))
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

const report = {
  outcome: 'investigation',
  score: 130,
  summary: 'The medical invoice was edited after it was issued, its total does not match its lines, and a name on it is not the insured person.',
  findings: [
    {
      code: 'T003',
      title: 'Line items do not add up to the total',
      saw: 'The lines of medical-invoice.pdf add up to CHF 1450; its total says CHF 4450.',
      where: ['medical-invoice.pdf, page 1'],
      expected: 'Line items that add up to the total (tolerance CHF 0.05).',
    },
    {
      code: 'R003',
      title: 'Incident within 14 days of the policy start',
      saw: 'Incident 5 days after the start of policy ACC-2026-2210 (2026-10-20).',
      where: ['medical-report.pdf, page 1'],
      expected: 'An incident more than 14 days after the policy start.',
    },
  ],
  not_checked: ['Duplicate invoices across claims.', 'Handwriting and stamps.'],
  next_steps: ['Ask the provider for the original invoice.'],
}

test('a write_fraud_report event loads the claim and shows the report first', async () => {
  const fetchMock = serve({ ...emptyClaim, score: 130, outcome: 'investigation', fraud_report: report })
  render(<PipelinePanel claimId="CL-1" />)
  FakeEventSource.all[0].emit(event(1, { stage: 'write_fraud_report' }))

  expect(await screen.findByRole('heading', { name: 'Fraud report' })).toBeInTheDocument()
  expect(fetchMock).toHaveBeenCalledWith('/claims/CL-1')
  expect(screen.getByText('investigation · score 130')).toHaveClass('pill-bad')
  expect(screen.getByText(report.summary)).toHaveClass('report-summary')
  const rows = screen.getAllByRole('row').map((r) => r.textContent)
  expect(rows[0]).toBe('CodeWhat the system sawWhereExpected')
  expect(rows[1]).toContain('T003')
  expect(rows[1]).toContain('add up to CHF 1450')
  expect(rows[1]).toContain('medical-invoice.pdf, page 1')
  expect(rows[1]).toContain('Line items that add up to the total')
  expect(rows[2]).toContain('medical-report.pdf, page 1')
  expect(screen.getByRole('heading', { name: 'Not checked' })).toBeInTheDocument()
  expect(screen.getByText('Handwriting and stamps.')).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Next steps' })).toBeInTheDocument()
  expect(screen.getByText('Ask the provider for the original invoice.')).toBeInTheDocument()
  const heads = screen.getAllByRole('heading', { level: 3 }).map((h) => h.textContent)
  expect(heads).toEqual(['Fraud report', 'Outcome', 'Events'])
})

test('no report, no Fraud report section', async () => {
  serve({ ...emptyClaim, score: 20, outcome: 'handler_review', fraud_report: null })
  render(<PipelinePanel claimId="CL-1" />)
  FakeEventSource.all[0].emit(event(1, { stage: 'assess_and_route' }))
  await screen.findByRole('heading', { name: 'Outcome' })
  expect(screen.queryByText('Fraud report')).not.toBeInTheDocument()
})

test('draws the graph strip from the workflow events of the claim', () => {
  render(<PipelinePanel claimId="CL-1" />)
  const nodes = () => screen.getAllByRole('listitem').filter((e) => e.querySelector('.graph-node'))
  expect(nodes()).toHaveLength(5)
  expect(screen.getByText('Intake').closest('.graph-node')).toHaveAttribute('data-state', 'todo')

  const source = FakeEventSource.all[0]
  source.emit(event(1, { actor: 'workflow', stage: 'intake', title: 'Node: intake', inputs: {}, outputs: {} }))
  source.emit(event(2, { actor: 'workflow', stage: 'documents', title: 'Node: documents', inputs: {}, outputs: {} }))

  expect(screen.getByText('Intake').closest('.graph-node')).toHaveAttribute('data-state', 'done')
  expect(screen.getByText('Documents').closest('.graph-node')).toHaveAttribute('data-state', 'current')
  expect(screen.getByText('Checks').closest('.graph-node')).toHaveAttribute('data-state', 'todo')
  // the node events are in the list of events too
  expect(screen.getByText('Node: documents')).toBeInTheDocument()
})

test('shows no graph strip without a claim', () => {
  render(<PipelinePanel claimId={null} />)
  expect(screen.queryByRole('list', { name: 'Agent graph' })).not.toBeInTheDocument()
})

test('shows the events of a turn in the order of their time, not of their arrival', () => {
  render(<PipelinePanel claimId="CL-1" />)
  const source = FakeEventSource.all[0]
  // the tool events arrive first; the node and model events of the sandbox come in a batch after
  // them, with the time they happened (a whole second and a part of a second: not string order)
  source.emit(event(1, { title: 'Tool call', time: '2026-11-12T10:00:01.5Z' }))
  source.emit(event(2, { actor: 'workflow', title: 'Node: intake', time: '2026-11-12T10:00:01Z' }))
  source.emit(event(3, { actor: 'agent', title: 'Model writes', time: '2026-11-12T10:00:02.250000Z' }))
  source.emit(event(4, { actor: 'sandbox', stage: 'turn', title: 'Sandbox turn', time: '2026-11-12T10:00:02.5Z' }))
  expect(screen.getAllByRole('group').length).toBe(4)
  const titles = ['Node: intake', 'Tool call', 'Model writes', 'Sandbox turn'].map((t) => screen.getByText(t))
  titles.forEach((el, i) => i > 0 && expect(titles[i - 1].compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy())
  // equal times keep the order they arrived in
  source.emit(event(5, { title: 'Same time A', time: '2026-11-12T10:00:09Z' }))
  source.emit(event(6, { title: 'Same time B', time: '2026-11-12T10:00:09Z' }))
  const ab = screen.getByText('Same time A').compareDocumentPosition(screen.getByText('Same time B'))
  expect(ab & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
})

test('sandbox events have their own pill colours among the others', () => {
  render(<PipelinePanel claimId="CL-1" />)
  const source = FakeEventSource.all[0]
  const sb = (n: number, stage: string, extra = {}) => event(n, { actor: 'sandbox', stage, title: `Sandbox ${stage} ${n}`, ...extra })
  source.emit(sb(1, 'created'))
  source.emit(sb(2, 'denied', { outputs: { reason: 'x' } }))
  source.emit(sb(3, 'probe', { outputs: { denied: true } }))
  source.emit(sb(4, 'probe', { outputs: { denied: false } }))
  source.emit(sb(5, 'failed'))
  source.emit(event(6, { actor: 'agent', title: 'Agent', stage: 'llm' }))
  const pill = (title: string) => screen.getByText(title).closest('summary')!.querySelector('.pill')!
  expect(pill('Sandbox created 1')).toHaveClass('pill-idle')
  expect(pill('Sandbox denied 2')).toHaveClass('pill-warn')
  expect(pill('Sandbox probe 3')).toHaveClass('pill-ok')
  expect(pill('Sandbox probe 4')).toHaveClass('pill-bad')
  expect(pill('Sandbox failed 5')).toHaveClass('pill-bad')
  expect(pill('Agent')).toHaveClass('pill-idle')
  expect(pill('Sandbox created 1').textContent).toBe('sandbox')
})
