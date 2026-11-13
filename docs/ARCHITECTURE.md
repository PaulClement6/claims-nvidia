# Architecture

Date: 2026-11-12, checked against the code on 2026-11-13. Status: **decided for version 1** (grill session). M0 to M3 are built; the document reading pipeline (M4) is not. The decisions and the open points are in `docs/DECISIONS.md`.

The rule of the model, taken from NemoForge: **a workflow decides, a tool acts, an agent reasons inside a sandbox.**

## 1. Components

```
 CUSTOMER PANEL (chat, upload)            PIPELINE PANEL (events, data, fraud report)
        │ messages, files                          ▲ event stream
        ▼                                          │
 Backend (FastAPI)
   claims API · customer view · handler view · event stream · mock policy DB · mock claims system
        │
        ├─ Agent runner ───────► OpenShell sandbox (deny by default)
        │                          claim agent (LangGraph graph, section 1a)
        │                          ├─ model ──► provider ──► agent LLM (NIM)
        │                          └─ tools ──► L7 proxy ──► Tool gateway (MCP)
        │
        └─ Tool gateway (trusted code, outside the sandbox)
             ├─ get_customer_policies      mock policy DB
             ├─ get_claim_summary          the claim as the agent may see it
             ├─ update_claim               save the story, the date, the amount
             ├─ required_documents         documents the workflow asks for
             ├─ classify_document          fixture reader (the VLM is M4)
             ├─ extract_document           fixture reader → JSON with sources (the VLM is M4)
             ├─ run_checks                 rule engine (deterministic)
             ├─ check_tampering            file forensics (deterministic)
             ├─ check_consistency          deterministic (a VLM check, C004, is M5)
             ├─ assess_and_route           score → outcome (deterministic)
             └─ write_fraud_report         template, from the signals only (an LLM is M5)
```

- The **agent** is a LangGraph graph (decision D36). The model talks to the customer and, in two nodes, calls the tools of that node. The graph holds the order of the steps. The agent holds no rule and no score. It cannot change an outcome.
- The **tool gateway** is the only door out of the sandbox, apart from the model. It writes one event per call. The pipeline panel is a view of these events.
- **Scores, rules and routing are deterministic code**, not model output. A model finds facts (fields, descriptions, mismatches). Code decides. This makes the result repeatable and testable.

### 1a. The agent graph

The claim flow is a graph of five nodes (`backend/app/agent/graph.py`). The order of the steps is code, not a prompt.

```
 intake ──► documents ──► checks ──► route ──► answer ──► end
 (model)    (model)       (code)     (code)    (code)
```

| Node | Does | Tools |
|---|---|---|
| `intake` | Model. Asks for the story, the date and the amount, and saves them. | `get_claim_summary`, `update_claim` |
| `documents` | Model. Asks for the required documents and reads each upload. | `required_documents`, `classify_document`, `extract_document`, `get_claim_summary`, `update_claim`, `customer_is_done` |
| `checks` | Code. Reads any document the model left unread, then `run_checks`, `check_tampering`, `check_consistency`. | none for the model |
| `route` | Code. `assess_and_route`. For `investigation` it writes the fraud report. | none for the model |
| `answer` | Code. Sends the `customer_message` word for word, closes the claim. | none for the model |

- **Edges.** `intake` goes to `documents` when the three facts are saved, in the same turn. `documents` goes to `checks` when the customer is done. After that, no model runs. A node cannot be skipped.
- **Done.** Code decides it for the Done button and the usual phrases. For free text the model calls `customer_is_done`. This tool is not a gateway tool. It sets a flag in the graph state and changes nothing else.
- **State.** The graph state holds the conversation (customer and agent messages only), the step that is due, and the ids of the documents that were read. The tool calls of a turn stay inside the node. The state is saved after each node in a SQLite checkpoint (`CLAIMS_CHECKPOINTS`). The thread id is the claim id.
- **Turns.** One customer message or one upload is one `invoke`. A turn ends after one message for the customer. If a code node fails, the customer gets the fallback message. The step is in the checkpoint, so the next message resumes at that node.
- **Independence.** `build_graph(model, call_tool, checkpointer, specs, emit)` imports nothing from the store, the gateway or FastAPI (a test checks it). `call_tool(name, inputs)` is the only way to the tools. In the backend it calls the gateway for one claim. In the sandbox it calls the MCP tools. The same file runs in both places.
- **Events.** A node writes one `workflow` event when it starts (stage = node name). The pipeline panel draws the graph strip from these events. The scripted agent of `local` mode writes the same events. Each model call writes one `llm` event.
- **Second line.** The trusted code keeps the rules of issue #12 (decision D40). `app/tools/guard.py` refuses `assess_and_route` until the story is saved, every document is read and the three checks ran; the customer gets the routing text word for word; every message of the model passes the output filter.

## 2. Document intelligence

| Step | Model | Notes |
|---|---|---|
| OCR | Nemotron OCR v2 | Reads scans and phone photos. Check which OCR version the NIM serves (see reading list). |
| Layout | Page-elements model | Finds titles, paragraphs, tables, charts. |
| Tables | Table-structure model | Rebuilds tables row by row. Needed for invoices and quotes. |
| Complex pages | Nemotron Parse | Whole page in reading order. Standalone NIM with a document API. |
| Understanding | Small VLM: Nemotron Nano 12B v2 VL or Nemotron 3 Nano Omni | Looks at the page image and the extracted text together. Fills the claim schema in JSON. Describes damage photos. |
| Benchmark | Nemotron 3.5 Super VL | Tested against the small VLM on hard documents. |

**Version 1 uses one model: Nemotron 3 Nano Omni alone** (`nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`), on the hosted NVIDIA endpoint: page image in, JSON with structured output out. Nemotron Nano 12B v2 VL is out (no hosted endpoint on 2026-11-12). The other rows of the table come later, where the accuracy numbers say they help. Measure each addition.

### The contract of `extract_document`

This contract is the boundary between the base and the intern's pipeline (decision D24).

- In: one stored `Document` (file, pages) and the workflow.
- Out: the document type, a list of `ExtractedField` (each with its source: document, page, snippet or box) and the line items.
- A reader is one class with this contract. The setting `CLAIMS_READER` picks it: `fixture` (the base) or `omni` (the intern).
- The **fixture reader** finds the `expected.json` of the uploaded file by the SHA-256 of its bytes and returns its content. An unknown file gives the type `unknown` and no field. It calls no model.

Checks, score, routing and the UI read only the output of this contract. They do not change when the reader changes.

## 3. Modes

| Mode | Agent | Document reading | Sandbox | Use |
|---|---|---|---|---|
| `local` | Scripted state machine | Fixture reader | None | Development, CI, tests. No GPU, no key. |
| `nim` | The LangGraph graph, in the backend process. Model: `ChatOpenAI` on the hosted NVIDIA endpoint. | Fixture reader (the Omni reader is M4) | None | Work on the agent and on extraction quality. |
| `sandbox` | The same graph, in OpenShell. The tools are MCP tools. | Same as `nim` | Enforced | The demo, on the Brev VM. |

The three modes share the tool gateway, the schemas, the rules and the UI. Only the agent and the model clients change. `sandbox` mode runs the same `build_graph`: the spike of issue #13 proved the graph, the model key swap and the MCP tools in OpenShell. Issue #14 built it (section 7). `local` mode imports no LangChain and no LangGraph: the packages load only in `nim` mode. `local` mode must give the right outcome for the three mock claim files.

## 4. Data model (Pydantic)

- `Customer`, `Policy` (number, line of business, start, end, coverage limit).
- `Claim`: id, customer, policy, type, description, incident date, claimed amount, documents, signals, score, outcome, fraud report, status.
- `Document`: id, file, document type, pages, fields, line items, file metadata.
- `ExtractedField`: name, value, confidence, `source` (document id, page, text snippet or box). The source is required.
- `Signal`: code, category (`rule`, `tampering`, `consistency`), severity, points, title, detail, evidence (list of sources).
- `PipelineEvent`: sequence, time, actor (`agent`, `tool`, `workflow`, `sandbox`), stage, title, inputs, outputs, model, duration.
- One typed claim schema per workflow, in `docs/WORKFLOWS.md`.

## 5. Views

- **Customer view** of a claim: messages and status only. Built by one function that drops signals, score and report. It gives the same exact message for `handler_review` and `investigation`. Non-negotiable 2 tests this function.
- **Pipeline / handler view**: everything.

### 5a. Routes

`GET /health`, `GET /customers`, `POST /claims`, `GET /claims/{id}` (the full claim, for the pipeline panel), `GET /claims/{id}/customer` (the customer view), `GET /claims/{id}/policies`, `PUT /claims/{id}/policy`, `GET` and `POST /claims/{id}/messages`, `POST /claims/{id}/documents`, `GET /claims/{id}/documents/{doc}/file`, `GET /claims/{id}/events` (the event stream), `GET /claims/{id}/sandbox`, `POST /claims/{id}/sandbox/probe`, and under `/demo`: `GET /kit`, `GET /scenarios`, `GET /kit/{folder}/{file}`, `POST /reset`. The MCP server of the sandbox is mounted at `/mcp/<token>`. The code is `backend/app/main.py` and `backend/app/demo.py`.

## 6. Workflows

A workflow is declarative data plus rules: required documents, the claim schema, the rule set, the straight-through limit. One engine runs the three. First version: plain Python, state in SQLite. No Temporal (decision D20).

## 7. Sandbox

Same pattern as NemoForge: OpenShell with a deny-by-default policy. The policy allows the tool gateway and the model provider, and nothing else. No folder of the host except a scratch folder. OpenShell injects the model key outside the sandbox. The pipeline panel shows the policy, the denials and a confinement test (issue #15, see "The pipeline panel" below).

`CLAIMS_MODE=sandbox` (issue #14, decisions D44 to D49) runs the graph of section 1a in one OpenShell sandbox per claim. OpenShell 0.1.5 with Colima. Nothing in the sandbox is trusted: the backend decides what the customer reads.

```
 Backend (127.0.0.1:BACKEND_PORT)                    OpenShell sandbox `claims-<claim id>`
   SandboxAgent ── sandbox exec, JSON on stdin ───►    python -m app.agent.sandbox_turn
     create (1st turn) · turn · delete (close, reset)    the graph (same graph.py as nim mode)
   MCP server  /mcp/<token>  ◄── MCP over HTTP ────     call_tool = MCP client, MCP_URL
     guarded_call → gateway.call → one tool event        model ──► provider claims-nim ──► NIM
```

- **One setup, once**: `infra/sandbox/setup.sh` builds the image `claims-agent:1` (Python slim, non-root, the pinned packages, the graph files of `backend/app`, no `curl`), imports the profile `claims-nim` and creates the provider `claims-nim` with the key from `NVIDIA_KEY` (in the environment of that one command). All names start with `claims-`. The backend stops at startup, naming this script, when `openshell`, the gateway, the provider or the image is missing.
- **MCP server** (`app/tools/mcp_server.py`): the tools of the gateway that the graph uses, on `/mcp/<token>`. The token fixes the claim. Every call goes through `guarded_call` and `gateway.call`, so rule 1 holds and one tool event is written per call. These tool events are the trusted ones.
- **Policy** (`infra/sandbox/policy.yaml`, made for each sandbox by `render_policy`): one network policy, the MCP endpoint of this claim (`host.openshell.internal`, the backend port, the path with the token), methods `initialize`, `notifications/initialized`, `tools/list`, and `tools/call` for the tools of `GRAPH_TOOLS`. The model goes through the provider profile (`POST /v1/chat/completions` on `integrate.api.nvidia.com`). Landlock `hard_requirement`; `/sandbox` writable; the rest read-only. Another host, another path of the backend, the MCP path of another claim, another tool and `GET /v1/models` are denied (`pytest -m sandbox` checks each).
- **A turn**: `openshell sandbox exec` with `{"text", "document_id"}` on stdin. The process runs the graph (SQLite checkpoint in `/sandbox/state`, thread id = claim id) and prints one JSON line: the reply and the node and model events. The backend stores the events as display-only (`"source": "sandbox"`), filters the reply, and, when the claim has an outcome, sends its own `customer_message` word for word.
- **Events**: `sandbox` actor: `created` (seconds, policy file, shortened path, how to read the denials), `turn` (seconds), `deleted`, `failed` (the reason). Time per turn: `docs/EVALUATION.md`.
- **Files and log**: the policy file of each sandbox is in `CLAIMS_SANDBOX_DIR` (default `.dev-data/sandboxes/<name>.policy.yaml`, mode 0600, it holds the token; its path is in the `created` event). The denials are `openshell logs <name> --source sandbox`, the lines with `DENIED` (the `created` event holds the command). Those OpenShell lines show the whole path, so the token: the backend cuts it before it stores or shows a line.

### The pipeline panel (issue #15, decisions D50 to D54)

The pipeline panel has a Sandbox section right under the graph strip. It shows only when the claim has a sandbox. The sidebar shows the mode (`local`, `nim`, `sandbox`) and the agent model. `GET /health` gives both (`mode`, `agent_model`, null in `local`).

- **Policy**: `GET /claims/{id}/sandbox` (pipeline view only, 404 when the claim has no sandbox). It gives the name, the state (`running`, `deleted`), the time `sandbox create` took, and the policy of this claim as data: the model endpoint (from the provider profile), the tool gateway (host, port, MCP path, methods, tools), the read-only and read-write folders, the user id and the Landlock setting. The token is cut to its first 6 characters. The full token is in no answer, no event and no log.
- **Denials**: after each turn, and after the probe, the backend reads the sandbox log (`OpenShell.log_lines`) and stores each new `DENIED` line as one event: actor `sandbox`, stage `denied`, title `Denied: <what>`, the reason of the line in the outputs. The token is cut from the line first. The panel shows them as a table (time, what, reason).
- **Confinement probe**: `POST /claims/{id}/sandbox/probe` (409 when the sandbox is not running, in the per-claim lock) runs a fixed script in the sandbox. The script is a constant of the backend (`PROBE` in `app/agent/sandbox_panel.py`), sent on stdin to `python -`: nothing of the request reaches the command. It tries five things, each with a timeout of 8 s: an HTTPS request to `example.com`; `tools/call` on the MCP path of a made-up other claim; a tool the policy does not allow (`write_fraud_report`) on its own path; reading the model key (the script reports only if it starts like a real key, and the placeholder text); listing `/Users` and `/host`. The backend decides from the answers, not the sandbox: a try is "denied" only when its answer proves it (HTTP 403, the supervisor refusing the connection, no key, no folder). One `probe` event per try (`Probe: other host → denied`), then the denials it caused. A try that is not denied is said plainly, and the endpoint returns `"confined": false`.
- **Order of events**: the node and model events of the sandbox arrive in one batch after the turn. Each one carries `at`, the time it happened by the clock of the sandbox. The backend stores that as the display time, kept inside the turn (`display_time`), and the panel sorts by time. The tool events written by the gateway keep their own time and stay the trusted events.

NemoForge runs OpenShell 0.1.5 with provider profiles (`inference.local` was removed in 0.1.x). This setup stays on 0.1.5. Later, test OpenShell 0.1.8: its gateway refuses to start without authentication, and static credentials go only in the `Authorization` header (table in `docs/DECISIONS.md`, spike section). NeMo Guardrails is issue #62 (M4, decision D60). NemoForge notes: `../nemoforge-plateforme/docs/DECISIONS.md` and `docs/LESSONS.md`.

## 8. Stack

- Backend: Python, FastAPI, uv, Pydantic, SQLite, pytest, mypy, ruff.
- Frontend: React, Vite, TypeScript.
- Run: Docker Compose on one Brev VM. No GPU in version 1: the models are hosted NVIDIA endpoints. Laptop for `local` mode.
- Model calls: OpenAI-compatible chat completions for the VLM and the agent LLM, on `integrate.api.nvidia.com`. Later: dedicated APIs for OCR, page elements, table structure and Parse.
- UI: same look as NemoForge.

## 9. Repository layout

```
backend/app/
  main.py       FastAPI app and the claims routes (section 5a)
  demo.py       demo routes: kit, scenarios, reset
  models.py     the Pydantic models of section 4
  views.py      the customer view
  stream.py     the event stream (server-sent events)
  agent/        graph (LangGraph, no store import), graph_agent (backend runner), scripted agent, handover, phrases (shared words and rules), model, sandbox (runner), sandbox_turn (the turn process in the image), sandbox_image (image build context), sandbox_panel (policy, denials, probe)
  tools/        tool gateway, one module per group of tools, guard (rule 1 of the routing), mcp_server
  docintel/     reader contract, fixture reader (base); the Omni reader is the work of the intern
  workflows/    engine, property.py, health.py, travel.py
  checks/       rules, tampering, consistency
  fraud/        routing (score and outcome), report (template)
  store/        SQLite store, seeded from data/mock/policies.json
backend/tests/
frontend/src/   App, Demo, CustomerPanel, Chat, PipelinePanel, PipelineSections, GraphStrip, SandboxSection, Health, ClaimMap and mapModel (the Map page)
data/mock/      policies, claim folders, hard versions, index.json, expected results
infra/sandbox/  Dockerfile, policy template, provider profile, setup.sh
infra/deploy/   install.sh (the Brev VM), public.sh (the public door)
scripts/        mock document generator (mockdocs/), measure_agent.py
docs/
```
