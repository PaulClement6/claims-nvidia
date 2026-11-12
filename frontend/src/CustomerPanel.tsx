import { useEffect, useState } from 'react'

type Customer = { id: string; name: string }
type Policy = { number: string; line_of_business: string }
type Claim = { id: string; type: string; policy: { number: string } }

export default function CustomerPanel({ claimId, onClaim }: { claimId: string | null; onClaim: (id: string) => void }) {
  const [customers, setCustomers] = useState<Customer[]>([])
  const [customer, setCustomer] = useState<Customer | null>(null)
  const [policies, setPolicies] = useState<Policy[]>([])
  const [filter, setFilter] = useState('')
  const [claim, setClaim] = useState<Claim | null>(null)
  const [error, setError] = useState('')

  const load = <T,>(url: string, set: (v: T) => void) =>
    fetch(url)
      .then((r) => (r.ok ? (r.json() as Promise<T>) : Promise.reject(new Error(String(r.status)))))
      .then(set)
      .catch(() => setError('The server did not answer.'))

  useEffect(() => void load('/customers', setCustomers), [])
  useEffect(() => {
    if (claimId) void load(`/claims/${claimId}`, setClaim)
  }, [claimId])

  const pickCustomer = (c: Customer) => {
    setCustomer(c)
    setFilter('')
    void load(`/customers/${c.id}/policies`, setPolicies)
  }

  const pickPolicy = (p: Policy) =>
    fetch('/claims', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ customer_id: customer?.id, policy_number: p.number }),
    })
      .then((r) => (r.ok ? (r.json() as Promise<Claim>) : Promise.reject(new Error(String(r.status)))))
      .then((c) => onClaim(c.id))
      .catch(() => setError('The claim was not created.'))

  const q = filter.trim().toLowerCase()
  const shown = policies.filter((p) => `${p.number} ${p.line_of_business}`.toLowerCase().includes(q))

  return (
    <section className="card panel panel-customer" aria-label="Customer">
      <p className="notice">Demo. Do not upload a real document.</p>
      <h2>Customer</h2>
      {error && <p className="pill pill-bad">{error}</p>}
      {claimId && claim ? (
        <>
          <p className="mono">{`Claim ${claim.id} · ${claim.policy.number} · ${claim.type}`}</p>
          <p className="muted">The chat comes next.</p>
        </>
      ) : !customer ? (
        <div className="stack">
          <h3>1. Pick an account</h3>
          {customers.map((c) => (
            <button key={c.id} type="button" onClick={() => pickCustomer(c)}>
              {c.name}
            </button>
          ))}
        </div>
      ) : (
        <div className="stack">
          <h3>2. Pick a policy of {customer.name}</h3>
          <input aria-label="Filter policies" placeholder="Number or line of business" value={filter} onChange={(e) => setFilter(e.target.value)} />
          {shown.map((p) => (
            <button key={p.number} type="button" onClick={() => void pickPolicy(p)}>
              <span className="mono">{p.number}</span> · {p.line_of_business}
            </button>
          ))}
        </div>
      )}
    </section>
  )
}
