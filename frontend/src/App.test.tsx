import { fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import App from './App'

afterEach(() => vi.unstubAllGlobals())

const stub = (health: object = { status: 'ok' }) =>
  vi.stubGlobal('fetch', vi.fn((url: string) => Promise.resolve(Response.json(url === '/health' ? health : []))))

test('shows the brand, the two panels and the backend health', async () => {
  stub()
  render(<App />)
  expect(screen.getByText('Claims')).toBeInTheDocument()
  expect(screen.getByRole('region', { name: 'Customer' })).toBeInTheDocument()
  expect(screen.getByRole('region', { name: 'Pipeline' })).toBeInTheDocument()
  expect(await screen.findByText('ok', { selector: '.pill' })).toBeInTheDocument()
})

test('the nav is built from the pages: the demo first, then the map, and a page header', async () => {
  stub({ status: 'ok', mode: 'sandbox' })
  render(<App />)
  const nav = screen.getByRole('navigation', { name: 'Main' })
  expect(within(nav).getAllByRole('button').map((b) => b.textContent)).toEqual(['Claim demo', 'Map'])
  expect(within(nav).getByRole('button', { name: 'Claim demo' })).toHaveAttribute('aria-current', 'page')
  expect(screen.getByRole('heading', { level: 1, name: 'Claim demo' })).toBeInTheDocument()
  expect(await screen.findByText('Demo · mode sandbox')).toBeInTheDocument()
  fireEvent.click(within(nav).getByRole('button', { name: 'Map' }))
  expect(within(nav).getByRole('button', { name: 'Map' })).toHaveAttribute('aria-current', 'page')
  expect(screen.getByRole('heading', { level: 1, name: /How a claim is/ })).toBeInTheDocument()
})

test('the customer panel says what the customer sees and never names the fraud checks', () => {
  stub()
  render(<App />)
  const customer = screen.getByRole('region', { name: 'Customer' })
  expect(within(customer).getByText('Customer view')).toBeInTheDocument()
  expect(customer).not.toHaveTextContent(/fraud/i)
})

test('the theme button switches and stores the theme', async () => {
  stub()
  document.documentElement.dataset.theme = 'dark'
  render(<App />)
  screen.getByRole('button', { name: 'Light theme' }).click()
  expect(await screen.findByRole('button', { name: 'Dark theme' })).toBeInTheDocument()
  expect(document.documentElement.dataset.theme).toBe('light')
  expect(localStorage.getItem('theme')).toBe('light')
})
