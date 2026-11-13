import { useEffect, useRef, useState } from 'react'
import type { DragEvent } from 'react'
import { json, send } from './api'

type Message = { role: 'customer' | 'agent'; text: string; time: string }

type Sample = { folder: string; file: string; document_type: string | null }
type Hard = Sample & { variant: string }
type Case = {
  folder: string
  title: string
  stories: { text: string }[]
  documents: (Sample & { hard: Hard[] })[]
}
const VARIANT: Record<string, string> = { scan: 'scan', photo: 'phone photo' }

const uploadError = (e: Error) =>
  e.message === '415' ? 'Only PDF, PNG or JPEG files.' : e.message === '413' ? 'The file is larger than 10 MB.' : 'The upload failed.'

// the agent is typing: used in the chat, and while the first message of a new claim is made
export function Typing() {
  return (
    <p className="bubble bubble-agent typing">
      <span className="sr-only">The agent is typing</span>
      <span className="typing-dots" aria-hidden="true">
        <i />
        <i />
        <i />
      </span>
      <span className="typing-still" aria-hidden="true">
        …
      </span>
    </p>
  )
}

const BEFORE_DOCUMENTS = ['ask_story', 'ask_date', 'ask_amount']
const DONE_HELP = 'Press Done when you have uploaded your documents.'

export default function Chat({ claimId, policy }: { claimId: string; policy: string }) {
  const [messages, setMessages] = useState<Message[]>([])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [over, setOver] = useState(false)
  const [cases, setCases] = useState<Case[]>([])
  const [open, setOpen] = useState(false)
  const [sent, setSent] = useState<string[]>([]) // sample documents uploaded in this claim
  const [step, setStep] = useState('') // the chat step of the customer view; Done needs it
  const list = useRef<HTMLDivElement>(null)
  const field = useRef<HTMLInputElement>(null)

  useEffect(() => {
    send<Message[]>(`/claims/${claimId}/messages`)
      .then(setMessages)
      .catch(() => setError('The server did not answer.'))
  }, [claimId])
  useEffect(() => {
    send<{ step?: string }>(`/claims/${claimId}/customer`)
      .then((v) => setStep(v.step ?? ''))
      .catch(() => undefined)
  }, [claimId])
  useEffect(() => {
    send<Case[]>(`/demo/scenarios?policy=${encodeURIComponent(policy)}`)
      .then(setCases)
      .catch(() => setCases([]))
  }, [policy])
  useEffect(() => {
    if (list.current) list.current.scrollTop = list.current.scrollHeight
  }, [messages, busy])
  useEffect(() => {
    if (!busy) field.current?.focus() // a disabled input loses the focus
  }, [busy])

  // one request at a time; the server answer is the whole conversation, reloaded after it
  const run = async (request: Promise<unknown>, shown: string, failed: (e: Error) => string) => {
    setBusy(true)
    setError('')
    setMessages((m) => [...m, { role: 'customer', text: shown, time: new Date().toISOString() }])
    let ok = true
    try {
      await request
    } catch (e) {
      ok = false
      setError(failed(e as Error))
    }
    try {
      setMessages(await send<Message[]>(`/claims/${claimId}/messages`))
      setStep((await send<{ step?: string }>(`/claims/${claimId}/customer`)).step ?? '')
    } catch {
      setError('The server did not answer.')
    }
    setBusy(false)
    return ok
  }

  const say = (t: string) =>
    void run(send(`/claims/${claimId}/messages`, json('POST', { text: t })), t, () => 'The message was not sent.')

  const submit = () => {
    const t = text.trim()
    if (!t || busy) return
    setText('')
    say(t)
  }

  const upload = (file: File | undefined) => {
    if (!file || busy) return Promise.resolve(false)
    const body = new FormData()
    body.append('file', file)
    return run(send(`/claims/${claimId}/documents`, { method: 'POST', body }), `Uploaded ${file.name}`, uploadError)
  }

  // the sample goes through the normal upload endpoint, like a file from the device
  const pickSample = async (f: Sample) => {
    try {
      const blob = await fetch(`/demo/kit/${f.folder}/${f.file}`).then((r) => (r.ok ? r.blob() : Promise.reject(new Error(String(r.status)))))
      if (await upload(new File([blob], f.file, { type: blob.type }))) setSent((s) => [...s, `${f.folder}/${f.file}`])
    } catch {
      setError('The sample document did not load.')
    }
  }

  const drop = (e: DragEvent) => {
    e.preventDefault()
    setOver(false)
    upload(e.dataTransfer.files[0])
  }

  const putStory = (t: string) => {
    setText(t)
    field.current?.focus()
  }
  const locked = busy || step === 'closed'
  const sample = (f: Sample, className?: string, label?: string) => (
    <button key={`${f.folder}/${f.file}`} type="button" className={className} disabled={locked} onClick={() => void pickSample(f)}>
      {sent.includes(`${f.folder}/${f.file}`) && <span aria-label="uploaded">✓ </span>}
      {label ?? f.file}
      {!className && <span className="pill pill-idle">{f.document_type ?? 'document'}</span>}
    </button>
  )

  const before = BEFORE_DOCUMENTS.includes(step) // Done does not work yet: say when it will

  return (
    <div
      className={`chat${over ? ' chat-over' : ''}`}
      onDragOver={(e) => {
        e.preventDefault()
        setOver(true)
      }}
      onDragLeave={() => setOver(false)}
      onDrop={drop}
    >
      {error && <p className="pill pill-bad">{error}</p>}
      <div className="chat-list" ref={list} role="log" aria-label="Conversation">
        {messages.map((m, i) => (
          <p key={i} className={`bubble bubble-${m.role}`}>
            {m.text}
          </p>
        ))}
        {busy && <Typing />}
      </div>
      <form
        className="chat-form"
        onSubmit={(e) => {
          e.preventDefault()
          submit()
        }}
      >
        <input ref={field} aria-label="Message" placeholder="Write a message" value={text} disabled={busy} onChange={(e) => setText(e.target.value)} />
        <button type="submit" className="btn-primary" disabled={busy}>
          Send
        </button>
        <label className="chat-upload" aria-disabled={busy}>
          Upload
          <input
            type="file"
            hidden
            disabled={busy}
            aria-label="Upload a document"
            accept="application/pdf,image/png,image/jpeg"
            onChange={(e) => {
              upload(e.target.files?.[0])
              e.target.value = ''
            }}
          />
        </label>
        <button
          type="button"
          disabled={busy || step !== 'ask_documents'}
          title={before ? DONE_HELP : undefined}
          aria-describedby={before ? 'done-help' : undefined}
          onClick={() => say('done')}
        >
          Done
        </button>
        {before && (
          <p id="done-help" className="muted chat-help">
            {DONE_HELP}
          </p>
        )}
      </form>
      {cases.length > 0 && (
        <div className="cases">
          <button type="button" aria-expanded={open} onClick={() => setOpen(!open)}>
            Sample cases ({cases.length})
          </button>
          {open && (
            <div className="cases-list" role="group" aria-label="Sample cases">
              {cases.map((c) => (
                <div key={c.folder} className="card case">
                  <h3>{c.title}</h3>
                  {c.stories.map((st) => (
                    <blockquote key={st.text}>
                      {st.text}
                      <button type="button" onClick={() => putStory(st.text)}>
                        Use this text
                      </button>
                    </blockquote>
                  ))}
                  {c.documents.map((d) => (
                    <div key={d.file} className="case-doc">
                      {sample({ ...d, folder: c.folder })}
                      {d.hard.map((h) => sample(h, 'case-hard', VARIANT[h.variant] ?? h.variant))}
                    </div>
                  ))}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
      <p className="muted">Drop a PDF, PNG or JPEG here, or press Upload.</p>
    </div>
  )
}
