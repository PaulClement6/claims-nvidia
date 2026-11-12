# Architecture

Date: 2026-11-12. Status: **decided for version 1** (grill session). The decisions and the open points are in `docs/DECISIONS.md`.

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
             ├─ classify_document          fixture, then VLM
             ├─ extract_document           fixture, then VLM → JSON with sources
             ├─ run_checks                 rule engine (deterministic)
             ├─ check_tampering            file forensics (deterministic)
             ├─ check_consistency          VLM over all documents
             ├─ assess_and_route           score → outcome (deterministic)
             └─ write_fraud_report         LLM, from the signals only
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
| `documents` | Model. Asks for the required documents and reads each upload. | `required_documents`, `classify_document`, `extract_document`, `get_claim_summary`, `customer_is_done` |
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
| `nim` | The LangGraph graph, in the backend process. Model: `ChatOpenAI` on the hosted NVIDIA endpoint. | Fixture reader or Omni reader (hosted endpoint) | None | Work on the agent and on extraction quality. |
| `sandbox` | The same graph, in OpenShell. The tools are MCP tools. | Same as `nim` | Enforced | The demo, on the Brev VM. |

The three modes share the tool gateway, the schemas, the rules and the UI. Only the agent and the model clients change. `sandbox` mode runs the same `build_graph`: the spike of issue #13 proved the graph, the model key swap and the MCP tools in OpenShell. The ticket for the MCP gateway (#14) connects them. `local` mode imports no LangChain and no LangGraph: the packages load only in `nim` mode. `local` mode must give the right outcome for the three mock claim files.

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

## 6. Workflows

A workflow is declarative data plus rules: required documents, the claim schema, the rule set, the straight-through limit. One engine runs the three. First version: plain Python, state in SQLite. No Temporal (decision D20).

## 7. Sandbox

Same pattern as NemoForge: OpenShell with a deny-by-default policy. The policy allows the tool gateway and the model provider, and nothing else. No folder of the host except a scratch folder. OpenShell injects credentials outside the sandbox. The pipeline panel shows the policy and the denials.

The sandbox is part of the base and runs on the Brev VM (decision D25). The agent is the LangGraph graph of section 1a (decision D36, which replaces Hermes). The spike of issue #13 ran it in OpenShell: the backend sends each turn with `openshell sandbox exec`, the model call goes through the provider key swap, and the tools are MCP tools. NeMo Guardrails comes after the first demo.

NemoForge runs OpenShell 0.1.5 with provider profiles (`inference.local` was removed in 0.1.x). Start from that setup, then test OpenShell 0.1.8: its gateway refuses to start without authentication, and static credentials go only in the `Authorization` header. Reuse its spike notes: `../nemoforge-plateforme/docs/DECISIONS.md` and `docs/LESSONS.md`.

## 8. Stack

- Backend: Python, FastAPI, uv, Pydantic, SQLite, pytest, mypy, ruff.
- Frontend: React, Vite, TypeScript.
- Run: Docker Compose on one Brev VM. No GPU in version 1: the models are hosted NVIDIA endpoints. Laptop for `local` mode.
- Model calls: OpenAI-compatible chat completions for the VLM and the agent LLM, on `integrate.api.nvidia.com`. Later: dedicated APIs for OCR, page elements, table structure and Parse.
- UI: same look as NemoForge.

## 9. Repository layout (target)

```
backend/app/
  api/          claims, documents, events, views
  agent/        graph (LangGraph, no store import), graph_agent (backend runner), scripted agent, phrases (shared words and rules), sandbox launcher
  tools/        tool gateway, one module per tool, guard (rule 1 of the routing)
  docintel/     reader contract, fixture reader (base); Omni reader, evaluation (intern)
  workflows/    engine, property.py, health.py, travel.py
  checks/       rules, tampering, consistency
  fraud/        score, routing, report
  store/        database, mock policies, mock claims system
backend/tests/
frontend/src/   CustomerPanel, PipelinePanel
data/mock/      customers, policies, claim files, expected results
infra/          compose, sandbox policy, Brev setup
scripts/        mock document generator, evaluation
docs/
```
