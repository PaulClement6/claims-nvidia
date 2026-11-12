import { useEffect, useRef, useState } from 'react'
import type { DragEvent } from 'react'
import { json, send } from './api'

type Message = { role: 'customer' | 'agent'; text: string; time: string }

type KitFile = { folder: string; file: string; document_type: string | null; size: number }

const uploadError = (e: Error) =>
  e.message === '415' ? 'Only PDF, PNG or JPEG files.' : e.message === '413' ? 'The file is larger than 10 MB.' : 'The upload failed.'

export default function Chat({ claimId }: { claimId: string }) {
  const [messages, setMessages] = useState<Message[]>([])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [over, setOver] = useState(false)
  const [kit, setKit] = useState<KitFile[] | null>(null) // null: list closed
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
    if (list.current) list.current.scrollTop = list.current.scrollHeight
  }, [messages])
  useEffect(() => {
    if (!busy) field.current?.focus() // a disabled input loses the focus
  }, [busy])

  // one request at a time; the server answer is the whole conversation, reloaded after it
  const run = async (request: Promise<unknown>, shown: string, failed: (e: Error) => string) => {
    setBusy(true)
    setError('')
    setMessages((m) => [...m, { role: 'customer', text: shown, time: new Date().toISOString() }])
    try {
      await request
    } catch (e) {
      setError(failed(e as Error))
    }
    try {
      setMessages(await send<Message[]>(`/claims/${claimId}/messages`))
      setStep((await send<{ step?: string }>(`/claims/${claimId}/customer`)).step ?? '')
    } catch {
      setError('The server did not answer.')
    }
    setBusy(false)
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
    if (!file || busy) return
    const body = new FormData()
    body.append('file', file)
    void run(send(`/claims/${claimId}/documents`, { method: 'POST', body }), `Uploaded ${file.name}`, uploadError)
  }

  const openKit = () =>
    kit
      ? setKit(null)
      : void send<KitFile[]>('/demo/kit')
          .then(setKit)
          .catch(() => setError('The sample documents did not load.'))

  // the sample goes through the normal upload endpoint, like a file from the device
  const pickSample = async (f: KitFile) => {
    setKit(null)
    try {
      const blob = await fetch(`/demo/kit/${f.folder}/${f.file}`).then((r) => (r.ok ? r.blob() : Promise.reject(new Error(String(r.status)))))
      upload(new File([blob], f.file, { type: blob.type }))
    } catch {
      setError('The sample document did not load.')
    }
  }

  const drop = (e: DragEvent) => {
    e.preventDefault()
    setOver(false)
    upload(e.dataTransfer.files[0])
  }

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
      </div>
      <form
        className="chat-form"
        onSubmit={(e) => {
          e.preventDefault()
          submit()
        }}
      >
        <input ref={field} aria-label="Message" placeholder="Write a message" value={text} disabled={busy} onChange={(e) => setText(e.target.value)} />
        <button type="submit" disabled={busy}>
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
        <button type="button" disabled={busy} aria-expanded={kit !== null} onClick={openKit}>
          Use a sample document
        </button>
        <button type="button" disabled={busy || step !== 'ask_documents'} onClick={() => say('done')}>
          Done
        </button>
      </form>
      {kit && (
        <div className="stack" role="group" aria-label="Sample documents">
          {[...new Set(kit.map((f) => f.folder))].map((folder) => (
            <div key={folder}>
              <h3>{folder}</h3>
              {kit
                .filter((f) => f.folder === folder)
                .map((f) => (
                  <button key={f.file} type="button" disabled={busy} onClick={() => void pickSample(f)}>
                    {f.file} · {f.document_type ?? 'document'} · {Math.max(1, Math.round(f.size / 1024))} KB
                  </button>
                ))}
            </div>
          ))}
        </div>
      )}
      <p className="muted">Drop a PDF, PNG or JPEG here, or press Upload.</p>
    </div>
  )
}
