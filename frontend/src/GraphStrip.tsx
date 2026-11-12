// The graph of the claim agent as a strip: one box per node. It reads only the
// `workflow` events of the claim, which both agents write when a node starts
// (the LangGraph agent in `nim` mode, the scripted agent in `local` mode).

export const NODES = [
  ['intake', 'Intake'],
  ['documents', 'Documents'],
  ['checks', 'Checks'],
  ['route', 'Route'],
  ['answer', 'Answer'],
] as const

export type NodeState = 'done' | 'current' | 'todo'

type Event = { actor: string; stage: string }

// the last node that started is the current one; every other node that started is done
export function nodeStates(events: Event[]): Record<string, NodeState> {
  const started = events.filter((e) => e.actor === 'workflow' && NODES.some(([id]) => id === e.stage)).map((e) => e.stage)
  const current = started[started.length - 1]
  return Object.fromEntries(
    NODES.map(([id]) => [id, id === current ? 'current' : started.includes(id) ? 'done' : 'todo']),
  )
}

const WORDS: Record<NodeState, string> = { done: 'done', current: 'current', todo: 'not yet' }

export default function GraphStrip({ events }: { events: Event[] }) {
  const states = nodeStates(events)
  return (
    <ol className="graph-strip" aria-label="Agent graph">
      {NODES.map(([id, label], i) => (
        <li key={id} className="graph-step">
          <span
            className="graph-node"
            data-state={states[id]}
            aria-current={states[id] === 'current' ? 'step' : undefined}
          >
            {label}
            <span className="sr-only"> ({WORDS[states[id]]})</span>
          </span>
          {i < NODES.length - 1 && (
            <span className="graph-arrow" aria-hidden="true">
              →
            </span>
          )}
        </li>
      ))}
    </ol>
  )
}
