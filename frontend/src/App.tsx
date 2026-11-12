import { useState } from 'react'
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
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">Claims</div>
        <div className="sidebar-foot">
          <Health />
          <button type="button" onClick={toggleTheme}>
            {theme === 'dark' ? 'Light theme' : 'Dark theme'}
          </button>
        </div>
      </aside>
      <main className="content panels">
        <CustomerPanel />
        <PipelinePanel />
      </main>
    </div>
  )
}
