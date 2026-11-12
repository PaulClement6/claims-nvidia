import { render, screen, within } from '@testing-library/react'
import { expect, test } from 'vitest'
import GraphStrip, { nodeStates } from './GraphStrip'

const node = (stage: string) => ({ actor: 'workflow', stage })

test('shows the five nodes in order', () => {
  render(<GraphStrip events={[]} />)
  const items = within(screen.getByRole('list', { name: 'Agent graph' })).getAllByRole('listitem')
  expect(items.map((i) => i.querySelector('.graph-node')?.firstChild?.textContent)).toEqual([
    'Intake',
    'Documents',
    'Checks',
    'Route',
    'Answer',
  ])
})

test('with no workflow event every node is outlined and not yet run', () => {
  expect(Object.values(nodeStates([{ actor: 'tool', stage: 'run_checks' }]))).toEqual(Array(5).fill('todo'))
})

test('the last node that started is current, the ones before are done, the rest wait', () => {
  expect(nodeStates([node('intake'), node('intake'), node('documents')])).toEqual({
    intake: 'done',
    documents: 'current',
    checks: 'todo',
    route: 'todo',
    answer: 'todo',
  })
})

test('a node that runs again stays current, and a closed claim ends on the answer', () => {
  const all = ['intake', 'documents', 'documents', 'checks', 'route', 'answer', 'answer'].map(node)
  expect(nodeStates(all)).toEqual({
    intake: 'done',
    documents: 'done',
    checks: 'done',
    route: 'done',
    answer: 'current',
  })
})

test('other events do not move the graph', () => {
  const events = [node('documents'), { actor: 'agent', stage: 'llm' }, { actor: 'tool', stage: 'extract_document' }]
  expect(nodeStates(events).documents).toBe('current')
  // a stage that is not a node of the graph is ignored, even on a workflow event
  expect(nodeStates([node('documents'), node('something_else')]).documents).toBe('current')
})

test('the state is in the markup: not by colour alone', () => {
  render(<GraphStrip events={[node('intake'), node('documents')]} />)
  const current = screen.getByText('Documents').closest('.graph-node')
  expect(current).toHaveAttribute('data-state', 'current')
  expect(current).toHaveAttribute('aria-current', 'step')
  expect(screen.getByText('Intake').closest('.graph-node')).toHaveAttribute('data-state', 'done')
  expect(screen.getByText('Intake')).toHaveTextContent('(done)')
  expect(screen.getByText('Answer').closest('.graph-node')).toHaveAttribute('data-state', 'todo')
})
