export default function PipelinePanel({ claimId }: { claimId: string | null }) {
  return (
    <section className="card panel panel-pipeline" aria-label="Pipeline">
      <h2>Pipeline</h2>
      {claimId && <p className="mono">Claim {claimId}</p>}
    </section>
  )
}
