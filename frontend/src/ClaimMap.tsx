// The Map page (issue #61): what the system is, and where the intern builds.
// Wide (the map is 1100 px or more): zones side by side, with arrows measured from the real boxes.
// Narrow: zones stacked, the order replaces the arrows. The detail panel always says what comes in and goes out.
import { useLayoutEffect, useRef, useState } from 'react'
import { Icon } from './Icon'
import './ClaimMap.css'
import { ARROWS, BOXES, EVENTS, ISSUES_URL, LEGEND, PRINCIPLES, TASKS, ZONES, type Arrow, type MapBox, type Status } from './mapModel'

const WIDE = 1100
const STATUS_TAG: Record<Status, string | null> = { built: null, yours: 'YOURS', planned: 'LATER' }
const STATUS_NAME: Record<Status, string> = { built: 'Built', yours: 'Yours', planned: 'Later' }
const titleOf = (id: string) => BOXES.find((b) => b.id === id)?.title ?? id

// ---- arrows ----------------------------------------------------------------------------------------------

interface Rect {
  l: number
  t: number
  r: number
  b: number
}
interface Shape {
  key: string
  d: string
  head: string
  tone: 'flow' | 'planned' | 'events'
  label?: { x: number; y: number; lines: string[] }
}

const rectOf = (el: Element, root: DOMRect): Rect => {
  const r = el.getBoundingClientRect()
  return { l: r.left - root.left, t: r.top - root.top, r: r.right - root.left, b: r.bottom - root.top }
}
const head = (x: number, y: number, dir: 'down' | 'up' | 'right' | 'left') => {
  const p = {
    down: [x, y, x - 4.5, y - 7, x + 4.5, y - 7],
    up: [x, y, x - 4.5, y + 7, x + 4.5, y + 7],
    right: [x, y, x - 7, y - 4.5, x - 7, y + 4.5],
    left: [x, y, x + 7, y - 4.5, x + 7, y + 4.5],
  }[dir]
  return `${p[0]},${p[1]} ${p[2]},${p[3]} ${p[4]},${p[5]}`
}
// A long label takes two lines, so that it fits in a gutter.
const wrap = (s: string) => {
  const i = s.lastIndexOf(' ')
  return s.length > 10 && i > 0 ? [s.slice(0, i), s.slice(i + 1)] : [s]
}

function shapeOf(a: Arrow, rects: Map<string, Rect>): Shape | null {
  const A = rects.get(a.from)
  const B = rects.get(a.to)
  if (!A || !B) return null
  const key = `${a.from}-${a.to}`
  const tone = a.dashed ? 'planned' : 'flow'
  const label = (x: number, y: number) => (a.label ? { x, y, lines: wrap(a.label) } : undefined)
  if (a.dir === 'down') {
    const x = A.l + (a.at ?? 0.5) * (A.r - A.l)
    return { key, tone, d: `M${x} ${A.b}V${B.t - 5}`, head: head(x, B.t, 'down'), label: label(x, (A.b + B.t) / 2 - 6) }
  }
  const right = a.dir === 'right'
  const sx = right ? A.r : A.l
  const ex = right ? B.l : B.r
  const lo = Math.max(A.t, B.t) + 14
  const hi = Math.min(A.b, B.b) - 14
  const dx = right ? -5 : 5
  if (lo <= hi) {
    // The boxes face each other: one straight line.
    const y = Math.min(Math.max((A.t + A.b) / 2, lo), hi)
    return { key, tone, d: `M${sx} ${y}H${ex + dx}`, head: head(ex, y, a.dir), label: label((sx + ex) / 2, y) }
  }
  const sy = (A.t + A.b) / 2
  const ey = (B.t + B.b) / 2
  const mx = (sx + ex) / 2
  return { key, tone, d: `M${sx} ${sy}H${mx}V${ey}H${ex + dx}`, head: head(ex, ey, a.dir), label: label(mx, (sy + ey) / 2) }
}

// Every zone sends events to the pipeline panel: a dashed bus under the map.
function eventShapes(zones: Map<string, Rect>, boxes: Map<string, Rect>, busY: number): Shape[] {
  const target = zones.get('observe')
  const z2 = zones.get('agent')
  if (!target || !z2 || !boxes.get(EVENTS.to)) return []
  const mid = (r: Rect) => (r.l + r.r) / 2
  const stubs = ['intake', 'model', 'tools'].flatMap((id) => {
    const z = zones.get(id)
    return z ? [`M${mid(z)} ${z.b}V${busY}`] : []
  })
  // The sandbox zone has another zone under it: its line leaves the left edge and goes down the gutter.
  const lane = z2.l - 16
  stubs.push(`M${z2.l} ${z2.b - 22}H${lane}V${busY}`)
  const x0 = Math.min(lane, mid(zones.get('intake') ?? z2))
  const x1 = mid(target)
  const bus = `M${x0} ${busY}H${x1}V${target.b + 5}`
  const lx = ((zones.get('tools')?.r ?? x1) + x1) / 2
  return [{ key: 'events', tone: 'events', d: [...stubs, bus].join(''), head: head(x1, target.b, 'up'), label: { x: lx, y: busY, lines: [EVENTS.label] } }]
}

// Measures the zones and the boxes in the map, and draws the arrows on top. Nothing when the map is stacked.
function useArrows(map: React.RefObject<HTMLDivElement | null>) {
  const [state, setState] = useState<{ w: number; h: number; shapes: Shape[] } | null>(null)
  useLayoutEffect(() => {
    const el = map.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const measure = () => {
      if (el.clientWidth < WIDE) return setState(null)
      const root = el.getBoundingClientRect()
      const all = (sel: string, attr: string) =>
        new Map([...el.querySelectorAll(sel)].map((n) => [n.getAttribute(attr) ?? '', rectOf(n, root)]))
      const boxes = all('[data-box]', 'data-box')
      const zones = all('[data-zone]', 'data-zone')
      const shapes = [
        ...ARROWS.map((a) => shapeOf(a, boxes)),
        ...eventShapes(zones, boxes, el.clientHeight - 20),
      ].filter((s): s is Shape => s !== null)
      setState({ w: el.clientWidth, h: el.clientHeight, shapes })
    }
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    measure()
    return () => ro.disconnect()
  }, [map])
  return state
}

function Arrows({ state }: { state: NonNullable<ReturnType<typeof useArrows>> }) {
  return (
    <svg className="cm-arrows" aria-hidden="true" focusable="false" width={state.w} height={state.h} viewBox={`0 0 ${state.w} ${state.h}`}>
      {state.shapes.map((s) => (
        <g key={s.key} data-tone={s.tone}>
          <path d={s.d} />
          <polygon points={s.head} />
          {s.label && (
            <g transform={`translate(${s.label.x} ${s.label.y})`}>
              <rect x={-32} y={-8 * s.label.lines.length - 1} width={64} height={16 * s.label.lines.length + 2} rx={4} />
              {s.label.lines.map((t, i) => (
                <text key={t} y={(i - (s.label!.lines.length - 1) / 2) * 13 + 4} textAnchor="middle">
                  {t}
                </text>
              ))}
            </g>
          )}
        </g>
      ))}
    </svg>
  )
}

// ---- boxes -----------------------------------------------------------------------------------------------

function Tags({ box }: { box: MapBox }) {
  const tag = STATUS_TAG[box.status]
  return (
    <>
      {tag && <span className="cm-tag">{tag}</span>}
      {box.status === 'yours' && box.tag && <span className="cm-tag cm-tag-new">{box.tag}</span>}
    </>
  )
}

function Box({ box, selected, dim, onSelect }: { box: MapBox; selected: boolean; dim: boolean; onSelect: () => void }) {
  const big = box.more !== undefined
  return (
    <button
      type="button"
      className="cm-box"
      data-box={box.id}
      data-status={box.status}
      data-big={big}
      data-dim={dim}
      aria-pressed={selected}
      onClick={onSelect}
    >
      {!big && (
        <span className="cm-tile">
          <Icon name={box.icon} size={16} />
        </span>
      )}
      <span className="cm-text">
        {big && (
          <span className="cm-tile cm-tile-big">
            <Icon name={box.icon} size={18} />
          </span>
        )}
        <span className="cm-title">
          {box.title}
          <Tags box={box} />
        </span>
        {box.sub && <span className="cm-sub">{box.sub}</span>}
        <span className="cm-line">{box.line}</span>
        {box.more?.map((l) => (
          <span key={l} className="cm-line cm-more">
            {l}
          </span>
        ))}
      </span>
    </button>
  )
}

// ---- detail ----------------------------------------------------------------------------------------------

function Detail({ box }: { box: MapBox }) {
  const gets = ARROWS.filter((a) => a.to === box.id)
  const sends = ARROWS.filter((a) => a.from === box.id)
  return (
    <section className="cm-detail" aria-label={`Detail of ${box.title}`} data-status={box.status}>
      <header>
        <h2>{box.title}</h2>
        <span className="cm-pill" data-status={box.status}>
          {STATUS_NAME[box.status]}
        </span>
      </header>
      <p>{box.detail}</p>
      <dl>
        {box.files.length > 0 && (
          <>
            <dt>{box.filesLabel ?? 'Files'}</dt>
            <dd>
              <ul className="cm-files">
                {box.files.map((f) => (
                  <li key={f}>
                    <code>{f}</code>
                  </li>
                ))}
              </ul>
            </dd>
          </>
        )}
        {box.issues.length > 0 && (
          <>
            <dt>{box.issues.length > 1 ? 'Issues' : 'Issue'}</dt>
            <dd>
              <ul className="cm-issues">
                {box.issues.map((i) => (
                  <li key={i.n}>
                    <a href={`${ISSUES_URL}/${i.n}`} target="_blank" rel="noopener noreferrer">
                      #{i.n}
                      {i.label ? ` ${i.label}` : ''}
                    </a>
                  </li>
                ))}
              </ul>
            </dd>
          </>
        )}
        {box.check && (
          <>
            <dt>Check</dt>
            <dd>
              {box.check.text && <span>{box.check.text} </span>}
              {box.check.cmd && <code>{box.check.cmd}</code>}
            </dd>
          </>
        )}
        {(gets.length > 0 || sends.length > 0) && (
          <>
            <dt>Flow</dt>
            <dd>
              {gets.map((a) => (
                <span key={a.from} className="cm-flow">
                  Gets from {titleOf(a.from)}
                  {a.label ? ` (${a.label})` : ''}
                </span>
              ))}
              {sends.map((a) => (
                <span key={a.to} className="cm-flow">
                  Sends to {titleOf(a.to)}
                  {a.label ? ` (${a.label})` : ''}
                </span>
              ))}
            </dd>
          </>
        )}
      </dl>
    </section>
  )
}

// ---- page ------------------------------------------------------------------------------------------------

export default function ClaimMap() {
  const [selected, setSelected] = useState('reading')
  const [onlyMine, setOnlyMine] = useState(false)
  const mapRef = useRef<HTMLDivElement>(null)
  const detailRef = useRef<HTMLDivElement>(null)
  const arrows = useArrows(mapRef)
  const current = BOXES.find((b) => b.id === selected) ?? BOXES[0]

  const select = (id: string) => {
    setSelected(id)
    // Stacked map: the detail is far below the box. jsdom has no scrollIntoView.
    if (!arrows) detailRef.current?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' })
  }

  return (
    <div className="cm">
      <header className="cm-head">
        <p className="cm-eyebrow">Claims · How it works</p>
        <h1>
          How a claim is <em>processed</em>
        </h1>
        <p className="cm-lede">
          A customer talks to an agent in a sandbox. Tools read the documents, code checks and routes the claim, and every step is an event.
        </p>
      </header>

      <div className="cm-banner" role="note">
        <span className="cm-tag">YOURS</span>
        <span>The lime boxes are yours. Open a box to see the files and the issue.</span>
        <label className="cm-filter">
          <input type="checkbox" checked={onlyMine} onChange={(e) => setOnlyMine(e.target.checked)} />
          Show only my boxes
        </label>
      </div>

      <div className="cm-mapwrap">
        <div className="cm-map" ref={mapRef}>
          {ZONES.map((z) => (
            <section key={z.id} className="cm-zone" data-zone={z.id} aria-labelledby={`cm-z-${z.id}`}>
              <h2 id={`cm-z-${z.id}`} className="cm-zone-h">
                {z.num} · {z.name}
              </h2>
              <ul className="cm-boxes">
                {BOXES.filter((b) => b.zone === z.id).map((b) => (
                  <li key={b.id}>
                    <Box
                      box={b}
                      selected={b.id === selected}
                      dim={onlyMine && b.status !== 'yours'}
                      onSelect={() => select(b.id)}
                    />
                  </li>
                ))}
              </ul>
            </section>
          ))}
          {arrows && <Arrows state={arrows} />}
        </div>
      </div>

      <ul className="cm-legend" aria-label="Legend">
        {LEGEND.map((l) => (
          <li key={l.status} data-kind={l.status}>
            {l.text}
          </li>
        ))}
      </ul>

      <div className="cm-lower">
        <div ref={detailRef}>
          <Detail box={current} />
        </div>
        <section className="cm-tasks" aria-labelledby="cm-tasks-h">
          <h2 id="cm-tasks-h">Your tasks</h2>
          <ol aria-label="Your tasks">
            {TASKS.map((t) => (
              <li key={t.n}>
                <a href={`${ISSUES_URL}/${t.n}`} target="_blank" rel="noopener noreferrer">
                  #{t.n}
                </a>
                <span>
                  <strong>{t.title}</strong>
                  <span className="cm-result">Result: {t.result}</span>
                </span>
              </li>
            ))}
          </ol>
        </section>
      </div>

      <ul className="cm-chips" aria-label="Principles">
        {PRINCIPLES.map((p) => (
          <li key={p}>{p}</li>
        ))}
      </ul>
    </div>
  )
}
