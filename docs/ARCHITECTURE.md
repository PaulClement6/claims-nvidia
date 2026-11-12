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
        │                          claim agent (Hermes, headless)
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

- The **agent** talks to the customer and chooses which tool to call. It holds no rule and no score. It cannot change an outcome.
- The **tool gateway** is the only door out of the sandbox, apart from the model. It writes one event per call. The pipeline panel is a view of these events.
- **Scores, rules and routing are deterministic code**, not model output. A model finds facts (fields, descriptions, mismatches). Code decides. This makes the result repeatable and testable.

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
| `nim` | LLM with tool calling | Fixture reader or Omni reader (hosted endpoint) | None | Work on the agent and on extraction quality. |
| `sandbox` | Hermes in OpenShell | Same as `nim` | Enforced | The demo, on the Brev VM. |

The three modes share the tool gateway, the schemas, the rules and the UI. Only the agent and the model clients change. `local` mode must give the right outcome for the three mock claim files.

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

The sandbox is part of the base and runs on the Brev VM (decision D25). The agent is Hermes, driven without a terminal through its API server; a spike confirms this (decision D27). NeMo Guardrails comes after the first demo.

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
  agent/        runner, scripted agent, LLM agent, sandbox launcher
  tools/        tool gateway and one module per tool
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
