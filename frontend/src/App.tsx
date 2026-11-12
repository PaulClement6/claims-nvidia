import { useState } from 'react'
import { send } from './api'
import CustomerPanel from './CustomerPanel'
import Health from './Health'
import PipelinePanel from './PipelinePanel'

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
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">Claims</div>
        <div className="sidebar-foot">
          <Health />
          <button type="button" onClick={toggleTheme}>
            {theme === 'dark' ? 'Light theme' : 'Dark theme'}
          </button>
          {resetError && <p className="pill pill-bad">{resetError}</p>}
          <button type="button" className="btn-danger" onClick={reset}>
            Reset demo
          </button>
        </div>
      </aside>
      <main className="content panels">
        <CustomerPanel key={`c${round}`} claimId={claimId} onClaim={setClaimId} />
        <PipelinePanel key={`p${round}`} claimId={claimId} />
      </main>
    </div>
  )
}
