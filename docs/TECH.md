# How it works and why

Written on 2026-11-13 for issue #69. The reader is Nikola, the intern, and the coding agent that he gives this repository to.

After you read this file, you can explain the whole system. You can say what each part does, how the parts talk, which NVIDIA technology is under each part, why we chose it, what is weak, and what you may change.

## How to read this file

- Sections 1 and 2 describe the system. Section 3 describes the NVIDIA technology. Section 4 gives the design rules.
- Section 5 lists what you may challenge. Section 6 lists what you may redo. Section 7 is a glossary.
- Section 8 lists every fact that I could not check.
- Names of components are the names of the Map page of the app (`frontend/src/mapModel.ts`): 5 zones and 15 boxes.
- `D` and a number is a decision in `docs/DECISIONS.md`. `O` and a number is an open question in the same file.
- A fact marked "measured" comes from `docs/EVALUATION.md`, `docs/LESSONS.md` or `docs/DEPLOY.md`.
- A fact marked "NVIDIA page" comes from a public NVIDIA page that I fetched on 2026-11-13. Section 8 lists the pages.
- The NVIDIA stack changes every month. Check a page again before you write code against it.

---

## 1. The system in one page

This is a demo claims platform for insurance. A customer talks to a claim agent and uploads documents. The agent reads the documents, checks the claim, looks for fraud and routes the claim. A second panel shows the pipeline live.

The claim flow, with the names of the Map:

1. The **Customer** opens the customer panel and picks a mock account.
2. The **Backend** lists the policies of the account (**Policies**). The customer picks one. The line of business sets the workflow: travel, property or health.
3. The **Backend** starts the agent. The agent runs in a **Claim sandbox**: one OpenShell sandbox per claim.
4. The agent asks for the story, the date and the amount (node `intake`).
5. The agent asks for the documents the **Workflows** require. For each upload, it calls `classify_document` and `extract_document` (node `documents`). These calls go through the **Tool gateway** to **Document reading**. Today a fixture reader answers. Later the Omni reader answers, with the **Vision model**.
6. When the customer is done, code (not the model) runs the three **Checks** (node `checks`): rules, tampering, consistency.
7. Code adds the points and picks the route (node `route`, box **Score & route**). For `investigation`, code also writes a fraud report.
8. Code sends the customer message word for word and closes the claim (node `answer`).
9. During all this, every tool call and every node writes an event. The **Pipeline panel** shows the events live.

```
 CUSTOMER PANEL (chat, upload)            PIPELINE PANEL (events, fields, fraud report)
        |  HTTP                                    ^  SSE (server-sent events)
        v                                          |
 Backend (FastAPI, SQLite, one lock per claim) ----+
        |
        |  openshell sandbox exec (JSON on stdin)
        v
 Claim sandbox (OpenShell, deny by default) -- LangGraph graph: intake, documents, checks, route, answer
        |  model call                      |  tool call (MCP over HTTP, /mcp/<token>)
        v                                  v
 provider claims-nim ----> NVIDIA      Tool gateway (in the backend, trusted code)
 (key added outside)       endpoint      |- get_claim_summary, update_claim, required_documents
                                         |- classify_document, extract_document -> Reader (fixture / Omni)
                                         |- run_checks, check_tampering, check_consistency
                                         '- assess_and_route -> write_fraud_report
```

There are three outcomes and no more: `straight_through`, `handler_review`, `investigation`. The system never rejects a claim. A person decides every refusal.

The one idea behind the whole design: **a model finds facts, code decides.** The model talks to the customer and reads documents. Code gives the points, picks the route and writes the customer message. This makes the result repeatable and testable.

The demo that defines "done" has three claim files (`docs/MOCK-DATA.md`):

| File | Story | Outcome |
|---|---|---|
| 1, travel | Anna cancels a trip because she is ill. Clean documents. | `straight_through` |
| 2, property | Marco has water damage. The amount is CHF 3,200, above the limit of CHF 2,000. | `handler_review` |
| 3, health | Lukas claims an invoice with a changed total, an edited PDF and a wrong patient name. | `investigation` |

The customer sees the same message for files 2 and 3. A different message would tell a fraudster that the system flagged the claim.

---

## 2. The components

### 2.1 The three modes

One setting picks the mode: `CLAIMS_MODE`. The function `get_agent` in `backend/app/agent/__init__.py` is the only place that reads it.

| Mode | The agent | Reading | Sandbox | Use |
|---|---|---|---|---|
| `local` (default) | A scripted state machine (`backend/app/agent/scripted.py`) | Fixture reader | None | Development, tests, CI. No GPU, no key, no network. |
| `nim` | The LangGraph graph in the backend process (`graph_agent.py`) | Fixture or Omni | None | Work on the agent and on extraction quality. Needs `NVIDIA_KEY`. |
| `sandbox` | The same graph, in an OpenShell sandbox (`sandbox.py`, `sandbox_turn.py`) | Fixture or Omni | Enforced | The demo, on the Brev VM. |

The three modes share the tool gateway, the schemas, the rules and the UI. Only the agent and the model client change.

Why `local` must always work:

- CI runs in `local` mode. It needs no key and no GPU, so every pull request can be tested.
- A person with no key (a new reader of the repository, a fork) can still run the full claim flow.
- `local` mode imports no LangChain and no LangGraph. The packages load only in `nim` and `sandbox` mode. A test checks it (`test_local_mode_does_not_import_langchain_or_langgraph`, in `backend/tests/test_graph_agent.py`).
- It gives a reference: the scripted agent must give the right outcome for the three claim files. If a model mode gives another outcome, the bug is not in the checks.

### 2.2 Customer (zone 01)

- What: the left panel. A chat and an upload button. It shows no score, no flag, no model name.
- Files: `frontend/src/CustomerPanel.tsx`, `frontend/src/Chat.tsx`.
- Talks to: the backend over HTTP (`POST /claims`, `PUT /claims/{id}/policy`, `POST /claims/{id}/messages`, `POST /claims/{id}/documents`, `GET /claims/{id}/customer`).
- Events: none. The panel only reads.
- Guard: non-negotiable 2 (section 4). The backend sends the panel a `CustomerView` with five fields: claim id, policy number, status, message, step.

### 2.3 Backend (zone 01)

- What: FastAPI. It holds the claims, the uploads, the SQLite store, the event stream and the mode switch.
- Files: `backend/app/main.py`, `backend/app/store/__init__.py`, `backend/app/stream.py`, `backend/app/views.py`, `backend/app/demo.py`.
- Store: SQLite, standard library, no ORM. Tables: `customers`, `policies`, `claims`, `events`, `messages`, `sandboxes`. A claim is one JSON row. One connection and one re-entrant lock serve all threads.
- One lock per claim (`claim_lock` in `main.py`): one turn at a time for a claim. A message, an upload and a policy change all take it.
- Upload rules: at most 10 MB. The type comes from the first bytes (PDF, PNG or JPEG), never from the file name or the client content type.
- Two views. `GET /claims/{id}/customer` is for the customer panel. `GET /claims/{id}` and `/events` are for the pipeline panel. There is no access control between them (D33): both panels are on one screen.
- Event stream: `GET /claims/{id}/events` is server-sent events (SSE). The server reads the store every 0.3 s and sends new events. This is simple on purpose (comment `ponytail` in `stream.py`).
- Demo routes (`backend/app/demo.py`): `GET /demo/kit`, `GET /demo/scenarios`, `POST /demo/reset`. The reset deletes the claims, the uploads, the checkpoints and the sandboxes of this app.
- Talks to the agent: in `local` and `nim` mode, a Python call. In `sandbox` mode, `openshell sandbox exec`.
- Guard: `backend/tests/test_customer_view.py`, `test_upload_api.py`, `test_events_api.py`, `test_concurrency.py`.

### 2.4 Policies (zone 01)

- What: mock customers and mock insurance policies. Three customers, six policies, all invented.
- Files: `data/mock/policies.json`, `docs/MOCK-DATA.md`. The store loads them at the first start.
- Tool: `get_customer_policies` (`backend/app/tools/policies.py`). It has no description, so the model cannot see it. Only the backend API calls it, to list the policies of an account.
- Guard: `backend/tests/test_policies_api.py`. Rule 7 (mock data only) is a review rule.

### 2.5 Claim sandbox (zone 02)

This is the confined agent. It is the most complex part. Read `docs/ARCHITECTURE.md` section 7 as well.

**The graph** (`backend/app/agent/graph.py`) has five nodes. The order is code, not a prompt.

| Node | Who | What it does |
|---|---|---|
| `intake` | Model | Asks for the story, the date and the amount. Saves them with `update_claim`. |
| `documents` | Model | Asks for the required documents. Reads each upload with `classify_document` and `extract_document`. |
| `checks` | Code | Reads any document the model left unread. Runs the three checks. |
| `route` | Code | Calls `assess_and_route`. |
| `answer` | Code | Sends the customer message word for word. Closes the claim. |

- The model has 2 to 6 tools in a node (`NODE_TOOLS`). It never gets `run_checks`, `check_tampering`, `check_consistency`, `assess_and_route` or `write_fraud_report`.
- `customer_is_done` is a tool of the model, but it is not a gateway tool. It sets a flag in the graph state. Code also decides "done" for the Done button and for the usual phrases (`is_done` in `phrases.py`).
- One customer message or one upload is one `invoke` on the thread of the claim. A turn ends after one message. The state is saved in a SQLite checkpoint after each node. The thread id is the claim id (D41, D42).
- If a code node fails, the step stays in the checkpoint. The next message resumes at that node. After two failures in a row in the same node, the claim goes to a person (`handover.py`, D56). It gets the status `in_review` and no outcome.
- `build_graph(model, call_tool, checkpointer, specs, emit)` imports nothing from the store, the gateway or FastAPI (D38). A test checks it. This is why the same file runs in the backend and in the sandbox.

**One sandbox per claim** (`backend/app/agent/sandbox.py`):

1. The backend creates the sandbox on the first turn. It takes about 0.5 s (measured).
2. The backend renders a policy file for this sandbox from `infra/sandbox/policy.yaml`. It fills in the port, a secret path and the tool rules.
3. Each turn is one `openshell sandbox exec`. The customer text goes on stdin as JSON, never as an argument.
4. The process (`python -m app.agent.sandbox_turn`) runs the graph and prints one JSON line: the reply and the node and model events.
5. The backend deletes the sandbox when the claim closes and on the reset.

**How the sandbox talks:**

| To | How | Allowed by |
|---|---|---|
| The backend (tools) | MCP over HTTP to `host.openshell.internal:<port>/mcp/<token>` | The network policy of the sandbox |
| The model | `POST /v1/chat/completions` to `integrate.api.nvidia.com` through the provider `claims-nim` | The provider profile `infra/sandbox/profile-nim.yaml` |
| Anything else | Nothing | Denied by default |

**The backend does not trust the sandbox** (D48):

- The JSON line is data. Node and model events from the sandbox are stored as display-only events, with `"source": "sandbox"`. The backend drops any sandbox event of actor `tool` or `sandbox`.
- The text of the agent passes the output filter. When the claim has an outcome, the backend drops that text. It sends its own `customer_message`.
- A turn that times out (default 150 s) deletes the sandbox, because a process may still run in it (D47).

**Events written:** actor `workflow` (one per node, stage = node name), actor `agent` (one per model call, stage `llm`; also `refused`, `fallback`), actor `sandbox` (`created`, `turn`, `deleted`, `failed`, `denied`, `probe`).

**Pipeline panel features:** the policy of the sandbox as data, the denied requests from the sandbox log, and a confinement test. The test is a fixed script that tries 5 forbidden things. The backend judges the answers, not the sandbox (D50 to D52).

**Guard:** `backend/tests/test_graph_agent.py`, `test_sandbox_agent.py`, `test_sandbox_panel.py`, `test_sandbox_image.py`, `test_no_secrets_in_sandbox.py`. With a real sandbox: `cd backend && uv run pytest -m sandbox`.

**Numbers (measured, `docs/EVALUATION.md`):**

| What | Value |
|---|---|
| Right outcomes, graph, 9 claim runs | 9 of 9 |
| Model calls per claim | 16.3 |
| Tool calls the model asked for | 11.0 |
| Tokens per claim | 24,069 |
| Median turn, `nim` / `sandbox` (6 claims each) | 6.6 s / 7.4 s |
| Fixed cost per turn in a sandbox | about 1 s (Python start and imports 0.9 s) |

### 2.6 Tool gateway (zone 03)

- What: the only door of the agent. Every tool call goes through it. Every call writes exactly one event, also when the tool fails.
- Files: `backend/app/tools/gateway.py` (the registry and the event), `backend/app/tools/mcp_server.py` (MCP server for the sandbox), `backend/app/tools/guard.py` (rule 1 of the routing), one module per tool group in `backend/app/tools/`.
- The event holds: sequence, time, actor `tool`, stage (the tool name), title, inputs, outputs, model, duration.
- `visible`: a tool can return fewer keys to the agent than it writes in the event. Example: `extract_document` returns only `document_type` to the agent. The event keeps all fields. `run_checks` and `assess_and_route` return nothing but the customer message (D31).
- A tool with no description is not listed for the model and not callable through MCP.
- **MCP server:** Streamable HTTP, stateless, JSON answers, official `mcp` SDK 1.30.0 (D44). It is mounted at `/mcp/<token>`.
  - Each sandbox has its own token: 32 random bytes. The backend stores only the SHA-256.
  - The token maps to a claim id. The claim id is in no tool schema, so the agent cannot name another claim.
  - The comparison runs in constant time against every open sandbox. An unknown or closed token gets 404.
  - The token never goes to a log: the uvicorn access log is filtered (`RedactToken`). Events show `/mcp/ab12…`.
- `guarded_call` (`guard.py`) is the second line of defence (D40). It refuses `assess_and_route` until the story is saved, every document is read and the three checks ran after the last change. The backend graph agent and the MCP server both call it.
- Guard: `backend/tests/test_tool_events.py` (rule 5), `test_mcp_server.py`, `test_tool_specs.py`.

### 2.7 Document reading (zone 03, yours)

- What: `classify_document` and `extract_document`. A document in, a type and fields with sources out.
- Files: `backend/app/docintel/reader.py` (the contract), `backend/app/docintel/fixture.py` (the base reader), `backend/tests/reader_contract.py`.
- **The Reader contract** (D24) is the boundary between the base and your work. A reader is one class with `name`, `classify(document, data)` and `extract(document, data, workflow)`.
  - `classify` returns a document type or `"unknown"`. It never raises on unknown bytes.
  - `extract` returns an `Extraction`: the type, a list of `ExtractedField` and the line items.
  - Every field carries a source: document id, page (1 or more), and a snippet or a box.
  - `CLAIMS_READER` picks the reader. `get_reader()` in `reader.py` is where you add yours.
- **The fixture reader** finds the `expected.json` of an uploaded file by the SHA-256 of its bytes (D35). It calls no model. An unknown file gives the type `unknown` and no field.
- Checks, score, routing and the UI read only the `Extraction`. They do not change when the reader changes.
- The reader runs in the backend, not in the sandbox. The agent never sees the fields.
- Guard: `test_fixture_reader.py` and `check_reader_contract()` in `backend/tests/reader_contract.py`. Call it on your reader.
- Your tasks: #17 schemas, #18 Omni reader, #20 tuning, #21 classification, #22 hard cases.

### 2.8 Workflows (zone 03)

- What: a workflow is data. It lists the required documents, the causes, the straight-through limit and its own rules. One engine runs the three.
- Files: `backend/app/workflows/engine.py`, `travel.py`, `property.py`, `health.py`. `docs/WORKFLOWS.md` is the table.
- A new claim type is one new file. `backend/app/workflows/__init__.py` finds it.
- Limits: travel CHF 2,500, property CHF 2,000, health CHF 1,000.
- Plain Python and SQLite. No Temporal (D20).
- Guard: `test_travel_api.py`, `test_property_api.py`, `test_health_api.py`.

### 2.9 Checks (zone 03)

- What: 12 signals in one table (`SIGNALS` in `backend/app/checks/rules.py`). Each signal has a code, a category, a severity, points, a title and an "expected" text.
- Three tools run them: `run_checks` (rules R001 to R005 and the rules of the workflow), `check_tampering` (T001 to T003), `check_consistency` (C001 to C003).
- Tampering reads the real bytes of the stored PDF with pypdf: the producer, the creator, the creation and modification dates. T003 compares the line items with the total.
- Every signal carries evidence: the document and page it comes from.
- All checks are deterministic code. **C004 (photo against the declared damage, by a VLM) is in the table but no code gives it yet.** The backlog has it (item 33).
- A scan has no PDF metadata. T001 and T002 cannot fire on it. This is a case for the reader, not for the checks.
- Guard: `test_checks.py`, `test_tampering.py`, `test_consistency.py`, `test_document_kit.py`.

### 2.10 Score & route (zone 03)

- What: the score is the sum of the points. `investigation` at 60 or more. `handler_review` at 20 or more, or a required document is missing, or the amount is above the limit, or a field has confidence below 0.8. Otherwise `straight_through`.
- Files: `backend/app/fraud/routing.py`, `backend/app/fraud/report.py`, `backend/app/tools/routing.py`, `docs/FRAUD.md`.
- `assess_and_route` is the only place that decides the outcome. For `investigation` it calls `write_fraud_report`, which saves the outcome and the report in one write (D32).
- The fraud report is a template, built from the signals only. It adds no fact. It holds: the score, one sentence of why, each finding with its evidence, what was not checked, and next steps.
- `FRAUD.md` says an LLM writes the report in `nim` mode. The code does not do this yet: the event is titled "Write fraud report (template)". The backlog has it (item 34).
- The Outcome type has three values. There is no code path to a rejection.
- Guard: `backend/tests/test_no_auto_rejection.py`, `test_fraud_report.py`, `test_routing.py`, `test_three_claim_files.py`.

### 2.11 Rails (zone 04, yours)

- What: NeMo Guardrails on what the agent reads. Not built. This is your task #62 (D28, D60).
- Today only code protects the agent: it has no tool that decides, and every message passes a regex output filter (`FORBIDDEN` in `backend/app/agent/phrases.py`).
- Details in section 3.7.

### 2.12 Agent model (zone 04)

- What: Nemotron 3.5 Lightning on the hosted NVIDIA endpoint. It drives the `intake` and `documents` nodes.
- Files: `backend/app/agent/model.py` (`ChatOpenAI` settings), `infra/sandbox/profile-nim.yaml`, `.env.example`.
- Settings: `AGENT_BASE_URL`, `AGENT_MODEL`, `AGENT_THINKING`. Timeout 60 s, 2000 completion tokens, 2 retries (D37).
- In the sandbox, the model key is a placeholder. OpenShell swaps it on the way out (section 3.1).
- Why this model: D17 and section 3.3.

### 2.13 Vision model (zone 04, yours)

- What: Nemotron 3 Nano Omni reads the page image and fills the claim schema in JSON. You call it from the Omni reader (D15).
- Settings: `VLM_BASE_URL`, `VLM_MODEL` in `.env`. The endpoint is OpenAI-compatible chat completions.
- Details in section 3.5.

### 2.14 Pipeline panel (zone 05)

- What: the right panel. A graph strip (the current node), the tool calls, the extracted fields with sources, the signals, the fraud report, and the Sandbox section.
- Files: `frontend/src/PipelinePanel.tsx`, `frontend/src/PipelineSections.tsx`, `frontend/src/GraphStrip.tsx`, `frontend/src/SandboxSection.tsx`.
- It reads the event stream (SSE). The graph strip reads only the `workflow` events (D43).
- Order of events: the sandbox gives each event its own clock time, and the panel sorts by time (D53). The gateway events keep their own time and stay the trusted events.
- Read-only in version 1 (D29). Confirm and correct by the handler is backlog item 36.

### 2.15 Evaluation (zone 05, yours)

- What: accuracy per field, pass rate, time per claim, against `expected.json`.
- Files: `data/mock/index.json`, `docs/EVALUATION.md`, `scripts/measure_agent.py`.
- Today we measured the agent only: which model, which outcome, how many seconds and tokens. We measured no extraction accuracy, because the fixture reader is exact by design. Your task #19 builds this.
- `scripts/measure_agent.py` is the model for a measure script.

### 2.16 Specialised chain (zone 05, planned)

- What: OCR, page elements, table structure and Nemotron Parse, for the cases where Omni alone fails (D16).
- Not built. After your hard-cases task (#22). Details in section 3.6.

### 2.17 How the parts talk

| From | To | Channel | Carries |
|---|---|---|---|
| Browser | Backend | HTTP, JSON and multipart | Messages, uploads, views |
| Backend | Browser | SSE | Pipeline events |
| Backend | Sandbox | `openshell sandbox exec`, JSON on stdin and one JSON line on stdout | One customer turn |
| Sandbox | Backend | MCP over HTTP, one path per claim | Tool calls |
| Sandbox | Model | HTTPS through the provider | Chat completions |
| Backend | Model (reader) | HTTPS, the key in the backend `.env` | Page images (Omni reader, later) |
| Backend | OpenShell | The `openshell` command, with timeouts | Create, exec, delete, logs |

### 2.18 The event model

The pipeline panel is a view of events. This is the key idea of the observability.

- One table, `events`, with one row per event: sequence, time, actor (`agent`, `tool`, `workflow`, `sandbox`), stage, title, inputs, outputs, model, duration.
- Trusted events: actor `tool`. The gateway writes them. Rule 1 of the routing reads only those.
- Display-only events: actor `workflow` and `agent` from the sandbox. The sandbox could lie about them, so nothing acts on them.
- The `message` events (actor `agent`, stage `message`) hold what the customer read.
- Model calls write an `llm` event with the token usage, the finish reason and the tool calls. The text of a failed call is not kept, because it can echo the prompt.

---

## 3. The NVIDIA technology

For each item: what it is, how we use it, why, what we measured, and the alternatives.

### 3.1 OpenShell

**What it is.** An open-source runtime for autonomous AI agents (NVIDIA page). You declare in a YAML policy what an agent may reach. OpenShell enforces it. Four layers (NVIDIA page):

- Filesystem. Locked when the sandbox starts.
- Process. Locked when the sandbox is created. Non-root, no Linux capabilities, fixed seccomp filters.
- Network. Can change while the sandbox runs.
- Provider credentials. A placeholder resolves only at endpoints that the provider profile authorises.

Components (NVIDIA page):

- The **gateway** is the control plane. It manages the lifecycle of sandboxes, checks identity, delivers policy and attaches providers.
- The **supervisor** runs on the trusted side. It checks requests against the policy, resolves DNS, opens approved connections and supplies credentials.
- The **sandbox** holds the agent processes. It makes no policy decision.
- With the Docker driver, the supervisor runs in its own container and the workload container has networking turned off.

Warning: the word "gateway" has two meanings in this repo. The OpenShell gateway is the control plane. The tool gateway is our code in the backend. They are not the same.

**How we use it.** OpenShell 0.1.5, Docker driver, on Colima (Mac) or on the VM (Linux). We call the `openshell` command. We do not use the Python SDK.

| What | File | Setting |
|---|---|---|
| One-time setup | `infra/sandbox/setup.sh` | Builds the image `claims-agent:1`. Imports the profile. Creates the provider `claims-nim`. |
| Image | `infra/sandbox/Dockerfile` | `python:3.12-slim`, non-root user 1500, pinned packages, no `curl`, no shell tool. |
| Policy | `infra/sandbox/policy.yaml` | Filled in by `render_policy` for each sandbox. |
| Profile | `infra/sandbox/profile-nim.yaml` | One endpoint: `integrate.api.nvidia.com:443`, `POST /v1/chat/completions` only. |
| Runner | `backend/app/agent/sandbox.py` | `create`, `exec`, `delete`, `logs`, all with a timeout. |

The policy of one sandbox:

- Filesystem: `/usr`, `/lib`, `/etc`, `/app`, `/proc`, `/sys` read-only. `/sandbox` and `/tmp` writable. The rest is not reachable. Landlock `hard_requirement`: the sandbox does not start if Landlock cannot be enforced.
- Process: runs as uid 1500.
- Network: one policy, `claims_gateway`. Host `host.openshell.internal`, the backend port, the secret path of this claim, `protocol: mcp`, `enforcement: enforce`. Allowed: `initialize`, `notifications/initialized`, `tools/list`, and `tools/call` for each tool of `GRAPH_TOOLS`. Only the Python binaries may use it.
- The model goes through the provider profile, not through the policy file.

**How the key stays outside** (D49, rule 6):

1. `setup.sh` reads `NVIDIA_KEY` from `.env` and puts it in the environment of one command: `openshell provider create`. It is never an argument, never written, never printed.
2. The sandbox gets an environment variable `NVIDIA_API_KEY` that holds a placeholder (`openshell:resolve:env:...`).
3. When the agent calls `integrate.api.nvidia.com`, the supervisor puts the real key in the `Authorization` header. It does this for that host only.
4. The backend in `sandbox` mode does not need `NVIDIA_KEY` at all.
5. The confinement test checks it. It reports only a boolean ("does the key look like a real key") and the placeholder text, never a value.

**Why we chose it.** The brief asked for the agent in an OpenShell sandbox (D6, D25). It is also the pattern that NemoForge proved (OpenShell 0.1.5, provider profiles). The spike of issue #13 proved that a LangGraph agent, the model key swap and MCP tools run in it.

**What we measured.**

- The sandbox costs 0.5 s once per claim and about 1 s per turn.
- An idle sandbox uses 26 MiB.
- Five confinement tries give 5 denials of 5 on the VM.
- The policy denies another host, another path of the backend, the MCP path of another claim, a tool outside `GRAPH_TOOLS`, and `GET /v1/models`.
- Not measured: many sandboxes at the same time.

**Limits and traps** (spike and `docs/LESSONS.md`):

- OpenShell matches a binary by process ancestry. A program that can start a shell can then reach the allowed endpoints. The L7 rules (method and tool) are the guarantee, not the binary list. The image has no shell tool.
- A `protocol: mcp` endpoint governs the whole host and port, not only its path (lesson, issue #14).
- `openshell sandbox exec` does not pass the `ENV` of the image. We set variables with `--env` at create time.
- The sandbox log reaches the gateway about 0.3 s late.
- The NVIDIA best-practices page says the default enforcement mode is `audit`, which logs but does not block. Our policy sets `enforce` explicitly. Keep it.
- The NVIDIA pages describe the latest version. We run 0.1.5. I did not check each statement against 0.1.5.
- 0.1.6 to 0.1.8 change things that touch us (gateway authentication, credential placement, log format). The table is in `docs/DECISIONS.md`, spike section, question E. We have not tested 0.1.8.

**Alternatives.** Run the agent in the backend with no sandbox (that is `nim` mode). Use plain Docker with a network fence. Use another sandbox product. We do not know a measured comparison.

### 3.2 NemoClaw

**What it is** (NVIDIA page). An open-source reference stack for running always-on agents "more safely" inside OpenShell containers. It adds, on top of OpenShell:

- onboarding and lifecycle (setup, sandbox creation, status, logs, rebuilds);
- a versioned, digest-verified blueprint with a host CLI and an agent-specific layer;
- routed inference: hosted and local providers through OpenShell routes, credentials outside the sandbox;
- egress policy: YAML network policies and presets.

The page lists three supported agents: OpenClaw, Hermes and LangChain Deep Agents Code. It calls the stack an early preview for "a trusted operator on one host". Primary tested platforms are Linux and DGX Spark. The page lists limits on macOS and on headless Linux servers.

**What the repo says.**

- D6: the agent runs in an OpenShell sandbox "(NemoClaw / OpenShell)".
- `docs/BRIEF.md`: "sandboxed in OpenShell with NemoClaw".
- `docs/READING-LIST.md` lists the NemoClaw overview and the NemoClaw with Hermes page. The Hermes route was our candidate harness (D27).
- D36 replaced D27: the agent is our own LangGraph graph, not Hermes.
- No file of code, infra or tests names NemoClaw (searched on 2026-11-13).

**So:** we use OpenShell directly, through the `openshell` command. We wrote the lifecycle code ourselves (`sandbox.py`, `setup.sh`). No decision records why we skipped NemoClaw. My reading: NemoClaw targets named agents, and ours is a custom graph. Whether NemoClaw can run a custom LangGraph agent: **not checked**.

**What you may do.** Read the NemoClaw overview and compare. Its lifecycle, blueprint and egress presets may replace parts of `sandbox.py` and `setup.sh`. Measure before you change. The sandbox tests (`pytest -m sandbox`) must still pass.

### 3.3 The agent model: LangGraph, LangChain and Nemotron 3.5 Lightning

**What they are.**

- LangGraph runs a graph of nodes with a saved state. LangChain gives `ChatOpenAI`, the chat model client. We pin `langgraph` 1.2.21, `langchain` 1.5.0, `langchain-openai` 1.7.4, `langgraph-checkpoint-sqlite` 3.1.3 (D36).
- Nemotron 3.5 Lightning: 30B parameters in total, 3B active per token (a mixture of experts). Text in, text out. It supports tool calling and structured output. Context up to 1M tokens. License: OpenMDW 1.1 (NVIDIA page, which is the card of the NVFP4 variant).

**How we use them.**

- `build_graph` in `backend/app/agent/graph.py`. `make_model` in `backend/app/agent/model.py` builds `ChatOpenAI` on the OpenAI-compatible endpoint (D37). We chose `ChatOpenAI` over `ChatNVIDIA` because the spike proved it with the key swap.
- The model id is `nvidia/nemotron-3.5-lightning-30b-a3b` (`AGENT_MODEL`).
- Reasoning: `AGENT_THINKING=off` sends `chat_template_kwargs.enable_thinking: false`. Empty means the model default. The NVIDIA page that I fetched shows how to switch reasoning on but does not document switching it off. Our notes say off works (one call each, `reasoning_tokens` was 0). **Not checked on the page.** We did not measure the effect on quality or speed.

**Why this model** (D17). Measured on 2026-11-12 with the hand-written loop, 9 claim runs per model:

| | Lightning | Omni as agent |
|---|---|---|
| Right outcomes | 9 of 9 | 9 of 9 |
| Median turn | 4.9 s | 10.5 s |
| Tokens per claim | 33,109 | 55,949 |
| Fallback messages | 0 | 5 |

Omni often used its token budget for reasoning, so its messages were cut. It also named tools in messages, and the output filter refused them. Omni stays the document reader.

**Why a graph** (D36). Paul chose LangGraph and LangChain over Hermes on 2026-11-12. The graph moved the order of the steps from the prompt into code. Result (measured): the same 9 of 9 and 27% fewer tokens, because each node has a short prompt and a few tools.

**Limits (measured).**

- Model calls did not go down: 16.3 per claim. The model still needs one call per tool step.
- The worst turn is 54.6 s: one slow model call.
- The free developer key allows about 40 requests per minute. This is not documented by NVIDIA (O15). A claim makes 16 to 23 model calls, so two visitors at the same time can reach the limit.
- The model writes a non-breaking hyphen (U+2011) in identifiers in some runs. Never match identifiers in the answer text.
- A tool call whose arguments are not JSON arrives in `invalid_tool_calls`. The graph answers it with an error message like any bad call.

**Alternatives.** Another agent model on the same endpoint (change `AGENT_MODEL` and measure with `scripts/measure_agent.py`). A plain state machine with no LangGraph (the scripted agent is one). `ChatNVIDIA`. The intern may test another model. The measure script is the tool for this.

### 3.4 Hosted endpoints and the MCP tool gateway

**Hosted endpoint.** `https://integrate.api.nvidia.com/v1` is OpenAI-compatible chat completions with a bearer key (NVIDIA page). A free developer key comes from build.nvidia.com. The model pages say the hosted trial service is governed by the NVIDIA API Trial Terms of Service. This is why version 1 holds mock data only (D15, rule 7).

**MCP** (Model Context Protocol) is a standard way for an agent to list and call tools over HTTP. We use it as the wire between the sandbox and the tool gateway.

- Server: official `mcp` SDK, pinned to 1.30.0 (D44). Do not use 2.x: `FastMCP` is gone there, and `langchain-mcp-adapters` needs `mcp<2`.
- Stateless, JSON answers, one path per claim.
- The sandbox client opens one session per call (initialize, call, close). The cost is a few local requests (`backend/app/agent/sandbox_turn.py`).
- The policy can allow each tool by name, because OpenShell reads the MCP method and the tool name (`protocol: mcp`).
- The server sets `validate_input=False`. The schema is what the model sees. The code nodes also send `agent_state` to `update_claim`, which the model must not see. An unknown argument fails inside the tool and writes an error event (D46).
- The SDK client asks `tools/list` again after a `tools/call`. The policy needs `tools/list` for that.

**Why MCP** (D19): one door outside the sandbox that the network policy can describe, the same pattern as NemoForge.

**Alternatives.** A plain REST API with a policy by path. This is simpler. You lose the per-tool rule at the proxy. A stateful MCP session saves requests. The spike notes say a stateful server makes the client send `DELETE`, which the policy may deny: test it before you change.

### 3.5 Nemotron 3 Nano Omni: the vision model of the reader

**What it is** (NVIDIA page). An omni-modal reasoning model. A hybrid Mamba2-Transformer mixture of experts, about 31B parameters in total and about 3B active per token. Inputs: video, audio, image and text. Output: text. Context up to 256k tokens. The page lists document intelligence among its uses and gives 67.04 on OCRBenchV2 (English), an NVIDIA figure. License: NVIDIA Open Model Agreement.

**Important for you:** the page says the API accepts images, not raw PDFs. You must render each PDF page to an image first. `pypdfium2` does this in our generator. It is in the `dev` dependency group of `backend/pyproject.toml`, not in the runtime group. You must move it if the reader needs it.

**How we use it.** Version 1 reads documents with this model alone (D15). The model id is `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning` (`VLM_MODEL`). The Omni reader does not exist yet. It is your task #18.

**Why** (D15, D16). One model is the simplest path to a working demo. NVIDIA's own tables give higher document scores than Nano 12B v2 VL (`docs/READING-LIST.md`). Nano 12B v2 VL has no hosted endpoint today, so it is out. The specialised chain needs a GPU budget that we do not have.

**What to measure** (your tasks #19 to #22): accuracy per field, time per document, the rate limit of the key, cost of one call per page against one per document, reasoning on against off, image resolution, scans and phone photos. None of this is measured yet.

**Not checked:** whether the hosted endpoint supports JSON output that follows a schema (`docs/ARCHITECTURE.md` says "structured output"). Test it first. The VLM NIM documentation has a "Structured Generation" page that I did not read.

**Alternatives.** The hosted Nano 12B v2 VL when it exists. Nemotron 3.5 Super VL (120B, 12B active) as a benchmark on hard documents (backlog item 40). Self-hosting Omni with vLLM or a VLM NIM (section 3.9).

### 3.6 The specialised chain: Parse, OCR, page elements, table structure

**What they are.**

- **Nemotron Parse** (NVIDIA page): a NIM in the NeMo Retriever family. Since release 2.4 it is a stand-alone NIM with a document API (`POST /v1/parse`). It takes one page image as a multipart `document` part. It returns Markdown with coordinates, classes, reading order, tables and charts. It is not OpenAI chat completions any more. Before 2.4 it was served through the VLM NIM, and the change breaks old clients. BF16 on GB200, RTX PRO 6000 Blackwell Server Edition, H100 NVL, A100, L40S, A10G. The page gives no memory figure.
- **Nemotron OCR v2**: reads text, also in scans and photos. NeMo Retriever extraction uses it by default for local Hugging Face runs. The page does not state which OCR version the Helm and hosted deployments serve. This is the open point in `docs/READING-LIST.md`.
- **Page elements** and **table structure**: find titles, paragraphs, tables and charts, and rebuild tables row by row. The extraction page names the layout step but no table-structure model. `docs/READING-LIST.md` names `nemotron-table-structure-v1` from the model catalog. **I did not re-check that name.**
- **NeMo Retriever extraction** offers four methods: `pdfium`, `pdfium_hybrid`, `ocr`, `nemotron_parse` (NVIDIA page). Its optional captioning step uses Nano Omni by default. So NVIDIA itself uses Omni in this role.

**How we use them.** We do not. `OCR_URL`, `PAGE_ELEMENTS_URL`, `TABLE_STRUCTURE_URL` and `PARSE_URL` in `.env.example` are empty placeholders. The map shows the box as "planned".

**Why later** (D16): only where the measures show that Omni fails, and only with a GPU budget. Invoices and quotes with tables are the likely case.

**Alternatives.** A text-layer reader (pypdf) for clean PDFs. It was replaced by the fixture reader (D24). It fails on scans.

### 3.7 NeMo Guardrails (your task #62)

**What it is** (NVIDIA page). An open-source Python library that adds programmable rails to LLM applications. Five stages: input, retrieval, dialog, execution and output rails. Input rails check before the model call. Output rails check or block a response. Execution rails validate tool calls. You configure it with YAML and Colang. You can use it as a library, as a server with an OpenAI-compatible `/v1/chat/completions` endpoint, or as a microservice. The rail-types page lists jailbreak detection as an input rail use. It does not mention prompt-injection detection.

**What we do today.** No Guardrails. D28: "after the first demo". D60 made it your task #62, after your six reading tasks. Today's protection is code:

- The agent has no tool that decides. The model cannot change a score or an outcome.
- Document fields never reach the agent. `extract_document` returns only the type. The agent does see the file name, with control characters removed, and the customer text.
- Every message of the model passes `check_message` (`backend/app/agent/phrases.py`): a regex list of forbidden words, tool names and the model name. A refused message is asked again once, then it becomes the fallback.

**What NemoForge did** (`../nemoforge-plateforme`, read-only):

- `nemoguardrails==0.24.1` runs as a server. A small proxy sits between the agent and NIM. For each message it calls `POST /v1/checks` (input rails only), then forwards the request.
- The proxy was needed because the chat endpoint of Guardrails rejects a request that streams and also sends tools.
- The OpenShell provider profile points at the proxy on `host.openshell.internal`. The NVIDIA key stays in the proxy process.
- If Guardrails does not answer, the proxy refuses (fail closed).
- Trap: version 0.24.1 starts its server in verbose mode. A `[/...]` in a user message crashed `/v1/checks`. NemoForge patches this in `infra/guardrails/serve.py` and refuses to start on another version. Re-run its tests on any upgrade.

**What this means for you** (my reading of the code, not a decision):

- The customer message and the model answer pass through the sandbox model call. A proxy in the provider profile can cover those, as NemoForge did.
- The document text does not pass there. The reader runs in the backend. A rail on document text must run in the backend, before or after the Omni call.
- The task brief asks for a block that sends the claim to a person and never rejects it. The mechanism exists: `handover.py` and the status `in_review` (D56).
- Measure three things: an injected document is blocked, false blocks on the kit, time added per turn.

**Alternatives.** Keep code-only protection and extend the output filter. Use a small classifier model. Compare these with Guardrails on the same test set.

### 3.8 Brev

**What it is** (NVIDIA page). A platform that gives preconfigured GPU instances on several clouds, with drivers, CUDA, Python and Docker. It has a CLI and "launchables" (shareable one-click environments).

**How we use it** (`docs/DEPLOY.md`, D58, D61).

- One CPU VM named `claims-nvidia`: `n2d-standard-4` (4 vCPU, 16 GB), Ubuntu 22.04, kernel 6.8, about USD 0.13 per hour. No GPU in version 1.
- Services as systemd user units: the backend (`CLAIMS_MODE=sandbox`), the frontend (`vite preview`) and the OpenShell gateway. All listen on `127.0.0.1`.
- Docker Compose is not used on the VM. In `sandbox` mode, the backend calls the `openshell` command of the host.
- SSH is closed from outside. Access: `brev port-forward claims-nvidia -p 5173:5173`.
- To watch the demo without a Brev account, `infra/deploy/public.sh` puts Caddy with basic auth in front of the frontend. A person opens a public port of the Brev console. The CLI cannot do that step.
- You work on the VM with your own Brev account, in your own clone, on other ports.

**Why** (D25, D58). The sandbox needs Linux with Landlock. Brev gives that VM, and the intern can reach it.

**Limits.** The state of the services after `brev stop` and `brev start` is not tested. Several sandboxes at the same time are not tested. The VM costs about USD 3 per day. Stop it when nobody uses it.

**Not checked:** the Brev pages I fetched do not describe port access. The facts about HTTP ports and `gobrev.dev` come from `docs/LESSONS.md` (issue #65).

### 3.9 NIM self-hosting (later, M6)

**What it is.** A NIM is a container that serves a model on a GPU with a standard API. Hosting the models ourselves keeps claim data inside the company. This is the real reason for the NVIDIA stack (`docs/PRODUCT.md`): the models are open-weight.

**Where we are.** Nothing is self-hosted. Version 1 uses hosted endpoints and mock data. Self-hosting needs GPU budget (backlog M6, items 39 to 41).

**Known facts.**

- Parse: the GPU list above (NVIDIA page). No memory figure.
- `docs/DECISIONS.md` O13 says Omni needs 80 GB (one A100, about USD 1.66 to 1.98 per hour). **Not checked on an NVIDIA page.** Measure it.
- The VLM NIM documentation lists a query guide for Nemotron 3 Nano Omni (NVIDIA page). I did not read it.
- The brief names GPU bursts: 4x B200 to benchmark Nemotron 3.5 Super VL, 8x H100 for a LoRA fine-tune of the small VLM, and a batch of past claims.

**What changes in the code when we self-host.** Only the endpoints in `.env` (`VLM_BASE_URL`, `AGENT_BASE_URL`) and the provider profile. The Reader contract does not change. This is a reason for the contract.

**Note on Parse:** its API is not chat completions. A client for Parse is separate code from the Omni client.

---

## 4. The design rules, and why

Seven rules. Each one points to a test, except rule 7 (review). The rules are in `AGENTS.md`.

| # | Rule | Why | Test |
|---|---|---|---|
| 1 | The system never rejects a claim. Three outcomes only. | A person must decide every refusal. A machine refusal is a legal and trust risk. | `backend/tests/test_no_auto_rejection.py` |
| 2 | The customer panel never shows a fraud score, a flag or the word "fraud". Same message for `handler_review` and `investigation`. | A different message tells a fraudster that the system flagged the claim (D18). | `backend/tests/test_customer_view.py` |
| 3 | Every extracted field carries its source: document and page. | A handler must check a value next to its page. A value with no source cannot be checked. | `backend/tests/test_provenance.py` |
| 4 | An `investigation` always has a fraud report with reasons and evidence. | The investigator must know why. | `backend/tests/test_fraud_report.py` |
| 5 | The agent acts only through the tool gateway. Every call writes one event. | Audit. The pipeline panel and rule 1 of the routing read these events. | `backend/tests/test_tool_events.py` |
| 6 | No API key enters the sandbox. Claim data goes only to the configured model endpoints. | A confined agent that holds a key can leak it. | `backend/tests/test_no_secrets_in_sandbox.py` |
| 7 | The repository holds mock data only. | No real personal or medical data in git. | Review rule, every pull request. |

How each rule works in the code:

- **Rule 1.** `Outcome` has three values. `route()` returns one of them. The test also reads every string literal of the backend and forbids words like `reject` and `decline`. D54 lets two sandbox files use `denied`, because a sandbox says "Denied" of a network request.
- **Rule 2.** One function builds the customer view and drops the signals, score and report. The output filter blocks a model message that holds a forbidden word. The status is as blunt as the message: both outcomes that go to a person give `in_review`. The titles and stories of the demo kit never say what the checks find (D57).
- **Rule 3.** `ExtractedField.source` is required. `Source` needs a page of 1 or more and a snippet or a box. Pydantic refuses the rest. A reader that cannot give a source cannot store a field.
- **Rule 4.** The store refuses an `investigation` with no report (`InvestigationWithoutReport`). The outcome and the report are saved in one write (D32).
- **Rule 5.** `Gateway.call` writes an event in a `finally` block. A failing tool still writes one. An unknown tool writes one.
- **Rule 6.** Four layers: the build context of the image holds no `.env`; the runner passes no key in `--env`; the provider holds the key; the policy allows one host for the model. The probe checks the sandbox at run time.

Two further principles that are not in the list:

- **A model finds facts, code decides.** Score, routing, "done" for the Done button, the customer text and the fraud report are code. The model never sees the outcome or the score (D31).
- **The sandbox is not trusted.** The backend judges a probe by evidence, filters the text, drops events it should not accept, and sends its own customer message (D48, D52).

---

## 5. What you can challenge

You are expected to question these choices. For each one: what we chose, why it may be wrong, how to test another option, and what must not change.

**What must not change, whatever you test:**

- The seven rules of section 4 and their tests.
- The Reader contract: `classify` and `extract`, fields with sources.
- The tool gateway as the only door, with one event per call.
- The key stays outside the sandbox.
- `local` mode keeps working with no key.

### 5.1 One model reads the document

- **Chosen:** Omni alone (D15).
- **May be wrong:** a VLM can invent a value, mix columns in a table or fail on a blurry photo. It gives no reliable confidence. We route to review below 0.8, but a model confidence may not be calibrated.
- **Test:** build the harness first (#19). Compare Omni alone with Omni plus Parse text for the same page. Measure per document type and per kind (clean, scan, photo). Check if confidence tracks errors.

### 5.2 Fixed points and thresholds

- **Chosen:** 12 signals with fixed points, `investigation` at 60, `handler_review` at 20 (D34). `docs/FRAUD.md` says "a first guess".
- **May be wrong:** the weights have no data behind them. Two medium signals (40) do not reach 60. A signal of 10 points (R004, T002) alone does not even send a claim to review.
- **Test:** change `SIGNALS` in `backend/app/checks/rules.py`. Re-run `test_document_kit.py`: it names the story that moves. Measure on more seeded cases. A model score would be less explainable. Keep the reasons.

### 5.3 Tampering reads only PDF metadata

- **Chosen:** T001 and T002 read pypdf metadata and a short list of editing tools (`EDITING_TOOLS`). T003 compares the total with the lines.
- **May be wrong:** a scan or a photo has no metadata, so it passes. A fraudster can set the producer. A clean tool such as Word is not in the list. A real editor missing from the list is missed.
- **Test:** add forged PDFs to the kit. Add font and pixel analysis (backlog item 37). Measure the false alarm rate on the clean files.

### 5.4 The checks are not complete

- **Chosen:** C004 (photo against the damage) is in the table with no code. The consistency checks C001 to C003 compare fields only. `docs/ARCHITECTURE.md` says "VLM over all documents" for `check_consistency`. The code does not do this.
- **May be wrong:** the damage photos in the kit are drawings (D35). A photo check cannot be tested on them.
- **Test:** add real photos, build C004 with Omni, measure false alarms on the clean files (backlog items 33 and 35). A model finds a fact. Code gives the points.

### 5.5 The fraud report is a template

- **Chosen:** built from the signals only. It cannot invent a fact.
- **May be wrong:** it reads like a form. `docs/FRAUD.md` plans an LLM text in `nim` mode.
- **Test:** an LLM writes the sentences from the signals only. Check with a test that it adds no fact and no code that is not in the signals.

### 5.6 The output filter is a regex

- **Chosen:** `FORBIDDEN` blocks fraud words, tool names, any word with an underscore (`\w+_\w+`), outcome names and the model name.
- **May be wrong:** it blocks a harmless message that holds a snake-case word. It is English only (D9). It does not stop a polite lie or a promise of payment.
- **Test:** measure false blocks on real model messages (the `fallback` events). Try Guardrails output rails (section 3.7). Never remove the filter without a replacement and a test.

### 5.7 "Done" decided by a phrase list

- **Chosen:** the Done button and a short list of phrases (`DONE` in `phrases.py`) end the document step in code. Free text goes to the model, which calls `customer_is_done`.
- **May be wrong:** a model can call it too early. The prompt says never to. The graph checks only that the story is complete.
- **Test:** replay free-text endings with the live tests (`pytest -m live`).

### 5.8 One sandbox per claim, one process per turn

- **Chosen:** a new sandbox per claim, a new Python process per turn (D47).
- **May be wrong:** each turn pays about 1 s for imports. Each tool call opens an MCP session. Two visitors at the same time are not tested.
- **Test:** the spike notes suggest an HTTP server in the sandbox behind `openshell forward`. It would save about 1 s per turn and keep the session open. Measure many sandboxes at once on the VM first.

### 5.9 Is LangGraph needed?

- **Chosen:** LangGraph with a SQLite checkpoint (D36, Paul's decision).
- **May be wrong:** only two of five nodes call a model. The flow is linear. A plain function with a step in the database would do the same. The graph adds four packages to pin and to put in the image.
- **Test:** the scripted agent is a plain state machine. Compare its size and behaviour. The decision is Paul's. Raise it with him before you change it.

### 5.10 Hosted endpoints and the free key

- **Chosen:** hosted endpoints, free key, mock data (D15).
- **May be wrong:** about 40 requests per minute is the measured limit (O15), and it is not documented. A claim uses 16 to 23 calls. A queue of visitors at the event can fail with 429 errors. The trial terms apply.
- **Test:** measure the rate limit with two and with four parallel claims. Test `AGENT_THINKING=off`. Plan a retry or a queue.

### 5.11 No login, one shared password

- **Chosen:** no login in the app (D33). The public door is Caddy basic auth with one shared password (D61).
- **May be wrong:** `GET /claims/{id}` is the pipeline view. Anyone with the password can read it and any claim. The customer panel cannot be served alone.
- **Test:** add access control between the two views before you serve the customer panel on its own.

### 5.12 The report of the sandbox arrives in one batch

- **Chosen:** node and model events of a turn arrive after the turn. The backend places them by the clock of the sandbox (D53).
- **May be wrong:** a wrong clock puts events at the start or end of the turn. The pipeline panel is not live inside a turn.
- **Test:** `exec` streams stdout. One JSON line per event is possible and untested (spike notes).

### 5.13 Pinned OpenShell 0.1.5

- **Chosen:** 0.1.5, as NemoForge (D58).
- **May be wrong:** the project is at 0.1.x and moves fast. The log format and the credential rules change in 0.1.7 and 0.1.8. The denial parser reads the log format.
- **Test:** the table in `docs/DECISIONS.md` (spike, question E). Upgrade in a separate branch. Run `pytest -m sandbox`.

### 5.14 Evaluation numbers

- **Chosen:** we measured the agent with the fixture reader.
- **May be wrong:** the 9 of 9 says nothing about extraction. The test set is 24 documents and 9 hard versions of mock claims that we generated ourselves. A model trained on such PDFs may do better than on real ones.
- **Test:** the harness (#19) is the first step. Add documents with different layouts, languages and handwriting. Keep the rule: mock data only.

### 5.15 A fixture reader hides the hard part

- **Chosen:** the fixture reader keys on a SHA-256 (D35).
- **May be wrong:** any changed byte gives `unknown`. A real upload is never known. The flow looks good in the demo for the kit only.
- **Test:** this is your task. The hard versions in `data/mock/hard/` are the first test.

---

## 6. What can be redone

These parts are simple on purpose. Replace one only when the "when" is true.

| Part | Today | Replace when |
|---|---|---|
| Fixture reader | Reads `expected.json` by SHA-256 | Always, for the real demo. It stays as the base for tests. |
| Mock data | 3 customers, 6 policies, 24 documents, 9 hard versions | You need more cases, real photographs, other layouts. Never real data. |
| Damage photos | Simple drawings (D35) | You build C004. |
| SQLite store | One file, one connection, one lock | More than one backend process, or real traffic. |
| Event stream | The server reads the store every 0.3 s | Many clients, or latency matters. Push from the gateway. |
| Per-claim lock | A Python lock per claim, kept in memory | More than one backend process. |
| Points and thresholds | One table in `rules.py` | You have measured data (section 5.2). |
| Tampering | pypdf metadata and a short tool list | Scans and photos matter (backlog item 37). |
| Fraud report | A template | An LLM text, with a test that it adds nothing. |
| Output filter | One regex | Guardrails output rails, or a classifier, with a measured false block rate. |
| Scripted agent | A state machine for `local` mode | Never fully. CI needs it. Keep it equal to the graph in outcomes. |
| One process per turn | `openshell sandbox exec` | Turn time matters. An HTTP server in the sandbox is a candidate. |
| Access control | None. One shared password for the public door | The customer panel is served alone. |
| Frontend serve | `vite preview` on the VM | You need a real web server. |
| Handler screen | None. The pipeline panel is read-only (D29) | Backlog item 36. |
| Export | A mock claims system | There is a real one. |
| Date reading in `local` mode | Regex, day first for `03/08/2026` (`scripted.py`) | A customer uses another order. Add a locale on the claim. |

Do not replace these without a long talk with Paul: the Reader contract, the three outcomes, the one door, the placement of the key.

---

## 7. Glossary

### Insurance words

| Word | Meaning here |
|---|---|
| Policy | The insurance contract. It has a number, a line of business, a start, an end and a coverage limit. Not the sandbox policy: see below. |
| Insured (policyholder) | The person the policy covers. Our customer. |
| Line of business | The kind of insurance: travel, household (property) or accident (health). It sets the workflow. |
| Coverage limit | The largest amount the policy pays. Signal R004 fires at 95% of it. |
| Deductible | The part of a loss that the insured pays. Our data does not model it. |
| Claim | A request to the insurer to pay for a loss. |
| FNOL (first notice of loss) | The first report of a loss. The customer chat is an FNOL channel. |
| Claim file | All documents of one claim: invoices, reports, photos. |
| Claims handler | The person who reviews a claim that the system did not pass alone. |
| Straight-through processing | A claim validated with no review by a person. Outcome `straight_through`. |
| Handler review | A handler confirms or corrects the claim. Outcome `handler_review`. |
| Investigation | Strong fraud signals. A fraud report goes to an investigator. Outcome `investigation`. |
| Straight-through limit | The largest claimed amount that can pass with no review. CHF 2,500 travel, 2,000 property, 1,000 health. |
| Fraud signal | A fact that does not fit: an early claim, an edited PDF, a wrong name. It has points. |
| Tampering | A change to a document after the issuer made it. |
| Incident | The event that caused the loss. |

### Technology words

| Word | Meaning here |
|---|---|
| Sandbox | An isolated environment where the agent runs. Deny by default. |
| OpenShell gateway | The control plane of OpenShell. Not our tool gateway. |
| Tool gateway | Our code in the backend. The only door of the agent. It writes one event per call. |
| Supervisor | The trusted OpenShell process that checks requests and adds credentials. |
| Provider | An OpenShell object that holds a credential. Ours is `claims-nim`. |
| Provider profile | A YAML file that says which endpoints and paths a provider may reach. `infra/sandbox/profile-nim.yaml`. |
| Sandbox policy | A YAML file for a sandbox: filesystem, process, network. Not an insurance policy. |
| Landlock | A Linux feature that limits file access. We require it (`hard_requirement`). |
| L7 | The application layer. L7 rules look at the HTTP method, the path or the MCP tool. |
| Placeholder | The fake key value inside the sandbox. The supervisor swaps it for the real key on the way out. |
| Deny by default | Everything is blocked unless a rule allows it. |
| Rail | A check before or after a model call. NeMo Guardrails has input, retrieval, dialog, execution and output rails. |
| NIM | NVIDIA Inference Microservice: a container that serves a model. |
| `build.nvidia.com` | NVIDIA's hosted model catalog and trial endpoints. |
| LLM | Large language model. Text in, text out. |
| VLM | Vision-language model. It reads an image and text. |
| Mixture of experts (MoE) | A model that uses only part of its parameters for each token. "30B-A3B": 30B in total, 3B active. |
| Reasoning tokens | Tokens a model spends thinking before it answers. They count against the output limit. |
| Token (model) | A piece of text. Models count and limit them. |
| Token (MCP path) | The secret in `/mcp/<token>` that maps a sandbox to its claim. Not a model token. |
| OCR | Optical character recognition: text from an image. |
| Page elements, table structure | NeMo Retriever models that find layout parts and rebuild tables. |
| Parse | Nemotron Parse: a model that turns a page image into Markdown with layout. |
| MCP | Model Context Protocol: a standard to list and call tools over HTTP. |
| SSE | Server-sent events: the server pushes events over one HTTP response. The pipeline panel uses it. |
| LangGraph | A library that runs a graph of nodes with a saved state. |
| Node | One step of the graph: `intake`, `documents`, `checks`, `route` or `answer`. |
| Checkpoint | The saved state of the graph, in SQLite. The thread id is the claim id. |
| Turn | One customer message or one upload, handled by one `invoke`. |
| Fallback message | The fixed sentence "Sorry, I could not answer just now" when a model call fails or a message is refused. |
| Provenance | Where a value comes from: document and page. Rule 3. |
| Reader | The class behind `extract_document`. `FixtureReader` or `OmniReader`. |
| Fixture | Stored test data that stands in for a real component. |
| Kit | The mock documents for the demo (`data/mock/`). |
| Mode | `local`, `nim` or `sandbox`. |
| Brev | The platform that hosts our VM. |
| Colima | A Linux VM on a Mac that runs Docker. OpenShell needs it on a Mac. |
| `uv` | The Python package tool of the backend. |
| `ponytail` comment | A code comment that marks a known simplification and its upgrade path. |

---

## 8. What I could not check

Pages fetched on 2026-11-13:

- NemoClaw overview and NemoClaw with Hermes overview (`docs.nvidia.com/nemoclaw/latest/...`).
- OpenShell: the GitHub README, the documentation home, the architecture page, the policies overview, the security best-practices page.
- NeMo Guardrails: the documentation home and the rail-types page.
- `build.nvidia.com`: the model pages of Nemotron 3 Nano Omni and Nemotron 3.5 Lightning.
- Nemotron Parse NIM overview.
- NeMo Retriever multimodal extraction 26.8.1.
- NVIDIA Brev documentation home.
- VLM NIM documentation home 1.7.0.
- The NemoForge notes in `../nemoforge-plateforme/docs/` (read-only).

Two pages returned 404 (`openshell/latest/sandboxes/policies` and its `.html` form). I used the policies overview instead. I did not read the network rules and policy schema pages.

Marked "not checked":

1. Whether OpenShell 0.1.5 behaves as the latest pages describe. Our own tests and the spike notes cover only what we use.
2. The exact format of the credential placeholder and where the swap happens. The architecture page does not describe them. The source is our spike and tests.
3. The `protocol: mcp` and `enforce` details of the policy. The pages I read do not describe them. The source is our spike, our tests and the NVIDIA best-practices page (default `audit`).
4. Whether NemoClaw can run a custom LangGraph agent.
5. Whether the Lightning model supports switching reasoning off. Our notes say yes. The page I fetched does not document it. It is also the NVFP4 card, and our model id has no `NVFP4` in it.
6. Whether the hosted Omni endpoint supports JSON that follows a schema.
7. The rate limit of the free key. NVIDIA does not document it. About 40 per minute is our measure.
8. That Omni needs 80 GB (D O13), and the memory of Parse and OCR v2.
9. The name `nemotron-table-structure-v1` and the OCR version of the hosted and Helm deployments.
10. Brev HTTP ports and `gobrev.dev`. They come from our lessons, not from a Brev page.
11. Parameter count of Omni: the page says about 31B in total, the model id says `30b`. I used the page.

Errors I saw in other files (not fixed, as asked):

- `README.md`, section "The test data": "images may differ between a Mac and Linux, see D55". D55 is about `update_claim` in the documents node. The right references are D35 and D57.
- `docs/ARCHITECTURE.md` section 1 (the tool list) says `check_consistency` is "VLM over all documents". The code is deterministic (C001 to C003). The same section says `write_fraud_report` is "LLM". The code writes a template.
- `docs/FRAUD.md`: "In `nim` mode an LLM writes the text". The code always uses the template.
- `docs/EVALUATION.md` mentions `backend/app/agent/llm.py`. The file was removed with the loop of issue #12. The text says so for the removed loop.
- `frontend/src/mapModel.ts`: the box "Specialised chain" points to issue 24. Issue numbers #17 to #22 are the intern tasks (`README.md`), and backlog item 24 is the deploy task. I could not check the right issue number without GitHub.
- `frontend/src/mapModel.ts`: the check text for "Document reading" says `README.md`, section "The reader". That section is called "What is yours".
