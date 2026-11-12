import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import CustomerPanel from './CustomerPanel'

afterEach(() => vi.unstubAllGlobals())

const policy = (number: string, line: string) => ({ number, line_of_business: line })

test('pick an account, filter the policies, pick one and create the claim', async () => {
  const fetchMock = vi.fn((url: string, init?: RequestInit) => {
    if (url === '/customers') return Promise.resolve(Response.json([{ id: 'C-1001', name: 'Anna Keller' }]))
    if (url === '/customers/C-1001/policies')
      return Promise.resolve(Response.json([policy('TRV-2026-0417', 'Travel'), policy('HH-2025-1182', 'Household')]))
    if (url === '/claims' && init?.method === 'POST')
      return Promise.resolve(Response.json({ id: 'CL-1', type: 'travel', policy: { number: 'TRV-2026-0417' } }, { status: 201 }))
    return Promise.reject(new Error(url))
  })
  vi.stubGlobal('fetch', fetchMock)
  const onClaim = vi.fn()
  render(<CustomerPanel claimId={null} onClaim={onClaim} />)

  expect(screen.getByText('Demo. Do not upload a real document.')).toBeInTheDocument()
  fireEvent.click(await screen.findByRole('button', { name: 'Anna Keller' }))
  expect(await screen.findByRole('button', { name: /HH-2025-1182/ })).toBeInTheDocument()

  fireEvent.change(screen.getByRole('textbox', { name: 'Filter policies' }), { target: { value: 'travel' } })
  expect(screen.queryByRole('button', { name: /HH-2025-1182/ })).not.toBeInTheDocument()

  fireEvent.click(screen.getByRole('button', { name: /TRV-2026-0417/ }))
  await vi.waitFor(() => expect(onClaim).toHaveBeenCalledWith('CL-1'))
  const post = fetchMock.mock.calls.find(([u]) => u === '/claims')![1]!
  expect(JSON.parse(post.body as string)).toEqual({ customer_id: 'C-1001', policy_number: 'TRV-2026-0417' })
})

test('shows the claim line and the placeholder once the claim exists', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn((url: string) =>
      Promise.resolve(
        url === '/claims/CL-1'
          ? Response.json({ id: 'CL-1', type: 'travel', policy: { number: 'TRV-2026-0417' } })
          : Response.json([]),
      ),
    ),
  )
  render(<CustomerPanel claimId="CL-1" onClaim={() => {}} />)
  expect(await screen.findByText('Claim CL-1 · TRV-2026-0417 · travel')).toBeInTheDocument()
  expect(screen.getByText('The chat comes next.')).toBeInTheDocument()
})
