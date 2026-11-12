import { useEffect, useRef, useState } from 'react'

type PipelineEvent = {
  sequence: number
  actor: string
  stage: string
  title: string
  inputs: unknown
  outputs: unknown
  model: string | null
  duration: number | null // seconds
}

type Field = { name: string; value: unknown; source: { page: number; snippet: string | null } }
type Doc = { id: string; file: string; document_type: string; fields: Field[]; file_metadata: { original_name?: string } }

// a new claim remounts the list, so the old events never show under it
export default function PipelinePanel({ claimId }: { claimId: string | null }) {
  return <Events key={claimId ?? ''} claimId={claimId} />
}

function Events({ claimId }: { claimId: string | null }) {
  const [events, setEvents] = useState<PipelineEvent[]>([])
  const [docs, setDocs] = useState<Doc[]>([])
  const end = useRef<HTMLLIElement>(null)

  useEffect(() => {
    if (!claimId) return
    const source = new EventSource(`/claims/${claimId}/events`)
    source.onmessage = (m: MessageEvent<string>) => {
      const e = JSON.parse(m.data) as PipelineEvent
      // the browser reconnects with Last-Event-ID, but a repeat must never show twice
      setEvents((all) => (all.some((x) => x.sequence === e.sequence) ? all : [...all, e]))
      // the extracted fields live in the claim: reload it when a document was read
      if (e.stage === 'extract_document')
        void fetch(`/claims/${claimId}`)
          .then((r) => r.json() as Promise<{ documents: Doc[] }>)
          .then((c) => setDocs(c.documents))
          .catch(() => undefined)
    }
    return () => source.close()
  }, [claimId])

  useEffect(() => {
    end.current?.scrollIntoView?.({ block: 'end' }) // no return: an effect must return a function or nothing
  }, [events])

  return (
    <section className="card panel panel-pipeline" aria-label="Pipeline">
      <h2>Pipeline</h2>
      {claimId && <p className="mono">Claim {claimId}</p>}
      {docs.length > 0 && (
        <div className="stack" aria-label="Extracted data">
          <h3>Extracted data</h3>
          {docs.map((d) => (
            <div className="card" key={d.id}>
              <p>
                <span className="mono">{d.file_metadata.original_name || d.file}</span>{' '}
                <span className="pill pill-idle">{d.document_type}</span>
              </p>
              {d.fields.length === 0 ? (
                <p className="muted">No field read.</p>
              ) : (
                <table>
                  <thead>
                    <tr>
                      <th>Field</th>
                      <th>Value</th>
                      <th>Source</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.fields.map((f) => (
                      <tr key={f.name}>
                        <td>{f.name}</td>
                        <td className="mono">{String(f.value)}</td>
                        <td className="muted">{`page ${f.source.page} · “${f.source.snippet ?? ''}”`}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          ))}
        </div>
      )}
      {events.length === 0 ? (
        <p className="muted">No event yet. Start a claim in the customer panel.</p>
      ) : (
        <ol className="events">
          {events.map((e) => (
            <li key={e.sequence}>
              <details>
                <summary>
                  <span className={`pill ${e.actor === 'tool' ? 'pill-ok' : 'pill-idle'}`}>{e.actor}</span>
                  <span className="event-title">{e.title}</span>
                  {e.model && <span className="mono muted">{e.model}</span>}
                  {e.duration !== null && <span className="mono muted">{Math.round(e.duration * 1000)} ms</span>}
                </summary>
                <p className="muted">Inputs</p>
                <pre className="mono">{JSON.stringify(e.inputs, null, 2)}</pre>
                <p className="muted">Outputs</p>
                <pre className="mono">{JSON.stringify(e.outputs, null, 2)}</pre>
              </details>
            </li>
          ))}
          <li ref={end} />
        </ol>
      )}
    </section>
  )
}
