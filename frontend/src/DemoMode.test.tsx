import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import App from './App'
import Chat from './Chat'

afterEach(() => vi.unstubAllGlobals())

const view = { claim_id: 'CL-1', policy_number: 'TRV-1', status: 'open', message: null }
const kit = [{ folder: 'claim-1-travel', file: 'booking-confirmation.pdf', document_type: 'booking_confirmation', size: 2048 }]

test('a sample document is fetched and posted to the upload endpoint', async () => {
  const fetchMock = vi.fn((url: string, init?: RequestInit) => {
    if (url === '/demo/kit') return Promise.resolve(Response.json(kit))
    if (url === '/demo/kit/claim-1-travel/booking-confirmation.pdf') return Promise.resolve(new Response('%PDF-x'))
    if (url === '/claims/CL-1/documents' && init?.method === 'POST') return Promise.resolve(Response.json({}, { status: 201 }))
    if (url === '/claims/CL-1/messages') return Promise.resolve(Response.json([]))
    return Promise.resolve(Response.json(view))
  })
  vi.stubGlobal('fetch', fetchMock)
  render(<Chat claimId="CL-1" />)
  fireEvent.click(screen.getByRole('button', { name: 'Use a sample document' }))
  expect(await screen.findByText('claim-1-travel')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /booking-confirmation.pdf/ }))
  await waitFor(() => expect(fetchMock.mock.calls.some(([u]) => u === '/claims/CL-1/documents')).toBe(true))
  const post = fetchMock.mock.calls.find(([u]) => u === '/claims/CL-1/documents')![1]!
  expect(post.method).toBe('POST')
  expect((post.body as FormData).get('file')).toBeInstanceOf(File)
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
