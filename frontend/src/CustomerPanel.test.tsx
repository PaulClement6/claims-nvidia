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

test('shows the claim line and the placeholder once the claim has a policy', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string) =>
      Promise.resolve(
        url === '/claims/CL-1' ? Response.json(claim({ number: 'TRV-2026-0417' }, 'travel')) : Response.json([]),
      ),
    ),
  )
  render(<CustomerPanel claimId="CL-1" onClaim={() => {}} />)
  expect(await screen.findByText('Claim CL-1 · TRV-2026-0417 · travel')).toBeInTheDocument()
  expect(screen.getByText('The chat comes next.')).toBeInTheDocument()
})
