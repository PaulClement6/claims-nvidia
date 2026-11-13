// The data of the Map page (ClaimMap.tsx, issue #61). Plain constants: the page and the tests read them.
// A path in `files` is relative to the repository root. The test checks that it exists.
import type { IconName } from './Icon'

export const ISSUES_URL = 'https://github.com/PaulClement6/claims-nvidia/issues'

export type Status = 'built' | 'yours' | 'planned'
export type ZoneId = 'intake' | 'agent' | 'tools' | 'model' | 'observe'

export interface Zone {
  id: ZoneId
  num: string
  name: string
}

export interface Issue {
  n: number
  label?: string
}

export interface MapBox {
  id: string
  zone: ZoneId
  title: string
  line: string
  status: Status
  icon: IconName
  detail: string
  files: string[]
  filesLabel?: string
  issues: Issue[]
  check?: { text?: string; cmd?: string }
  // The big box of the confined agent.
  sub?: string
  more?: string[]
  // A second tag next to YOURS.
  tag?: string
}

export interface Arrow {
  from: string
  to: string
  dir: 'down' | 'right' | 'left'
  label?: string
  dashed?: boolean
  // Where a vertical arrow stands on the box width (0 to 1). Default 0.5.
  at?: number
}

export const ZONES: Zone[] = [
  { id: 'intake', num: '01', name: 'Customer & intake' },
  { id: 'agent', num: '02', name: 'Confined agent' },
  { id: 'tools', num: '03', name: 'Tools & workflows' },
  { id: 'model', num: '04', name: 'Model & rails' },
  { id: 'observe', num: '05', name: 'Observe' },
]

export const BOXES: MapBox[] = [
  {
    id: 'customer',
    zone: 'intake',
    title: 'Customer',
    line: 'Opens the customer panel, tells the story, uploads documents.',
    status: 'built',
    icon: 'user',
    detail: 'The left panel of the demo. It never shows a score or a flag.',
    files: ['frontend/src/CustomerPanel.tsx', 'frontend/src/Chat.tsx'],
    issues: [],
    check: { cmd: 'cd frontend && npm test' },
  },
  {
    id: 'backend',
    zone: 'intake',
    title: 'Backend',
    line: 'FastAPI: claims, uploads, the event stream.',
    status: 'built',
    icon: 'box',
    detail: 'One claim = one row, one lock, one stream of events.',
    files: ['backend/app/main.py', 'backend/app/store/', 'backend/app/stream.py'],
    issues: [],
    check: { cmd: 'cd backend && uv run pytest' },
  },
  {
    id: 'policies',
    zone: 'intake',
    title: 'Policies',
    line: 'Mock customers and policies.',
    status: 'built',
    icon: 'layers',
    detail: 'Three mock customers. Mock data only.',
    files: ['data/mock/', 'docs/MOCK-DATA.md'],
    issues: [],
  },
  {
    id: 'sandbox',
    zone: 'agent',
    title: 'Claim sandbox',
    sub: 'LangGraph agent · one per claim',
    line: 'One OpenShell sandbox per claim.',
    more: [
      'Nodes: intake → documents → checks → route → answer.',
      'It can reach the model and its own tools. Nothing else.',
      'The model key stays outside.',
    ],
    status: 'built',
    icon: 'lock',
    detail: 'The agent reasons, it does not decide: the score and the route are code.',
    files: ['backend/app/agent/graph.py', 'backend/app/agent/sandbox.py', 'infra/sandbox/policy.yaml'],
    issues: [],
    check: { text: 'Press the button "Run confinement test" in the pipeline panel (5 denials of 5).' },
  },
  {
    id: 'gateway',
    zone: 'tools',
    title: 'Tool gateway',
    line: 'The only door of the agent. One event per call.',
    status: 'built',
    icon: 'link',
    detail: 'Served to the sandbox as an MCP server, one path per claim.',
    files: ['backend/app/tools/gateway.py', 'backend/app/tools/mcp_server.py', 'backend/tests/test_tool_events.py'],
    issues: [],
    check: { cmd: 'cd backend && uv run pytest tests/test_tool_events.py' },
  },
  {
    id: 'reading',
    zone: 'tools',
    title: 'Document reading',
    line: 'classify_document, extract_document. Today: a fixture reader. You build the real one.',
    status: 'yours',
    icon: 'activity',
    detail:
      'The Reader contract: classify(document, data) and extract(document, data, workflow). Every field carries its document and page. Today FixtureReader returns expected.json. You build OmniReader (Nemotron 3 Nano Omni).',
    files: ['backend/app/docintel/reader.py', 'backend/app/docintel/fixture.py', 'backend/tests/reader_contract.py'],
    issues: [
      { n: 17, label: 'schemas' },
      { n: 18, label: 'Omni reader' },
      { n: 20, label: 'tuning' },
      { n: 21, label: 'classification' },
      { n: 22, label: 'hard cases' },
    ],
    // The contract test has no fixed name yet: README.md, section "The reader", says how to write it.
    check: {
      text: 'Call check_reader_contract(OmniReader()) in a test (see README.md), then run it with CLAIMS_READER=omni:',
      cmd: 'cd backend && uv run pytest -k reader',
    },
  },
  {
    id: 'workflows',
    zone: 'tools',
    title: 'Workflows',
    line: 'Travel, property, health: required documents, fields, rules.',
    status: 'built',
    icon: 'list',
    detail: 'A workflow decides which documents and fields a claim needs.',
    files: ['backend/app/workflows/', 'docs/WORKFLOWS.md'],
    issues: [],
  },
  {
    id: 'checks',
    zone: 'tools',
    title: 'Checks',
    line: 'Rules R, tampering T, consistency C. Each one gives a signal.',
    status: 'built',
    icon: 'shield',
    detail: '12 signals in one table.',
    files: ['backend/app/checks/', 'docs/FRAUD.md'],
    issues: [],
  },
  {
    id: 'route',
    zone: 'tools',
    title: 'Score & route',
    line: 'Code decides: straight through, handler review or investigation. Never a rejection.',
    status: 'built',
    icon: 'alert',
    detail: '60 points or more: investigation, with a fraud report. 20 or more: handler review.',
    files: ['backend/app/fraud/', 'backend/tests/test_no_auto_rejection.py', 'backend/tests/test_fraud_report.py'],
    issues: [],
    check: { cmd: 'cd backend && uv run pytest tests/test_no_auto_rejection.py tests/test_fraud_report.py' },
  },
  {
    id: 'rails',
    zone: 'model',
    title: 'Rails',
    line: 'NeMo Guardrails on what the agent reads: the customer message and the document text.',
    status: 'yours',
    tag: 'NEW',
    icon: 'shield',
    detail:
      'A document can hold a sentence written for the model. Today only code protects us (the agent has no tool that decides) plus an output filter. You add an input rail, a rail on the document text and an output rail. A block never rejects a claim: it goes to a person.',
    filesLabel: 'Files to read',
    files: ['../nemoforge-plateforme/infra/guardrails', 'docs/DECISIONS.md'],
    issues: [{ n: 62 }],
    check: { text: 'In docs/DECISIONS.md, read decision D28 first.' },
  },
  {
    id: 'model',
    zone: 'model',
    title: 'Agent model',
    line: 'Nemotron 3.5 Lightning on the NVIDIA endpoint. OpenShell adds the key outside the sandbox.',
    status: 'built',
    icon: 'chip',
    detail: 'Median turn 5 to 7 s, about 40 requests per minute with the free key.',
    files: ['infra/sandbox/profile-nim.yaml', 'docs/EVALUATION.md'],
    issues: [],
  },
  {
    id: 'vision',
    zone: 'model',
    title: 'Vision model',
    line: 'Nemotron 3 Nano Omni reads the pages. You call it from the Omni reader.',
    status: 'yours',
    icon: 'chip',
    detail: 'Page image in, JSON that follows your schema out. Variables VLM_BASE_URL and VLM_MODEL in .env.',
    files: [],
    issues: [{ n: 18 }],
  },
  {
    id: 'pipeline',
    zone: 'observe',
    title: 'Pipeline panel',
    line: 'Every node, tool call and denial, live.',
    status: 'built',
    icon: 'grid',
    detail: 'The right panel of the demo.',
    files: ['frontend/src/PipelinePanel.tsx', 'frontend/src/SandboxSection.tsx'],
    issues: [],
  },
  {
    id: 'evaluation',
    zone: 'observe',
    title: 'Evaluation',
    line: 'Accuracy per field, pass rate, time per claim, against expected.json.',
    status: 'yours',
    icon: 'trend-up',
    detail:
      'Your numbers decide the prompt, the resolution and the model, and they support the request for GPU credits. scripts/measure_agent.py is the model for a measure script.',
    files: ['data/mock/index.json', 'docs/EVALUATION.md', 'scripts/measure_agent.py'],
    issues: [{ n: 19 }],
  },
  {
    id: 'chain',
    zone: 'observe',
    title: 'Specialised chain',
    line: 'OCR, page elements, tables, Parse: where Omni fails.',
    status: 'planned',
    icon: 'layers',
    detail: 'After the hard cases (#22).',
    files: [],
    issues: [{ n: 24 }],
  },
]

export const ARROWS: Arrow[] = [
  { from: 'customer', to: 'backend', dir: 'down' },
  { from: 'backend', to: 'policies', dir: 'down' },
  { from: 'backend', to: 'sandbox', dir: 'right', label: 'one turn' },
  { from: 'sandbox', to: 'gateway', dir: 'right', label: 'tool call (MCP)' },
  { from: 'gateway', to: 'reading', dir: 'down' },
  { from: 'reading', to: 'workflows', dir: 'down' },
  { from: 'workflows', to: 'checks', dir: 'down' },
  { from: 'checks', to: 'route', dir: 'down' },
  // Dashed while the Rails box is not built.
  { from: 'sandbox', to: 'rails', dir: 'down', label: 'model call', dashed: true, at: 0.75 },
  { from: 'rails', to: 'model', dir: 'down', dashed: true },
  { from: 'reading', to: 'vision', dir: 'left', label: 'page image' },
]

// Every zone sends its events to the pipeline panel (dashed).
export const EVENTS = { label: 'events', to: 'pipeline' }

export interface Task {
  n: number
  title: string
  result: string
}

export const TASKS: Task[] = [
  { n: 17, title: 'Pydantic schema per document type', result: 'every expected.json validates' },
  { n: 18, title: 'Omni reader behind the Reader contract', result: 'the contract test passes' },
  { n: 19, title: 'Evaluation harness', result: 'accuracy per field and time per claim' },
  { n: 20, title: 'Tuning', result: 'a table per variant' },
  { n: 21, title: 'Classification with Omni', result: 'accuracy on the kit' },
  { n: 22, title: 'Hard cases: scans and phone photos', result: 'the gain per document kind' },
  { n: 62, title: 'Rails on what the agent reads', result: 'an injected document is blocked, with the count of false blocks' },
]

export const LEGEND: { status: Status | 'events'; text: string }[] = [
  { status: 'built', text: 'Built' },
  { status: 'yours', text: 'Yours' },
  { status: 'planned', text: 'Later' },
  { status: 'events', text: 'Dashed arrow: events for the pipeline panel' },
]

export const PRINCIPLES = [
  'A model finds facts, code decides',
  'Never a rejection',
  'Every field has its source page',
  'The key never enters the sandbox',
]
