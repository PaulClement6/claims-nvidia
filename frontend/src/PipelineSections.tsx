// The claim sections of the pipeline panel: outcome, checks and signals, extracted data.

export type Source = { document_id: string; page: number; snippet: string | null }
export type Field = { name: string; value: unknown; source: { page: number; snippet: string | null } }
export type Doc = { id: string; file: string; document_type: string; fields: Field[]; file_metadata: { original_name?: string } }
export type Signal = { code: string; title: string; severity: string; points: number; detail: string; evidence: Source[] }
export type PipelineClaim = {
  documents: Doc[]
  signals: Signal[]
  score: number | null
  outcome: string | null
  outcome_reasons: string[]
}

const OUTCOME_PILL: Record<string, string> = { straight_through: 'pill-ok', handler_review: 'pill-warn', investigation: 'pill-bad' }
const SEVERITY_PILL: Record<string, string> = { low: 'pill-idle', medium: 'pill-warn', high: 'pill-bad' }

export function OutcomeSection({ claim }: { claim: PipelineClaim }) {
  if (!claim.outcome) return null
  return (
    <div className="stack" aria-label="Outcome">
      <h3>Outcome</h3>
      <p>
        <span className="mono">Score {claim.score}</span> <span className={`pill ${OUTCOME_PILL[claim.outcome] ?? 'pill-idle'}`}>{claim.outcome}</span>
      </p>
      {claim.outcome_reasons.length > 0 && (
        <ul>
          {claim.outcome_reasons.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      )}
    </div>
  )
}

const docName = (docs: Doc[], id: string) => {
  const d = docs.find((x) => x.id === id)
  return d ? d.file_metadata.original_name || d.file : id
}

// `ran`: the run_checks event arrived. Before it, nothing shows; after it, an empty list is "No signal."
export function ChecksSection({ claim, ran }: { claim: PipelineClaim; ran: boolean }) {
  if (!ran) return null
  return (
    <div className="stack" aria-label="Checks and signals">
      <h3>Checks and signals</h3>
      {claim.signals.length === 0 ? (
        <p className="muted">No signal.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Code</th>
              <th>Title</th>
              <th>Severity</th>
              <th>Points</th>
              <th>Evidence</th>
            </tr>
          </thead>
          <tbody>
            {claim.signals.map((s) => (
              <tr key={s.code}>
                <td className="mono">{s.code}</td>
                <td>{s.title}</td>
                <td>
                  <span className={`pill ${SEVERITY_PILL[s.severity] ?? 'pill-idle'}`}>{s.severity}</span>
                </td>
                <td className="mono">{s.points}</td>
                <td className="muted">
                  {s.evidence.length === 0
                    ? s.detail
                    : s.evidence.map((e) => <div key={`${e.document_id}-${e.page}`}>{`${docName(claim.documents, e.document_id)} · page ${e.page}`}</div>)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

export function ExtractedSection({ docs }: { docs: Doc[] }) {
  if (docs.length === 0) return null
  return (
    <div className="stack" aria-label="Extracted data">
      <h3>Extracted data</h3>
      {docs.map((d) => (
        <div className="card" key={d.id}>
          <p>
            <span className="mono">{d.file_metadata.original_name || d.file}</span> <span className="pill pill-idle">{d.document_type}</span>
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
  )
}
