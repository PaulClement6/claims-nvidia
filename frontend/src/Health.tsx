import { useEffect, useState } from 'react'

type Status = 'checking' | 'ok' | 'down'
type Info = { status: Status; mode: string | null; model: string | null }

const MODE_TONE: Record<string, string> = { local: 'idle', nim: 'warn', sandbox: 'ok' }

async function checkHealth(): Promise<Info> {
  try {
    const res = await fetch('/health')
    if (!res.ok) return { status: 'down', mode: null, model: null }
    const body = (await res.json().catch(() => ({}))) as { mode?: unknown; agent_model?: unknown }
    return {
      status: 'ok',
      mode: typeof body.mode === 'string' ? body.mode : null,
      model: typeof body.agent_model === 'string' ? body.agent_model : null,
    }
  } catch {
    return { status: 'down', mode: null, model: null }
  }
}

// the backend pill, the mode pill under it, and the agent model when there is one
export default function Health() {
  const [info, setInfo] = useState<Info>({ status: 'checking', mode: null, model: null })

  useEffect(() => {
    const update = () => void checkHealth().then(setInfo)
    update()
    // ponytail: polling so "down" shows without reload; replace when a live channel exists
    const id = setInterval(update, 5000)
    return () => clearInterval(id)
  }, [])

  const { status, mode, model } = info
  const tone = status === 'ok' ? 'ok' : status === 'down' ? 'bad' : 'idle'
  return (
    <>
      <span className={`pill pill-${tone}`}>Backend: {status}</span>
      {mode && <span className={`pill pill-${MODE_TONE[mode] ?? 'idle'}`}>Mode: {mode}</span>}
      {model && <span className="mono muted model-name">{model}</span>}
    </>
  )
}
