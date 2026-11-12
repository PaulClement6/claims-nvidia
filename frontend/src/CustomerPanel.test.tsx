import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, expect, test, vi } from 'vitest'
import CustomerPanel from './CustomerPanel'

afterEach(() => vi.unstubAllGlobals())

const policy = (number: string, line: string) => ({ number, line_of_business: line })

// what the customer endpoint gives: nothing else
const view = (policy_number: string | null) => ({ claim_id: 'CL-1', policy_number, status: 'open', message: null })

test('pick an account to create the claim, then pick a policy to set it', async () => {
  let picked = false
  const fetchMock = vi.fn((url: string, init?: RequestInit) => {
    if (url === '/customers') return Promise.resolve(Response.json([{ id: 'C-1001', name: 'Anna Keller' }]))
    if (url === '/claims' && init?.method === 'POST') return Promise.resolve(Response.json(view(null), { status: 201 }))
    if (url === '/claims/CL-1/customer') return Promise.resolve(Response.json(view(picked ? 'TRV-2026-0417' : null)))
    if (url === '/claims/CL-1/policies')
      return Promise.resolve(Response.json([policy('TRV-2026-0417', 'Travel'), policy('HH-2025-1182', 'Household')]))
    if (url === '/claims/CL-1/policy' && init?.method === 'PUT') {
      picked = true
      return Promise.resolve(Response.json(view('TRV-2026-0417')))
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
  expect(await screen.findByText('Claim CL-1 · TRV-2026-0417')).toBeInTheDocument()
  const put = fetchMock.mock.calls.find(([, i]) => i?.method === 'PUT')![1]!
  expect(JSON.parse(put.body as string)).toEqual({ policy_number: 'TRV-2026-0417' })
})

const withChat = (extra?: (url: string, init?: RequestInit) => Response | undefined) =>
  vi.fn((url: string, init?: RequestInit) =>
    Promise.resolve(
      extra?.(url, init) ??
        (url === '/claims/CL-1/customer'
          ? Response.json(view('TRV-2026-0417'))
          : url === '/claims/CL-1/messages'
            ? Response.json([{ role: 'agent', text: 'Hello. What happened?', time: '2026-11-12T10:00:00Z' }])
            : Response.json([])),
    ),
  )

test('shows the claim line, the notice and the greeting once the claim has a policy', async () => {
  vi.stubGlobal('fetch', withChat())
  render(<CustomerPanel claimId="CL-1" onClaim={() => {}} />)
  expect(await screen.findByText('Claim CL-1 · TRV-2026-0417')).toBeInTheDocument()
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

test('the customer panel never asks for the pipeline view or the events', async () => {
  const fetchMock = withChat((_, init) => (init?.method === 'POST' ? Response.json({ id: 'D-1' }, { status: 201 }) : undefined))
  vi.stubGlobal('fetch', fetchMock)
  render(<CustomerPanel claimId="CL-1" onClaim={() => {}} />)
  const input = await screen.findByRole('textbox', { name: 'Message' })
  fireEvent.change(input, { target: { value: 'I was ill' } })
  fireEvent.submit(input.closest('form')!)
  await vi.waitFor(() => expect(input).toBeEnabled())
  fireEvent.change(screen.getByLabelText('Upload a document'), {
    target: { files: [new File(['%PDF-'], 'a.pdf', { type: 'application/pdf' })] },
  })
  await vi.waitFor(() => expect(input).toBeEnabled())

  const urls = fetchMock.mock.calls.map(([u]) => u)
  expect(urls).toContain('/claims/CL-1/customer')
  expect(urls).toContain('/claims/CL-1/messages')
  expect(urls.filter((u) => /^\/claims\/[^/]+$/.test(u) || u.endsWith('/events'))).toEqual([])
})

test('the Done button is on only in the documents step and sends "done"', async () => {
  let step = 'ask_amount'
  const fetchMock = withChat((url, init) =>
    url === '/claims/CL-1/customer'
      ? Response.json({ ...view('TRV-2026-0417'), step })
      : init?.method === 'POST'
        ? ((step = 'closed'), Response.json([]))
        : undefined,
  )
  vi.stubGlobal('fetch', fetchMock)
  const { rerender } = render(<CustomerPanel claimId="CL-1" onClaim={() => {}} />)
  const done = await screen.findByRole('button', { name: 'Done' })
  expect(done).toBeDisabled()
  // a disabled Done says when it works: in a title and in text under the buttons
  const help = 'Press Done when you have uploaded your documents.'
  expect(await screen.findByText(help)).toBeVisible()
  expect(done).toHaveAttribute('title', help)

  step = 'ask_documents'
  rerender(<CustomerPanel claimId="CL-1" onClaim={() => {}} key="again" />)
  await vi.waitFor(() => expect(screen.getByRole('button', { name: 'Done' })).toBeEnabled())
  expect(screen.queryByText(help)).not.toBeInTheDocument() // only before the documents step
  expect(screen.getByRole('button', { name: 'Done' })).not.toHaveAttribute('title')

  fireEvent.click(screen.getByRole('button', { name: 'Done' }))
  const post = fetchMock.mock.calls.find(([, i]) => i?.method === 'POST')!
  expect(post[0]).toBe('/claims/CL-1/messages')
  expect(JSON.parse(post[1]!.body as string)).toEqual({ text: 'done' })
  expect(await screen.findByText('done')).toHaveClass('bubble-customer')
  await vi.waitFor(() => expect(screen.getByRole('button', { name: 'Done' })).toBeDisabled())
})

test('an agent bubble with three dots shows while a request runs, and goes when the answer arrives or fails', async () => {
  let release: () => void = () => {}
  let gate = new Promise<void>((r) => (release = r))
  let fail = false
  const base = withChat()
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) =>
      init?.method === 'POST' ? gate.then(() => (fail ? Promise.reject(new Error('down')) : Response.json([]))) : base(url, init),
    ),
  )
  render(<CustomerPanel claimId="CL-1" onClaim={() => {}} />)
  const input = await screen.findByRole('textbox', { name: 'Message' })
  expect(screen.queryByText('The agent is typing')).not.toBeInTheDocument()

  fireEvent.change(input, { target: { value: 'I was ill' } })
  fireEvent.submit(input.closest('form')!)
  const typing = (await screen.findByText('The agent is typing')).closest('p')!
  expect(typing).toHaveClass('bubble', 'bubble-agent', 'typing')
  expect(typing.querySelectorAll('.typing-dots i')).toHaveLength(3)
  expect(typing.querySelector('.typing-still')).toHaveTextContent('…') // shown instead of the dots when motion is off
  expect(screen.getByRole('log').lastElementChild).toBe(typing) // after the last message
  release()
  await vi.waitFor(() => expect(screen.queryByText('The agent is typing')).not.toBeInTheDocument())

  fail = true
  gate = new Promise<void>((r) => (release = r))
  fireEvent.change(input, { target: { value: 'again' } })
  fireEvent.submit(input.closest('form')!)
  expect(await screen.findByText('The agent is typing')).toBeInTheDocument()
  release()
  expect(await screen.findByText('The message was not sent.')).toBeInTheDocument()
  expect(screen.queryByText('The agent is typing')).not.toBeInTheDocument()
})

test('the typing dots show while the policy is being set, then the chat takes over', async () => {
  let release: () => void = () => {}
  const gate = new Promise<void>((r) => (release = r))
  let picked = false
  const base = withChat()
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      if (url === '/claims/CL-1/customer') return Promise.resolve(Response.json(view(picked ? 'TRV-2026-0417' : null)))
      if (url === '/claims/CL-1/policies') return Promise.resolve(Response.json([policy('TRV-2026-0417', 'Travel')]))
      if (url === '/claims/CL-1/policy' && init?.method === 'PUT') return gate.then(() => ((picked = true), Response.json(view('TRV-2026-0417'))))
      return base(url, init)
    }),
  )
  render(<CustomerPanel claimId="CL-1" onClaim={() => {}} />)
  fireEvent.click(await screen.findByRole('button', { name: /TRV-2026-0417/ }))

  const typing = (await screen.findByText('The agent is typing')).closest('p')!
  expect(typing).toHaveClass('bubble', 'bubble-agent', 'typing')
  expect(typing.querySelectorAll('.typing-dots i')).toHaveLength(3)
  expect(screen.queryByRole('button', { name: /TRV-2026-0417/ })).not.toBeInTheDocument()

  release()
  expect(await screen.findByText('Hello. What happened?')).toBeInTheDocument()
  expect(screen.queryByText('The agent is typing')).not.toBeInTheDocument()
})

test('the typing dots go and the list comes back when the policy is not set', async () => {
  const base = withChat()
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string, init?: RequestInit) => {
      if (url === '/claims/CL-1/customer') return Promise.resolve(Response.json(view(null)))
      if (url === '/claims/CL-1/policies') return Promise.resolve(Response.json([policy('TRV-2026-0417', 'Travel')]))
      if (url === '/claims/CL-1/policy' && init?.method === 'PUT') return Promise.reject(new Error('down'))
      return base(url, init)
    }),
  )
  render(<CustomerPanel claimId="CL-1" onClaim={() => {}} />)
  fireEvent.click(await screen.findByRole('button', { name: /TRV-2026-0417/ }))
  expect(await screen.findByText('The policy was not set.')).toBeInTheDocument()
  expect(screen.queryByText('The agent is typing')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: /TRV-2026-0417/ })).toBeInTheDocument()
})
