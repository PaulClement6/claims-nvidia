import { useEffect, useRef, useState } from 'react'
import GraphStrip from './GraphStrip'
import SandboxSection from './SandboxSection'
import { ChecksSection, ExtractedSection, FraudReportSection, OutcomeSection } from './PipelineSections'
import type { PipelineClaim } from './PipelineSections'

type PipelineEvent = {
  sequence: number
  time: string
  actor: string
  stage: string
  title: string
  inputs: unknown
  outputs: unknown
  model: string | null
  duration: number | null // seconds
}

// The sandbox reports its node and model events after the turn, with the time they happened:
// show the events in the order of that time (a tie keeps the order they were stored in).
const byTime = (a: PipelineEvent, b: PipelineEvent) => Date.parse(a.time) - Date.parse(b.time) || a.sequence - b.sequence

// tool events are the trusted ones (ok); a sandbox event: a denial warns, a failure is bad,
// a probe is ok when the try was denied, and the life cycle (created, turn, deleted) is idle
const pillOf = (e: PipelineEvent) => {
  if (e.actor === 'tool') return 'pill-ok'
  if (e.actor !== 'sandbox') return 'pill-idle'
  if (e.stage === 'denied') return 'pill-warn'
  if (e.stage === 'failed') return 'pill-bad'
  if (e.stage === 'probe') return (e.outputs as { denied?: boolean }).denied === false ? 'pill-bad' : 'pill-ok'
  return 'pill-idle'
}

const RELOAD = ['extract_document', 'run_checks', 'check_tampering', 'check_consistency', 'assess_and_route', 'write_fraud_report']

// a new claim remounts the list, so the old events never show under it
export default function PipelinePanel({ claimId }: { claimId: string | null }) {
  return <Events key={claimId ?? ''} claimId={claimId} />
}

function Events({ claimId }: { claimId: string | null }) {
  const [events, setEvents] = useState<PipelineEvent[]>([])
  const [claim, setClaim] = useState<PipelineClaim | null>(null)
  const end = useRef<HTMLLIElement>(null)

  useEffect(() => {
    if (!claimId) return
    const source = new EventSource(`/claims/${claimId}/events`)
    source.onmessage = (m: MessageEvent<string>) => {
      const e = JSON.parse(m.data) as PipelineEvent
      // the browser reconnects with Last-Event-ID, but a repeat must never show twice
      setEvents((all) => (all.some((x) => x.sequence === e.sequence) ? all : [...all, e].sort(byTime)))
      // the fields, signals and outcome live in the claim: reload it when one of them changed
      if (RELOAD.includes(e.stage))
        void fetch(`/claims/${claimId}`)
          .then((r) => r.json() as Promise<PipelineClaim>)
          .then(setClaim)
          .catch(() => undefined)
    }
    return () => source.close()
  }, [claimId])

  useEffect(() => {
    end.current?.scrollIntoView?.({ block: 'end' }) // no return: an effect must return a function or nothing
  }, [events])

  // the checks ran when their event arrived without an error
  const checksRan = events.some((e) => e.stage === 'run_checks' && !(e.outputs as { error?: string }).error)

  return (
    <section className="card panel panel-pipeline" aria-label="Pipeline">
      <header className="panel-head">
        <span className="eyebrow">Pipeline · live</span>
        {claimId && <span className="mono muted">Claim {claimId}</span>}
      </header>
      <div className="panel-body">
        {claimId && <GraphStrip events={events} />}
        {claimId && <SandboxSection claimId={claimId} events={events} />}
        {claim?.fraud_report && <FraudReportSection report={claim.fraud_report} />}
        {claim && <OutcomeSection claim={claim} />}
        {claim && <ChecksSection claim={claim} ran={checksRan} />}
        {claim && <ExtractedSection docs={claim.documents} claimId={claimId!} />}
        <h3>Events</h3>
        {events.length === 0 ? (
          <p className="muted">No event yet. Start a claim in the customer panel.</p>
        ) : (
          <ol className="events">
            {events.map((e) => (
              <li key={e.sequence}>
                <details>
                  <summary>
                    <span className={`pill ${pillOf(e)}`}>{e.actor}</span>
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
      </div>
    </section>
  )
}
