import { render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import App from './App'

afterEach(() => vi.unstubAllGlobals())

test('shows the brand, the two panels and the backend health', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ status: 'ok' })))
  render(<App />)
  expect(screen.getByText('Claims')).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Customer' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: 'Pipeline' })).toBeInTheDocument()
  expect(await screen.findByText('Backend: ok')).toBeInTheDocument()
})

test('the theme button switches and stores the theme', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ status: 'ok' })))
  document.documentElement.dataset.theme = 'dark'
  render(<App />)
  screen.getByRole('button', { name: 'Light theme' }).click()
  expect(await screen.findByRole('button', { name: 'Dark theme' })).toBeInTheDocument()
  expect(document.documentElement.dataset.theme).toBe('light')
  expect(localStorage.getItem('theme')).toBe('light')
})
