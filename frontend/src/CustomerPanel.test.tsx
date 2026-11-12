import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, expect, test, vi } from 'vitest'
import CustomerPanel from './CustomerPanel'

afterEach(() => vi.unstubAllGlobals())

const policy = (number: string, line: string) => ({ number, line_of_business: line })

const claim = (policy: { number: string } | null, type: string | null = null) => ({
  id: 'CL-1',
  type,
  policy,
  customer: { id: 'C-1001', name: 'Anna Keller' },
})

test('pick an account to create the claim, then pick a policy to set it', async () => {
  let current = claim(null)
  const fetchMock = vi.fn((url: string, init?: RequestInit) => {
    if (url === '/customers') return Promise.resolve(Response.json([{ id: 'C-1001', name: 'Anna Keller' }]))
    if (url === '/claims' && init?.method === 'POST') return Promise.resolve(Response.json(current, { status: 201 }))
    if (url === '/claims/CL-1' && !init) return Promise.resolve(Response.json(current))
    if (url === '/claims/CL-1/policies')
      return Promise.resolve(Response.json([policy('TRV-2026-0417', 'Travel'), policy('HH-2025-1182', 'Household')]))
    if (url === '/claims/CL-1/policy' && init?.method === 'PUT') {
      current = claim({ number: 'TRV-2026-0417' }, 'travel')
      return Promise.resolve(Response.json(current))
    }
    return Promise.reject(new Error(url))
  })
  vi.stubGlobal('fetch', fetchMock)
  const Host = () => {
    const [id, setId] = useState<string | null>(null)
    return <CustomerPanel claimId={id} onClaim={setId} />
  }
  render(<Host />)

  expect(screen.getByText('Demo. Do not upload a real document.')).toBeInTheDocument()
  fireEvent.click(await screen.findByRole('button', { name: 'Anna Keller' }))
  const post = fetchMock.mock.calls.find(([u]) => u === '/claims')![1]!
  expect(JSON.parse(post.body as string)).toEqual({ customer_id: 'C-1001' })
  expect(await screen.findByRole('button', { name: /HH-2025-1182/ })).toBeInTheDocument()

  fireEvent.change(screen.getByRole('textbox', { name: 'Filter policies' }), { target: { value: 'travel' } })
  expect(screen.queryByRole('button', { name: /HH-2025-1182/ })).not.toBeInTheDocument()

  fireEvent.click(screen.getByRole('button', { name: /TRV-2026-0417/ }))
  expect(await screen.findByText('Claim CL-1 · TRV-2026-0417 · travel')).toBeInTheDocument()
  const put = fetchMock.mock.calls.find(([, i]) => i?.method === 'PUT')![1]!
  expect(JSON.parse(put.body as string)).toEqual({ policy_number: 'TRV-2026-0417' })
})

const withChat = (extra?: (url: string, init?: RequestInit) => Response | undefined) =>
  vi.fn((url: string, init?: RequestInit) =>
    Promise.resolve(
      extra?.(url, init) ??
        (url === '/claims/CL-1'
          ? Response.json(claim({ number: 'TRV-2026-0417' }, 'travel'))
          : url === '/claims/CL-1/messages'
            ? Response.json([{ role: 'agent', text: 'Hello. What happened?', time: '2026-11-12T10:00:00Z' }])
            : Response.json([])),
    ),
  )

test('shows the claim line, the notice and the greeting once the claim has a policy', async () => {
  vi.stubGlobal('fetch', withChat())
  render(<CustomerPanel claimId="CL-1" onClaim={() => {}} />)
  expect(await screen.findByText('Claim CL-1 · TRV-2026-0417 · travel')).toBeInTheDocument()
  expect(await screen.findByText('Hello. What happened?')).toHaveClass('bubble-agent')
  expect(screen.getByText('Demo. Do not upload a real document.')).toBeInTheDocument()
})

test('Enter sends the message, the input is off while the request runs, the answer shows', async () => {
  let answered = false
  let release: () => void = () => {}
  const gate = new Promise<void>((r) => (release = r))
  const base = withChat((url, init) =>
    url === '/claims/CL-1/messages' && !init && answered
      ? Response.json([
          { role: 'customer', text: 'I was ill', time: 't' },
          { role: 'agent', text: 'On what date?', time: 't' },
        ])
      : undefined,
  )
  const fetchMock = vi.fn((url: string, init?: RequestInit) =>
    init?.method === 'POST' ? gate.then(() => ((answered = true), Response.json([]))) : base(url, init),
  )
  vi.stubGlobal('fetch', fetchMock)
  render(<CustomerPanel claimId="CL-1" onClaim={() => {}} />)

  const input = await screen.findByRole('textbox', { name: 'Message' })
  fireEvent.change(input, { target: { value: 'I was ill' } })
  fireEvent.submit(input.closest('form')!)

  expect(await screen.findByText('I was ill')).toHaveClass('bubble-customer')
  expect(input).toBeDisabled()
  const post = fetchMock.mock.calls.find(([, i]) => i?.method === 'POST')!
  expect(post[0]).toBe('/claims/CL-1/messages')
  expect(JSON.parse(post[1]!.body as string)).toEqual({ text: 'I was ill' })

  release()
  expect(await screen.findByText('On what date?')).toHaveClass('bubble-agent')
  expect(input).toBeEnabled()
  expect(input).toHaveValue('')
})

test('uploads a file with the button and with drag and drop', async () => {
  const fetchMock = withChat((_, init) => (init?.method === 'POST' ? Response.json({ id: 'D-1' }, { status: 201 }) : undefined))
  vi.stubGlobal('fetch', fetchMock)
  render(<CustomerPanel claimId="CL-1" onClaim={() => {}} />)

  const input = await screen.findByLabelText('Upload a document')
  const file = new File(['%PDF-'], 'booking.pdf', { type: 'application/pdf' })
  fireEvent.change(input, { target: { files: [file] } })
  expect(screen.getByRole('textbox', { name: 'Message' })).toBeDisabled()
  await vi.waitFor(() => expect(screen.getByRole('textbox', { name: 'Message' })).toBeEnabled())

  const second = new File(['%PDF-'], 'cert.pdf', { type: 'application/pdf' })
  fireEvent.drop(screen.getByRole('log').parentElement!, { dataTransfer: { files: [second] } })

  await vi.waitFor(() => expect(fetchMock.mock.calls.filter(([, i]) => i?.method === 'POST')).toHaveLength(2))
  const posts = fetchMock.mock.calls.filter(([, i]) => i?.method === 'POST')
  expect(posts.map(([u]) => u)).toEqual(['/claims/CL-1/documents', '/claims/CL-1/documents'])
  expect((posts[0][1]!.body as FormData).get('file')).toBe(file)
  expect((posts[1][1]!.body as FormData).get('file')).toBe(second)
})
