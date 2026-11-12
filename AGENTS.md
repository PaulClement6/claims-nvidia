# claims-nvidia

A demo claims platform for insurance on the NVIDIA stack. A customer talks to a claim agent and uploads documents. The agent runs in an NVIDIA OpenShell sandbox. It calls document intelligence tools and workflows to read the documents, check the claim, look for fraud and route the claim. A second panel shows the full pipeline live.

Product vision: `docs/PRODUCT.md`. Read it before you plan a feature.
Target: a working demo on real-looking mock claim files at the Q1 2027 event.

## Read first

| File | Content |
|---|---|
| `docs/PRODUCT.md` | Problem, users, the two panels, the claim flow, the demo that defines "done" |
| `docs/ARCHITECTURE.md` | Components, agent and tools, modes, data model, event stream |
| `docs/WORKFLOWS.md` | The three claim workflows: documents, fields, rules |
| `docs/FRAUD.md` | Fraud signals, score, routing, fraud report |
| `docs/MOCK-DATA.md` | Mock customers, policies and the three demo claim files |
| `docs/DECISIONS.md` | Decisions taken and decisions still open |
| `docs/READING-LIST.md` | NVIDIA documentation to read, with what to take from each |
| `docs/BACKLOG.md` | Ordered tasks, with the tasks kept for the intern |
| `docs/BRIEF.md` | Paul's first brief, kept as the source |

## Status

2026-11-12: M0 to M2 are built (issues #2 to #11): the full claim flow works in `local` mode, and the three mock claim files give `straight_through`, `handler_review` and `investigation`. M3 has started: the LLM agent runs in `nim` mode (issue #12, `docs/EVALUATION.md`). Next for the base: the OpenShell sandbox and the deploy on the VM. The intern owns M4: the document reading pipeline behind the `Reader` contract (`README.md`).

## Commands (target, same as NemoForge)

Backend in `backend/` (Python, FastAPI, uv). Frontend in `frontend/` (React, Vite, TypeScript).

- Test: `cd backend && uv run pytest` and `cd frontend && npm test`
- Types: `cd backend && uv run mypy .` and `cd frontend && npm run typecheck`
- Lint: `cd backend && uv run ruff check . ../scripts` and `cd frontend && npm run lint`
- Mock documents: `cd backend && uv run python ../scripts/generate_mock_documents.py`
- Run: `cd backend && uv run uvicorn app.main:app --env-file ../.env` (loads `.env`: `CLAIMS_MODE`, `NVIDIA_KEY`, the model settings) and `cd frontend && npm run dev`
- Live tests (the real model, needs `NVIDIA_KEY` in `.env`, skipped in CI): `cd backend && uv run pytest -m live`. Measure: `cd backend && uv run python ../scripts/measure_agent.py` (`docs/EVALUATION.md`)
- Run tests and types before you say a task is done.

## Non-negotiables

Each rule must point to a test that checks it. The ticket that first touches a rule creates the test file.

1. The system never rejects a claim. The only outcomes are `straight_through`, `handler_review` and `investigation`. → `backend/tests/test_no_auto_rejection.py`
2. The customer panel never shows a fraud score, a fraud flag or the word "fraud". The customer message is the same for `handler_review` and `investigation`. → `backend/tests/test_customer_view.py`
3. Every extracted field carries its source: document and page. A field with no source is not stored. → `backend/tests/test_provenance.py`
4. A claim routed to `investigation` always has a fraud report with reasons and evidence. → `backend/tests/test_fraud_report.py`
5. The agent acts only through the tool gateway. Every tool call writes one event with inputs, outputs, model and duration. → `backend/tests/test_tool_events.py`
6. No API key ever enters the sandbox. Claim data goes only to the configured model endpoints. → `backend/tests/test_no_secrets_in_sandbox.py`
7. The repository holds mock data only. No real personal or medical data. → review rule, checked in every PR.

## Work tracking

- All work lives in GitHub Issues. Use the `gh` CLI to read and update them.
- A feature PRD is an issue with the label `prd`. Tickets are issues that link to it.
- Labels: `afk` (agent can do it alone), `human` (needs a person), `intern` (kept for the intern), `blocked`.
- One ticket = one branch = one pull request. The PR body says `Closes #<number>`.
- Skills live in `.agents/skills/` (copied from `nemoforge-plateforme` on 2026-11-12).

## How to work

- Use TDD: write one failing test, make it pass, then refactor.
- Prefer deep modules: few modules, small interfaces, most logic inside.
- Work in vertical slices: each change touches every layer it needs and is testable end to end.
- `local` mode must always work with no GPU and no sandbox. CI runs in `local` mode.
- Do only what the current issue asks. List other problems at the end of your report.
- Never push to `main`: `main` takes pull requests only, and the CI check `checks` must be green.
- The coordinating agent merges a pull request of the base when CI is green and there is no conflict (Paul, 2026-11-12). Other agents and the intern do not merge: Paul or the coordinating agent does.
- Check the NVIDIA docs before you write code against a NVIDIA API. The stack changes every month. `docs/READING-LIST.md` has the links.

## How to talk to me

- Follow the `ste` skill for all explanations, summaries and questions.
