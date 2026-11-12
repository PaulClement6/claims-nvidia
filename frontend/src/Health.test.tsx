import { render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import Health from './Health'

afterEach(() => vi.unstubAllGlobals())

test('shows ok when the backend answers', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ status: 'ok' })))
  render(<Health />)
  expect(await screen.findByText('Backend: ok')).toBeInTheDocument()
  expect(fetch).toHaveBeenCalledWith('/health')
})

test('shows down when the backend is unreachable', async () => {
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('network')))
  render(<Health />)
  expect(await screen.findByText('Backend: down')).toBeInTheDocument()
})

test('shows down when the proxy returns an error', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status: 502 })))
  render(<Health />)
  expect(await screen.findByText('Backend: down')).toBeInTheDocument()
})
