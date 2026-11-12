import { useEffect, useState } from 'react'

type Status = 'checking' | 'ok' | 'down'

async function checkHealth(): Promise<Status> {
  try {
    const res = await fetch('/health')
    return res.ok ? 'ok' : 'down'
  } catch {
    return 'down'
  }
}

export default function Health() {
  const [status, setStatus] = useState<Status>('checking')

  useEffect(() => {
    const update = () => void checkHealth().then(setStatus)
    update()
    // ponytail: polling so "down" shows without reload; replace when a live channel exists
    const id = setInterval(update, 5000)
    return () => clearInterval(id)
  }, [])

  const tone = status === 'ok' ? 'ok' : status === 'down' ? 'bad' : 'idle'
  return <span className={`pill pill-${tone}`}>Backend: {status}</span>
}
