import { useEffect, useState } from 'react'
import { json, send } from './api'
import Chat from './Chat'

type Customer = { id: string; name: string }
type Policy = { number: string; line_of_business: string }
type Claim = { id: string; type: string | null; policy: { number: string } | null; customer: Customer }

export default function CustomerPanel({ claimId, onClaim }: { claimId: string | null; onClaim: (id: string) => void }) {
  const [customers, setCustomers] = useState<Customer[]>([])
  const [policies, setPolicies] = useState<Policy[]>([])
  const [filter, setFilter] = useState('')
  const [claim, setClaim] = useState<Claim | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    send<Customer[]>('/customers')
      .then(setCustomers)
      .catch(() => setError('The server did not answer.'))
  }, [])
  useEffect(() => {
    if (!claimId) return
    const fail = () => setError('The server did not answer.')
    send<Claim>(`/claims/${claimId}`)
      .then((c) => {
        setClaim(c)
        // the policy list goes through the gateway: it is the first event of the claim
        if (!c.policy) send<Policy[]>(`/claims/${claimId}/policies`).then(setPolicies).catch(fail)
      })
      .catch(fail)
  }, [claimId])

  // the claim is created when the customer picks an account, before any policy
  const pickCustomer = (c: Customer) =>
    send<Claim>('/claims', json('POST', { customer_id: c.id }))
      .then((created) => onClaim(created.id))
      .catch(() => setError('The claim was not created.'))

  const pickPolicy = (p: Policy) =>
    send<Claim>(`/claims/${claimId}/policy`, json('PUT', { policy_number: p.number }))
      .then((c) => {
        setClaim(c)
        setFilter('')
      })
      .catch(() => setError('The policy was not set.'))

  const q = filter.trim().toLowerCase()
  const shown = policies.filter((p) => `${p.number} ${p.line_of_business}`.toLowerCase().includes(q))

  return (
    <section className="card panel panel-customer" aria-label="Customer">
      <p className="notice">Demo. Do not upload a real document.</p>
      <h2>Customer</h2>
      {error && <p className="pill pill-bad">{error}</p>}
      {!claimId ? (
        <div className="stack">
          <h3>1. Pick an account</h3>
          {customers.map((c) => (
            <button key={c.id} type="button" onClick={() => void pickCustomer(c)}>
              {c.name}
            </button>
          ))}
        </div>
      ) : claim?.policy ? (
        <>
          <p className="mono">{`Claim ${claim.id} · ${claim.policy.number} · ${claim.type}`}</p>
          <Chat claimId={claim.id} />
        </>
      ) : claim ? (
        <div className="stack">
          <h3>2. Pick a policy of {claim.customer.name}</h3>
          <input aria-label="Filter policies" placeholder="Number or line of business" value={filter} onChange={(e) => setFilter(e.target.value)} />
          {shown.map((p) => (
            <button key={p.number} type="button" onClick={() => void pickPolicy(p)}>
              <span className="mono">{p.number}</span> · {p.line_of_business}
            </button>
          ))}
        </div>
      ) : null}
    </section>
  )
}
