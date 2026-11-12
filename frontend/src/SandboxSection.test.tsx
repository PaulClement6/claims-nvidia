import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import PipelinePanel from './PipelinePanel'

class FakeEventSource {
  static all: FakeEventSource[] = []
  onmessage: ((e: MessageEvent) => void) | null = null
  url: string
  constructor(url: string) {
    this.url = url
    FakeEventSource.all.push(this)
  }
  close() {}
  emit(data: unknown) {
    act(() => this.onmessage?.(new MessageEvent('message', { data: JSON.stringify(data) })))
  }
}

beforeEach(() => {
  FakeEventSource.all = []
  vi.stubGlobal('EventSource', FakeEventSource)
})
afterEach(() => vi.unstubAllGlobals())

const view = (state: 'running' | 'deleted' = 'running') => ({
  name: 'claims-cl-1a2b3c4d',
  state,
  created: '2026-11-12T10:00:00Z',
  created_ms: 481,
  policy: {
    model: { host: 'integrate.api.nvidia.com', port: 443, method: 'POST', path: '/v1/chat/completions' },
    gateway: {
      host: 'host.openshell.internal',
      port: 8000,
      path: '/mcp/AbCdEf…',
      methods: ['initialize', 'notifications/initialized', 'tools/list', 'tools/call'],
      tools: ['assess_and_route', 'check_tampering', 'extract_document'],
    },
    read_only: ['/usr'],
    read_write: ['/sandbox', '/tmp'],
    user: '1500',
    landlock: 'hard_requirement',
  },
})

const probe = (confined = true) => ({
  confined,
  results: [
    { id: 'other_host', label: 'other host', what: 'HTTPS request to example.com', denied: true, detail: 'ConnectError: [Errno 13] Permission denied' },
    { id: 'other_claim', label: 'other claim', what: 'tools/call on the MCP path of another claim', denied: true, detail: 'HTTP 403 policy_denied' },
    { id: 'other_tool', label: 'tool outside the policy', what: 'write_fraud_report on its own path', denied: confined, detail: confined ? 'HTTP 403 policy_denied' : 'HTTP 200 ok' },
    { id: 'model_key', label: 'model key', what: 'read the model key in the environment', denied: true, detail: 'the variable holds a placeholder: openshell:resolve:env:v1_NVIDIA_API_KEY' },
    { id: 'host_files', label: 'host files', what: 'list /Users and /host', denied: true, detail: '/Users: FileNotFoundError, /host: FileNotFoundError' },
  ],
})

const sandboxEvent = (sequence: number, stage: string, extra = {}) => ({
  sequence,
  time: `2026-11-12T10:00:0${sequence}Z`,
  actor: 'sandbox',
  stage,
  title: `Sandbox ${stage}`,
  inputs: {},
  outputs: {},
  model: null,
  duration: null,
  ...extra,
})

const stub = (handlers: Record<string, () => Response>) => {
  const fetchMock = vi.fn((url: string, init?: RequestInit) => {
    const key = `${init?.method ?? 'GET'} ${url}`
    const handler = handlers[key]
    return Promise.resolve(handler ? handler() : new Response('', { status: 404 }))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

test('a claim with no sandbox event shows no Sandbox section and never asks for one', () => {
  const fetchMock = stub({})
  render(<PipelinePanel claimId="CL-1" />)
  FakeEventSource.all[0].emit({ ...sandboxEvent(1, 'x'), actor: 'tool', stage: 'get_customer_policies' })
  expect(screen.queryByRole('heading', { name: 'Sandbox' })).not.toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([u]) => u === '/claims/CL-1/sandbox')).toBe(false)
})

test('a sandbox event with a 404 from the backend shows no section', async () => {
  const fetchMock = stub({})
  render(<PipelinePanel claimId="CL-1" />)
  FakeEventSource.all[0].emit(sandboxEvent(1, 'created'))
  await vi.waitFor(() => expect(fetchMock.mock.calls.some(([u]) => u === '/claims/CL-1/sandbox')).toBe(true))
  expect(screen.queryByRole('heading', { name: 'Sandbox' })).not.toBeInTheDocument()
})

test('shows the name, the state, the creation time, what the sandbox can reach and what it cannot', async () => {
  stub({ 'GET /claims/CL-1/sandbox': () => Response.json(view()) })
  render(<PipelinePanel claimId="CL-1" />)
  FakeEventSource.all[0].emit(sandboxEvent(1, 'created'))

  const section = await screen.findByLabelText('Sandbox')
  expect(within(section).getByText('claims-cl-1a2b3c4d')).toHaveClass('mono')
  expect(within(section).getByText('running')).toHaveClass('pill-ok')
  expect(within(section).getByText('created in 481 ms')).toBeInTheDocument()
  expect(within(section).getByText('Can reach')).toBeInTheDocument()
  expect(within(section).getByText('Model')).toBeInTheDocument()
  expect(within(section).getByText('integrate.api.nvidia.com')).toBeInTheDocument()
  expect(within(section).getByText('POST /v1/chat/completions')).toBeInTheDocument()
  expect(within(section).getByText('Tools')).toBeInTheDocument()
  expect(within(section).getByText('/mcp/AbCdEf…')).toBeInTheDocument()
  for (const tool of ['assess_and_route', 'check_tampering', 'extract_document']) expect(within(section).getByText(tool)).toHaveClass('pill')
  expect(within(section).getByText('Cannot reach')).toBeInTheDocument()
  expect(
    within(section).getByText('Any other host, any other path of the backend, the host file system. The model key is not in the sandbox: it holds a placeholder.'),
  ).toHaveClass('muted')
  expect(within(section).getByRole('button', { name: 'Run confinement test' })).toBeEnabled()
  expect(within(section).getByText('No request was denied.')).toBeInTheDocument()
  // right under the graph strip
  const strip = screen.getByRole('list', { name: 'Agent graph' })
  expect(strip.compareDocumentPosition(section) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
})

test('the confinement test calls the probe and shows five results, denied is the good one', async () => {
  const fetchMock = stub({
    'GET /claims/CL-1/sandbox': () => Response.json(view()),
    'POST /claims/CL-1/sandbox/probe': () => Response.json(probe(false)),
  })
  render(<PipelinePanel claimId="CL-1" />)
  FakeEventSource.all[0].emit(sandboxEvent(1, 'created'))
  fireEvent.click(await screen.findByRole('button', { name: 'Run confinement test' }))
  expect(screen.getByRole('button', { name: 'Running…' })).toBeDisabled()

  const list = await screen.findByRole('list', { name: 'Confinement test' })
  const rows = within(list).getAllByRole('listitem')
  expect(rows).toHaveLength(5)
  expect(within(rows[0]).getByText('denied')).toHaveClass('pill-ok')
  expect(within(rows[0]).getByText('HTTPS request to example.com')).toBeInTheDocument()
  expect(within(rows[2]).getByText('NOT denied')).toHaveClass('pill-bad')
  expect(within(rows[3]).getByText(/openshell:resolve:env:/)).toBeInTheDocument()
  expect(within(list).getAllByText('denied')).toHaveLength(4)
  expect(fetchMock.mock.calls.find(([u]) => u.endsWith('/probe'))![1]).toEqual({ method: 'POST' }) // no body: nothing to inject
  expect(screen.getByRole('button', { name: 'Run confinement test' })).toBeEnabled()
})

test('a probe that fails says so', async () => {
  stub({
    'GET /claims/CL-1/sandbox': () => Response.json(view()),
    'POST /claims/CL-1/sandbox/probe': () => new Response('', { status: 409 }),
  })
  render(<PipelinePanel claimId="CL-1" />)
  FakeEventSource.all[0].emit(sandboxEvent(1, 'created'))
  fireEvent.click(await screen.findByRole('button', { name: 'Run confinement test' }))
  expect(await screen.findByText('The sandbox is not running.')).toHaveClass('pill-bad')
  expect(screen.queryByRole('list', { name: 'Confinement test' })).not.toBeInTheDocument()
})

test('a closed claim: the state is deleted and the button is off', async () => {
  stub({ 'GET /claims/CL-1/sandbox': () => Response.json(view('deleted')) })
  render(<PipelinePanel claimId="CL-1" />)
  FakeEventSource.all[0].emit(sandboxEvent(1, 'deleted'))
  expect(await screen.findByText('deleted', { selector: '.pill' })).toHaveClass('pill-idle')
  expect(screen.getByRole('button', { name: 'Sandbox deleted' })).toBeDisabled()
})

test('the denied requests are a table of time, what and reason, from the denied events', async () => {
  stub({ 'GET /claims/CL-1/sandbox': () => Response.json(view()) })
  render(<PipelinePanel claimId="CL-1" />)
  const source = FakeEventSource.all[0]
  source.emit(sandboxEvent(1, 'created'))
  source.emit(sandboxEvent(2, 'denied', { title: 'Denied: example.com:443', outputs: { reason: 'transparent_tcp_policy_denied' } }))
  source.emit(sandboxEvent(3, 'turn'))
  const section = await screen.findByLabelText('Sandbox')
  expect(within(section).queryByText('No request was denied.')).not.toBeInTheDocument()
  const table = within(section).getByRole('table')
  expect(within(table).getAllByRole('columnheader').map((h) => h.textContent)).toEqual(['Time', 'What', 'Reason'])
  const [, row] = within(table).getAllByRole('row')
  const cells = within(row).getAllByRole('cell')
  expect(cells[0].textContent).toBe(new Date('2026-11-12T10:00:02Z').toLocaleTimeString())
  expect(cells[1]).toHaveTextContent('example.com:443')
  expect(cells[1]).not.toHaveTextContent('Denied:')
  expect(cells[2]).toHaveTextContent('transparent_tcp_policy_denied')
})

test('the section reloads when a sandbox event arrives', async () => {
  let state: 'running' | 'deleted' = 'running'
  const fetchMock = stub({ 'GET /claims/CL-1/sandbox': () => Response.json(view(state)) })
  render(<PipelinePanel claimId="CL-1" />)
  const source = FakeEventSource.all[0]
  source.emit(sandboxEvent(1, 'created'))
  expect(await screen.findByText('running', { selector: '.pill' })).toBeInTheDocument()
  const loads = () => fetchMock.mock.calls.filter(([u]) => u === '/claims/CL-1/sandbox').length
  expect(loads()).toBe(1)

  source.emit({ ...sandboxEvent(2, 'x'), actor: 'tool', stage: 'run_checks' }) // not a sandbox event
  expect(loads()).toBe(1)
  state = 'deleted'
  source.emit(sandboxEvent(3, 'deleted'))
  expect(await screen.findByText('deleted', { selector: '.pill' })).toBeInTheDocument()
  expect(loads()).toBe(2)
})
