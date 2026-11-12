# Decisions

## Taken (2026-11-12, interview with Paul)

| # | Decision |
|---|---|
| D1 | Three claim types: property / household, health / accident, travel. |
| D2 | Routing: type first (one workflow per type), then risk (three outcomes). |
| D3 | Fraud signals of the first version: rule-based red flags, document tampering, cross-document consistency. Duplicate detection comes later. |
| D4 | The customer deposits the claim, through a chat with a claim agent. |
| D5 | UI with two panels: customer panel (agent only) and pipeline panel (all the plumbing). |
| D6 | The agent runs in an OpenShell sandbox (NemoClaw / OpenShell). It calls document intelligence tools and workflows. |
| D7 | No automatic rejection. A flagged customer sees "escalated to a specialist". The back end shows the fraud flag and a report with the reasons. |
| D8 | Demo login: the customer picks a mock account, then types or searches a policy of that account. |
| D9 | English only. |
| D10 | Mock documents that look real. No real claim data. |
| D11 | Backend FastAPI. Stack close to NemoForge. Demo on a Brev instance. |
| D12 | Replaced by D22. |
| D13 | First define what we build. Then choose the features the intern builds and improves. |

## Taken (2026-11-12, grill session)

| # | Decision | Closes |
|---|---|---|
| D14 | At the event, visitors use the demo themselves. They do not upload their own documents. We give a kit of 20 to 30 mock documents for the three mock accounts: clean, scanned, photographed and tampered. A visitor picks an account, tells any story and mixes the documents. The screen says "do not upload a real document". The reset deletes the uploaded files. | |
| D15 | Version 1 reads documents with one vision-language model alone: Nemotron 3 Nano Omni (`nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`). It gets the page image and fills the claim JSON. We call the hosted NVIDIA endpoint with a free developer key. This is acceptable because the data is mock data. Nemotron Nano 12B v2 VL is out: it has no hosted endpoint today. | O1, O4 |
| D16 | The specialised chain (Nemotron Parse, OCR v2, page elements, table structure) comes after version 1, and only for the cases where the measures show that Omni fails. These are intern tickets. The chain needs GPU budget that we do not have yet. | O4 |
| D17 | Agent model: a one-day spike tests Omni in this role, so one model does both jobs. If it fails, the agent uses `nvidia/nemotron-3.5-lightning-30b-a3b`, which NemoForge already uses. | O3 |
| D18 | The customer sees the same exact message for `handler_review` and `investigation`. Only `straight_through` has a different message. A test compares the two texts. Reason: a different message tells a fraudster that the system flagged the claim. | |
| D19 | The agent reaches the tools through one MCP server outside the sandbox. The sandbox network policy allows it. Same pattern as NemoForge. | O5 |
| D20 | No Temporal. Plain Python and SQLite. | O7 |
| D21 | Stand-alone project. The tool gateway and the workflows hold no UI code, so they can move to NemoForge later. | O8 |
| D22 | Repository `PaulClement6/claims-nvidia`. Public, so the intern can work on it and deploy it on a VM. | O10, D12 |
| D23 | The intern is a data engineer. He works with GitHub Copilot, for two to three weeks, starting now. He owns the document reading pipeline: six guided tasks, each with things to test and parameters to tune (`docs/BACKLOG.md`, M4). The README tells him what is his. | O11 |
| D24 | Boundary between the base and the intern: the tool `extract_document` has a fixed contract (a document in, fields with their sources out). In the base a **fixture reader** returns the expected fields of each kit document from its `expected.json`. The full flow works with no model. The intern puts the real reader behind the same contract. The fixture reader replaces the "text layer of the PDF" reader. | |
| D25 | The OpenShell sandbox is part of the base, not a later step. It runs on the Brev VM, and the intern has access to this VM. `local` mode still works with no sandbox and no GPU. | |
| D26 | The UI follows the look of NemoForge. | |

## Taken by default (Paul did not answer; he can change them)

| # | Decision | Closes |
|---|---|---|
| D27 | Agent harness: Hermes, driven without a terminal through its API server. A spike confirms it. Start from the OpenShell 0.1.5 setup that NemoForge proved, then test 0.1.8 (breaking changes: the gateway refuses to start without authentication, and static credentials go only in the `Authorization` header). | O2 |
| D28 | NeMo Guardrails in front of the agent model: after the first demo. | O6 |
| D29 | The pipeline panel is read-only in version 1. Confirm and correct per field is a later ticket. | O9 |

## Taken while we built the base (2026-11-12)

| # | Decision |
|---|---|
| D30 | The agent runs the checks only when the customer says "done" (or presses Done). When the required documents are all present it asks for more documents first. Reason: a visitor can add a second photo or any other document of the kit. |
| D31 | The agent gets only the customer text from `assess_and_route`. It never sees the outcome, the score or the reasons. Trusted code writes the fraud report when the outcome is `investigation`. Reason: the next agent is an LLM, and it must not be able to tell the outcome or to skip the report. |
| D32 | The outcome `investigation` and its fraud report are saved in one write. The store refuses an `investigation` with no report (non-negotiable 4). |
| D33 | Two API views: `GET /claims/{id}/customer` for the customer panel, `GET /claims/{id}` and `/events` for the pipeline panel. There is no access control between them: both panels are on one screen in the demo. Add it before the customer panel is served alone. |
| D34 | Thresholds, each one named constant: low confidence below 0.8; R003 window 0 to 14 days; T003 tolerance CHF 0.05. The points per signal are one table in `backend/app/checks/rules.py`. |
| D35 | The fixture reader finds a mock document by the SHA-256 of its bytes. The mock PDFs are byte-identical on every run of the generator. The damage photos are simple drawings: real-looking photos come with the document kit (issue #23). |

## Open

| # | Question | Proposal |
|---|---|---|
| O12 | Are the documents, fields and limits of `docs/WORKFLOWS.md` right for the claim types we want to show? | Paul confirms or corrects. The base uses them as written. |
| O13 | Which GPU on Brev for a self-hosted demo? | Not needed for version 1: the models are hosted endpoints, so the VM needs no GPU. For a self-hosted demo: Omni needs 80 GB (one A100, about USD 1.66 to 1.98 per hour). The memory of Parse and OCR v2 is not in the NVIDIA docs: measure it. |
| O14 | What is the date of the event? | Needed to plan the dry run. |
| O15 | What is the rate limit of the free NVIDIA developer key? | Measure it in the first intern task. |
