// The claim sections of the pipeline panel: fraud report, outcome, checks and signals, extracted data.

export type Source = { document_id: string; page: number; snippet: string | null }
export type Field = { name: string; value: unknown; source: { page: number; snippet: string | null; box?: number[] | null } }
export type Doc = { id: string; file: string; document_type: string; fields: Field[]; file_metadata: { original_name?: string } }
export type Signal = { code: string; title: string; severity: string; points: number; detail: string; evidence: Source[] }
export type Finding = { code: string; title: string; saw: string; where: string[]; expected: string }
export type FraudReport = {
  outcome: string
  score: number
  summary: string
  findings: Finding[]
  not_checked: string[]
  next_steps: string[]
}
export type PipelineClaim = {
  documents: Doc[]
  signals: Signal[]
  score: number | null
  outcome: string | null
  outcome_reasons: string[]
  fraud_report?: FraudReport | null
}

const OUTCOME_PILL: Record<string, string> = { straight_through: 'pill-ok', handler_review: 'pill-warn', investigation: 'pill-bad' }
const SEVERITY_PILL: Record<string, string> = { low: 'pill-idle', medium: 'pill-warn', high: 'pill-bad' }

// Shown only when a report exists (outcome investigation). Readable in one minute.
export function FraudReportSection({ report }: { report: FraudReport }) {
  return (
    <div className="stack" aria-label="Fraud report">
      <h3>Fraud report</h3>
      <p>
        <span className="pill pill-bad">{`${report.outcome} · score ${report.score}`}</span>
      </p>
      <p className="report-summary">{report.summary}</p>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Code</th>
              <th>What the system saw</th>
              <th>Where</th>
              <th>Expected</th>
            </tr>
          </thead>
          <tbody>
            {report.findings.map((f) => (
              <tr key={f.code}>
                <td className="mono">{f.code}</td>
                <td>{f.saw}</td>
                <td className="muted">
                  {f.where.map((w) => (
                    <div key={w}>{w}</div>
                  ))}
                </td>
                <td className="muted">{f.expected}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h4>Not checked</h4>
      <ul>
        {report.not_checked.map((t) => (
          <li key={t}>{t}</li>
        ))}
      </ul>
      <h4>Next steps</h4>
      <ul>
        {report.next_steps.map((t) => (
          <li key={t}>{t}</li>
        ))}
      </ul>
    </div>
  )
}

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
        <div className="table-scroll">
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
        </div>
      )}
    </div>
  )
}

export function ExtractedSection({ docs, claimId }: { docs: Doc[]; claimId: string }) {
  if (docs.length === 0) return null
  return (
    <div className="stack" aria-label="Extracted data">
      <h3>Extracted data</h3>
      {docs.map((d) => (
        <div className="card" key={d.id}>
          <p>
            <span className="mono">{d.file_metadata.original_name || d.file}</span> <span className="pill pill-idle">{d.document_type}</span>
          </p>
          {/\.(jpe?g|png)$/.test(d.file) && (
            <img
              src={`/claims/${claimId}/documents/${d.id}/file`}
              alt={d.file_metadata.original_name || d.file}
              style={{ maxHeight: '96px', maxWidth: '100%' }}
            />
          )}
          {d.fields.length === 0 ? (
            <p className="muted">No field read.</p>
          ) : (
            <div className="table-scroll">
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
                      <td className="muted">{f.source.snippet ? `page ${f.source.page} · “${f.source.snippet}”` : `page ${f.source.page} · box ${f.source.box?.join(', ') ?? ''}`}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      ))}
    </div>
  )
}
