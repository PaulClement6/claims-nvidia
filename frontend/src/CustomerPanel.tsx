import { useEffect, useState } from 'react'

type Customer = { id: string; name: string }
type Policy = { number: string; line_of_business: string }
type Claim = { id: string; type: string | null; policy: { number: string } | null; customer: Customer }

export default function CustomerPanel({ claimId, onClaim }: { claimId: string | null; onClaim: (id: string) => void }) {
  const [customers, setCustomers] = useState<Customer[]>([])
  const [policies, setPolicies] = useState<Policy[]>([])
  const [filter, setFilter] = useState('')
  const [claim, setClaim] = useState<Claim | null>(null)
  const [error, setError] = useState('')

  const send = <T,>(url: string, init?: RequestInit) =>
    fetch(url, init).then((r) => (r.ok ? (r.json() as Promise<T>) : Promise.reject(new Error(String(r.status)))))
  const load = <T,>(url: string, set: (v: T) => void) =>
    send<T>(url)
      .then(set)
      .catch(() => setError('The server did not answer.'))
  const json = (method: string, body: object): RequestInit => ({
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })

  useEffect(() => void load('/customers', setCustomers), [])
  useEffect(() => {
    if (!claimId) return
    void load<Claim>(`/claims/${claimId}`, (c) => {
      setClaim(c)
      // the policy list goes through the gateway: it is the first event of the claim
      if (!c.policy) void load<Policy[]>(`/claims/${claimId}/policies`, setPolicies)
    })
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
          <p className="muted">The chat comes next.</p>
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
