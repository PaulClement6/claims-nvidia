import { useState } from 'react'
import type { ReactNode } from 'react'
import { send } from './api'
import Demo from './Demo'
import { HealthRows, useHealth } from './Health'
import { Icon, type IconName } from './Icon'

// What every page can read: the claim of the demo, the reset round and the backend mode.
export type Shared = { claimId: string | null; onClaim: (id: string | null) => void; round: number; mode: string | null }

// The nav is built from this list: to add a page, add one line. No router: `useState` holds the current page.
type Page = { id: string; label: string; icon: IconName; render: (shared: Shared) => ReactNode }
const PAGES: Page[] = [{ id: 'demo', label: 'Claim demo', icon: 'activity', render: (s) => <Demo {...s} /> }]

function useTheme() {
  const [theme, setTheme] = useState(document.documentElement.dataset.theme === 'light' ? 'light' : 'dark')
  const toggle = () => {
    const next = theme === 'dark' ? 'light' : 'dark'
    document.documentElement.dataset.theme = next
    try {
      localStorage.setItem('theme', next)
    } catch {
      // ponytail: private mode or blocked storage; the theme still applies for this visit
    }
    setTheme(next)
  }
  return [theme, toggle] as const
}

export default function App() {
  const [theme, toggleTheme] = useTheme()
  const health = useHealth()
  const [page, setPage] = useState(PAGES[0].id)
  const [claimId, setClaimId] = useState<string | null>(null)
  const [round, setRound] = useState(0) // a reset remounts both panels
  const [resetError, setResetError] = useState('')
  const reset = () => {
    if (!confirm('Delete all claims, messages and uploaded files?')) return
    send('/demo/reset', { method: 'POST' })
      .then(() => {
        setResetError('')
        setClaimId(null)
        setRound((r) => r + 1)
      })
      .catch(() => setResetError('The reset failed.'))
  }
  const shared: Shared = { claimId, onClaim: setClaimId, round, mode: health.mode }
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <svg width="26" height="26" viewBox="0 0 30 30" aria-hidden="true">
            <path d="M15 2 27 9v12l-12 7-12-7V9z" fill="none" stroke="var(--text)" strokeWidth="2" />
            <path d="M3 9l12 7 12-7M15 16v12" fill="none" stroke="var(--accent)" strokeWidth="2" />
          </svg>
          Claims
        </div>
        <nav className="nav" aria-label="Main">
          <div role="group" aria-labelledby="nav-demo" className="nav-group">
            <span id="nav-demo" className="eyebrow">
              Demo
            </span>
            {PAGES.map((p) => (
              <button key={p.id} type="button" aria-current={p.id === page ? 'page' : undefined} onClick={() => setPage(p.id)}>
                <Icon name={p.icon} />
                <span>{p.label}</span>
              </button>
            ))}
          </div>
        </nav>
        <div className="sidebar-foot">
          <div className="health-card" aria-label="System">
            <span className="eyebrow">System</span>
            <HealthRows info={health} />
          </div>
          <div className="sidebar-actions">
            <button type="button" onClick={toggleTheme}>
              {theme === 'dark' ? 'Light theme' : 'Dark theme'}
            </button>
            <button type="button" className="btn-danger" onClick={reset}>
              Reset demo
            </button>
          </div>
          {resetError && <p className="pill pill-bad">{resetError}</p>}
        </div>
      </aside>
      <main className="content">
        {/* kept mounted: a page keeps its state (and its event stream) while another one is open */}
        {PAGES.map((p) => (
          <div key={p.id} className="page" hidden={p.id !== page}>
            {p.render(shared)}
          </div>
        ))}
      </main>
    </div>
  )
}
