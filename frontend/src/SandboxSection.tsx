// The Sandbox section of the pipeline panel (issue #15): the policy of the sandbox of
// this claim, a confinement test, and the requests the sandbox denied. It is mounted
// only when the claim has a `sandbox` event, and it reloads when a new one arrives.

import { useEffect, useState } from 'react'
import { send } from './api'

type Event = { sequence: number; time: string; actor: string; stage: string; title: string; outputs: unknown }

type View = {
  name: string
  state: 'running' | 'deleted'
  created_ms: number | null
  policy: {
    model: { host: string; port: number; method: string; path: string }
    gateway: { host: string; port: number; path: string; methods: string[]; tools: string[] }
  }
}
type Result = { id: string; label: string; what: string; denied: boolean; detail: string }

const probeError = (e: Error) =>
  e.message === '409' ? 'The sandbox is not running.' : e.message === '404' ? 'This claim has no sandbox.' : 'The test failed.'

export default function SandboxSection({ claimId, events }: { claimId: string; events: Event[] }) {
  const [view, setView] = useState<View | null>(null)
  const [results, setResults] = useState<Result[] | null>(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const sandbox = events.filter((e) => e.actor === 'sandbox')
  const last = sandbox.reduce((n, e) => Math.max(n, e.sequence), 0)

  useEffect(() => {
    if (last === 0) return
    void send<View>(`/claims/${claimId}/sandbox`)
      .then(setView)
      .catch(() => setView(null))
  }, [claimId, last])

  if (!view) return null
  const closed = view.state === 'deleted'
  const { model, gateway } = view.policy
  const denied = sandbox.filter((e) => e.stage === 'denied')

  const run = () => {
    setRunning(true)
    setError('')
    send<{ results: Result[] }>(`/claims/${claimId}/sandbox/probe`, { method: 'POST' })
      .then((r) => setResults(r.results))
      .catch((e: Error) => setError(probeError(e)))
      .finally(() => setRunning(false))
  }

  return (
    <div className="stack sandbox" aria-label="Sandbox">
      <h3>Sandbox</h3>
      <p className="sandbox-line">
        <span className="mono">{view.name}</span>
        <span className={`pill ${closed ? 'pill-idle' : 'pill-ok'}`}>{view.state}</span>
        {view.created_ms !== null && <span className="muted">created in {view.created_ms} ms</span>}
      </p>
      <h4>Can reach</h4>
      <dl className="reach">
        <dt>Model</dt>
        <dd>
          <span className="mono">{model.host}</span> · <span className="mono">{`${model.method} ${model.path}`}</span>
        </dd>
        <dt>Tools</dt>
        <dd>
          <span className="mono" title={`${gateway.host}:${gateway.port}`}>
            {gateway.path}
          </span>
          <span className="tool-pills">
            {gateway.tools.map((t) => (
              <span key={t} className="pill pill-idle tool-pill mono">
                {t}
              </span>
            ))}
          </span>
        </dd>
      </dl>
      <h4>Cannot reach</h4>
      <p className="muted">
        Any other host, any other path of the backend, the host file system. The model key is not in the sandbox: it holds a placeholder.
      </p>
      <p>
        <button type="button" disabled={closed || running} onClick={run}>
          {closed ? 'Sandbox deleted' : running ? 'Running…' : 'Run confinement test'}
        </button>
      </p>
      {error && <p className="pill pill-bad">{error}</p>}
      {results && (
        <ul className="probe-list" aria-label="Confinement test">
          {results.map((r) => (
            <li key={r.id}>
              <span className={`pill ${r.denied ? 'pill-ok' : 'pill-bad'}`}>{r.denied ? 'denied' : 'NOT denied'}</span>
              <span>{r.what}</span>
              <span className="muted">{r.detail}</span>
            </li>
          ))}
        </ul>
      )}
      <h4>Denied requests</h4>
      {denied.length === 0 ? (
        <p className="muted">No request was denied.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Time</th>
              <th>What</th>
              <th>Reason</th>
            </tr>
          </thead>
          <tbody>
            {denied.map((e) => (
              <tr key={e.sequence}>
                <td className="mono">{new Date(e.time).toLocaleTimeString()}</td>
                <td className="mono">{e.title.replace(/^Denied: /, '')}</td>
                <td className="muted">{(e.outputs as { reason?: string }).reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
