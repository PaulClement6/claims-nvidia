/// <reference types="node" />
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { expect, test } from 'vitest'
import ClaimMap from './ClaimMap'
import { ARROWS, BOXES, ISSUES_URL, TASKS, ZONES } from './mapModel'

// vitest runs from frontend/
const repoRoot = resolve(process.cwd(), '..')
const box = (title: string) => screen.getByRole('button', { name: new RegExp(`^${title}`) })

test('every zone and every box of the model renders', () => {
  render(<ClaimMap />)
  for (const z of ZONES) expect(screen.getByRole('heading', { name: `${z.num} · ${z.name}` })).toBeInTheDocument()
  for (const b of BOXES) expect(box(b.title)).toBeInTheDocument()
})

test('the boxes of the intern carry the tag YOURS, the planned one LATER', () => {
  render(<ClaimMap />)
  const yours = BOXES.filter((b) => b.status === 'yours')
  expect(yours.map((b) => b.title)).toEqual(['Document reading', 'Rails', 'Vision model', 'Evaluation'])
  for (const b of yours) expect(within(box(b.title)).getByText('YOURS')).toBeInTheDocument()
  expect(within(box('Rails')).getByText('NEW')).toBeInTheDocument()
  expect(within(box('Specialised chain')).getByText('LATER')).toBeInTheDocument()
  expect(within(box('Checks')).queryByText('YOURS')).toBeNull()
})

test('Document reading is selected at first, with its files and issue links', () => {
  render(<ClaimMap />)
  expect(box('Document reading')).toHaveAttribute('aria-pressed', 'true')
  const detail = screen.getByRole('region', { name: 'Detail of Document reading' })
  expect(within(detail).getByText('backend/app/docintel/reader.py').tagName).toBe('CODE')
  const link = within(detail).getByRole('link', { name: /#18/ })
  expect(link).toHaveAttribute('href', `${ISSUES_URL}/18`)
  expect(link).toHaveAttribute('target', '_blank')
  expect(link.getAttribute('rel')).toContain('noopener')
})

test('selecting another box shows its detail and its check', () => {
  render(<ClaimMap />)
  fireEvent.click(box('Rails'))
  expect(box('Rails')).toHaveAttribute('aria-pressed', 'true')
  expect(box('Document reading')).toHaveAttribute('aria-pressed', 'false')
  const detail = screen.getByRole('region', { name: 'Detail of Rails' })
  expect(within(detail).getByRole('link', { name: /#62/ })).toHaveAttribute('href', `${ISSUES_URL}/62`)
  fireEvent.click(box('Backend'))
  expect(screen.getByText('cd backend && uv run pytest').tagName).toBe('CODE')
})

test('the filter dims the boxes that are not for the intern', () => {
  render(<ClaimMap />)
  const toggle = screen.getByRole('checkbox', { name: 'Show only my boxes' })
  expect(box('Checks')).toHaveAttribute('data-dim', 'false')
  fireEvent.click(toggle)
  expect(box('Checks')).toHaveAttribute('data-dim', 'true')
  expect(box('Rails')).toHaveAttribute('data-dim', 'false')
  expect(box('Evaluation')).toHaveAttribute('data-dim', 'false')
  expect(box('Specialised chain')).toHaveAttribute('data-dim', 'true')
})

test('the task list has the 7 issues in order, each a link to its issue', () => {
  render(<ClaimMap />)
  const list = screen.getByRole('list', { name: 'Your tasks' })
  const links = within(list).getAllByRole('link')
  expect(links.map((a) => a.getAttribute('href'))).toEqual(
    [17, 18, 19, 20, 21, 22, 62].map((n) => `${ISSUES_URL}/${n}`),
  )
  expect(TASKS).toHaveLength(7)
})

test('every arrow joins two boxes of the model', () => {
  const ids = new Set(BOXES.map((b) => b.id))
  for (const a of ARROWS) {
    expect(ids.has(a.from)).toBe(true)
    expect(ids.has(a.to)).toBe(true)
  }
})

test('every file path of the model exists in the repository', () => {
  const paths = BOXES.flatMap((b) => b.files).filter((p) => !p.startsWith('../'))
  expect(paths.length).toBeGreaterThan(20)
  for (const p of paths) expect(existsSync(resolve(repoRoot, p)), p).toBe(true)
})

test('the page never uses the claim outcome word "rejected"', () => {
  const { container } = render(<ClaimMap />)
  for (const b of BOXES) fireEvent.click(box(b.title))
  expect(container.textContent).not.toMatch(/rejected/i)
})
