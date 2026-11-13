import CustomerPanel from './CustomerPanel'
import PipelinePanel from './PipelinePanel'
import type { Shared } from './App'

// The demo page: a page header, then the two panels side by side.
export default function Demo({ claimId, onClaim, round, mode }: Shared) {
  return (
    <>
      <header className="page-head">
        <div className="title">
          <span className="eyebrow">{mode ? `Demo · mode ${mode}` : 'Demo'}</span>
          <h1>Claim demo</h1>
          <p>Left: what the customer sees. Right: what the pipeline does, live.</p>
        </div>
      </header>
      <div className="panels">
        <CustomerPanel key={`c${round}`} claimId={claimId} onClaim={onClaim} />
        <PipelinePanel key={`p${round}`} claimId={claimId} />
      </div>
    </>
  )
}
