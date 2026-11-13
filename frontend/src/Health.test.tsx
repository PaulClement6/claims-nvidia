import { render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import Health from './Health'

afterEach(() => vi.unstubAllGlobals())

test('shows ok when the backend answers', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ status: 'ok' })))
  render(<Health />)
  expect(await screen.findByText('ok', { selector: '.pill' })).toBeInTheDocument()
  expect(fetch).toHaveBeenCalledWith('/health')
})

test('shows down when the backend is unreachable', async () => {
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('network')))
  render(<Health />)
  expect(await screen.findByText('down', { selector: '.pill' })).toBeInTheDocument()
})

test('shows down when the proxy returns an error', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status: 502 })))
  render(<Health />)
  expect(await screen.findByText('down', { selector: '.pill' })).toBeInTheDocument()
})

test('shows the mode under the backend pill, with the agent model when there is one', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ status: 'ok', mode: 'sandbox', agent_model: 'nvidia/some-model' })))
  render(<Health />)
  expect(await screen.findByText('sandbox', { selector: '.pill' })).toHaveClass('pill-ok')
  expect(screen.getByText('nvidia/some-model')).toHaveClass('mono')
})

test.each([
  ['local', 'pill-idle'],
  ['nim', 'pill-warn'],
])('the %s mode pill is %s and local has no model', async (mode, tone) => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ status: 'ok', mode, agent_model: mode === 'nim' ? 'm-1' : null })))
  render(<Health />)
  expect(await screen.findByText(mode, { selector: '.pill' })).toHaveClass(tone)
  expect(screen.queryByText('m-1') === null).toBe(mode === 'local')
})

test('an old backend with no mode shows no mode pill, and a down backend shows none', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ status: 'ok' })))
  const { unmount } = render(<Health />)
  expect(await screen.findByText('ok', { selector: '.pill' })).toBeInTheDocument()
  expect(screen.queryByText('Mode')).not.toBeInTheDocument()
  unmount()
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('network')))
  render(<Health />)
  expect(await screen.findByText('down', { selector: '.pill' })).toBeInTheDocument()
  expect(screen.queryByText('Mode')).not.toBeInTheDocument()
})
