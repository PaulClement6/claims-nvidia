import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import App from './App'
import Chat from './Chat'

afterEach(() => vi.unstubAllGlobals())

const view = { claim_id: 'CL-1', policy_number: 'TRV-1', status: 'open', message: null }
const cases = [
  {
    folder: 'claim-1-travel',
    title: 'Trip cancelled because of illness',
    stories: [{ text: 'My trip was cancelled.' }],
    documents: [
      {
        folder: 'claim-1-travel',
        file: 'booking-confirmation.pdf',
        document_type: 'booking_confirmation',
        hard: [{ folder: 'hard', file: 'b-scan.pdf', document_type: 'booking_confirmation', variant: 'scan' }],
      },
    ],
  },
]

const stub = (list: unknown[] = cases, step = 'ask_documents') => {
  const fetchMock = vi.fn((url: string, init?: RequestInit) => {
    if (url === '/demo/scenarios?policy=TRV-1') return Promise.resolve(Response.json(list))
    if (url === '/demo/kit/claim-1-travel/booking-confirmation.pdf') return Promise.resolve(new Response('%PDF-x'))
    if (url === '/demo/kit/hard/b-scan.pdf') return Promise.resolve(new Response('%PDF-y'))
    if (url === '/claims/CL-1/documents' && init?.method === 'POST') return Promise.resolve(Response.json({}, { status: 201 }))
    if (url === '/claims/CL-1/messages') return Promise.resolve(Response.json([]))
    return Promise.resolve(Response.json({ ...view, step }))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

test('sample cases are closed at first; a story fills the message box and a document uploads', async () => {
  const fetchMock = stub()
  render(<Chat claimId="CL-1" policy="TRV-1" />)
  expect(screen.queryByText('Trip cancelled because of illness')).toBeNull()
  fireEvent.click(await screen.findByRole('button', { name: 'Sample cases (1)' }))
  expect(screen.getByText('Trip cancelled because of illness')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Use this text' }))
  expect(screen.getByRole('textbox', { name: 'Message' })).toHaveValue('My trip was cancelled.')
  expect(fetchMock.mock.calls.some(([u, i]) => u === '/claims/CL-1/messages' && i?.method === 'POST')).toBe(false)
  fireEvent.click(screen.getByRole('button', { name: /booking-confirmation.pdf/ }))
  await waitFor(() => expect(fetchMock.mock.calls.some(([u]) => u === '/claims/CL-1/documents')).toBe(true))
  const post = fetchMock.mock.calls.find(([u]) => u === '/claims/CL-1/documents')![1]!
  expect((post.body as FormData).get('file')).toBeInstanceOf(File)
  expect(await screen.findByLabelText('uploaded')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /booking-confirmation.pdf/ })).toBeEnabled()
})

test('a hard version is a smaller button after its document', async () => {
  const fetchMock = stub()
  render(<Chat claimId="CL-1" policy="TRV-1" />)
  fireEvent.click(await screen.findByRole('button', { name: 'Sample cases (1)' }))
  fireEvent.click(screen.getByRole('button', { name: 'scan' }))
  await waitFor(() => expect(fetchMock.mock.calls.some(([u]) => u === '/demo/kit/hard/b-scan.pdf')).toBe(true))
})

test('no scenario: nothing is shown; closed claim: the uploads are disabled', async () => {
  stub([])
  const { unmount } = render(<Chat claimId="CL-1" policy="TRV-1" />)
  await screen.findByRole('textbox', { name: 'Message' })
  expect(screen.queryByRole('button', { name: /Sample cases/ })).toBeNull()
  unmount()
  stub(cases, 'closed')
  render(<Chat claimId="CL-1" policy="TRV-1" />)
  fireEvent.click(await screen.findByRole('button', { name: 'Sample cases (1)' }))
  await waitFor(() => expect(screen.getByRole('button', { name: /booking-confirmation.pdf/ })).toBeDisabled())
})

test('New claim goes back to the account choice; Reset demo asks, calls the API and starts over', async () => {
  const fetchMock = vi.fn((url: string, init?: RequestInit) => {
    if (url === '/customers') return Promise.resolve(Response.json([{ id: 'C-1003', name: 'Marc' }]))
    if (url === '/claims' && init?.method === 'POST') return Promise.resolve(Response.json(view, { status: 201 }))
    if (url === '/demo/reset') return Promise.resolve(Response.json({ claims: 1 }))
    if (url === '/health') return Promise.resolve(Response.json({ status: 'ok' }))
    if (url.endsWith('/messages') || url.endsWith('/policies')) return Promise.resolve(Response.json([]))
    return Promise.resolve(Response.json(view))
  })
  vi.stubGlobal('fetch', fetchMock)
  vi.stubGlobal('EventSource', class { onmessage = null; close() {} })
  const ask = vi.fn(() => true)
  vi.stubGlobal('confirm', ask)
  render(<App />)
  expect(screen.queryByRole('button', { name: 'New claim' })).toBeNull()
  fireEvent.click(await screen.findByRole('button', { name: 'Marc' }))
  fireEvent.click(await screen.findByRole('button', { name: 'New claim' }))
  expect(await screen.findByRole('button', { name: 'Marc' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Marc' }))
  await screen.findByRole('button', { name: 'New claim' })
  fireEvent.click(screen.getByRole('button', { name: 'Reset demo' }))
  expect(ask).toHaveBeenCalled()
  expect(await screen.findByRole('heading', { name: '1. Pick an account' })).toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([u, i]) => u === '/demo/reset' && i?.method === 'POST')).toBe(true)
})
