import { useEffect, useState } from 'react'
import { json, send } from './api'
import Chat, { Typing } from './Chat'

type Customer = { id: string; name: string }
type Policy = { number: string; line_of_business: string }
// the customer endpoint: claim id, policy, status and the result text, nothing else
type View = { claim_id: string; policy_number: string | null; status: string; message: string | null }

export default function CustomerPanel({ claimId, onClaim }: { claimId: string | null; onClaim: (id: string | null) => void }) {
  const [customers, setCustomers] = useState<Customer[]>([])
  const [policies, setPolicies] = useState<Policy[]>([])
  const [filter, setFilter] = useState('')
  const [claim, setClaim] = useState<View | null>(null)
  const [name, setName] = useState('')
  const [error, setError] = useState('')
  const [starting, setStarting] = useState(false) // the policy is being set: the agent writes its first message

  useEffect(() => {
    send<Customer[]>('/customers')
      .then(setCustomers)
      .catch(() => setError('The server did not answer.'))
  }, [])
  useEffect(() => {
    if (!claimId) return
    const fail = () => setError('The server did not answer.')
    send<View>(`/claims/${claimId}/customer`)
      .then((c) => {
        setClaim(c)
        // the policy list goes through the gateway: it is the first event of the claim
        if (!c.policy_number) send<Policy[]>(`/claims/${claimId}/policies`).then(setPolicies).catch(fail)
      })
      .catch(fail)
  }, [claimId])

  // the claim is created when the customer picks an account, before any policy
  const pickCustomer = (c: Customer) =>
    send<View>('/claims', json('POST', { customer_id: c.id }))
      .then((created) => {
        setName(c.name)
        onClaim(created.claim_id)
      })
      .catch(() => setError('The claim was not created.'))

  const pickPolicy = (p: Policy) => {
    setStarting(true) // nim and sandbox mode: the first message takes 5 to 10 seconds
    return send<View>(`/claims/${claimId}/policy`, json('PUT', { policy_number: p.number }))
      .then((c) => {
        setClaim(c)
        setFilter('')
      })
      .catch(() => setError('The policy was not set.'))
      .finally(() => setStarting(false))
  }

  const newClaim = () => {
    setClaim(null)
    setPolicies([])
    onClaim(null) // no data is deleted: the old claim stays in the store
  }

  const q = filter.trim().toLowerCase()
  const shown = policies.filter((p) => `${p.number} ${p.line_of_business}`.toLowerCase().includes(q))

  return (
    <section className="card panel panel-customer" aria-label="Customer">
      <p className="notice">Demo. Do not upload a real document.</p>
      <h2>Customer</h2>
      {claimId && (
        <button type="button" onClick={newClaim}>
          New claim
        </button>
      )}
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
      ) : claim?.policy_number ? (
        <>
          <p className="mono">{`Claim ${claim.claim_id} · ${claim.policy_number}`}</p>
          <Chat claimId={claim.claim_id} policy={claim.policy_number} />
        </>
      ) : claim && starting ? (
        <div className="chat-list" role="log" aria-label="Conversation">
          <Typing />
        </div>
      ) : claim ? (
        <div className="stack">
          <h3>{name ? `2. Pick a policy of ${name}` : '2. Pick a policy'}</h3>
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
