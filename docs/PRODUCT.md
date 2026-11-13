# Product vision

Written on 2026-11-12 from Paul's brief (`docs/BRIEF.md`), an interview and a grill session. The decisions are in `docs/DECISIONS.md`.

## Problem

Today a claims handler reads each PDF, scan and photo of a claim file and types the key facts by hand. This is slow and it costs a lot. Fraud costs more: a handler has little time to compare documents and see what does not fit.

We want the machine to read the claim file, check it and look for fraud. The handler only reviews what the machine is not sure about.

## Users

- **Customer** (the insured): opens a claim, answers the questions of the claim agent and uploads documents.
- **Claims handler**: reviews the claims the system did not pass alone. Sees each field next to its source page, and the fraud report.
- **Demo visitor** (NVIDIA, the event): plays the customer. Picks a mock account, tells a story, uploads documents from our kit of mock documents, and watches the two panels side by side.
- **Intern**: builds the document reading pipeline on top of the base (`docs/BACKLOG.md`, M4).

## The two panels

The UI is one screen with two panels.

**Left: customer panel.** A chat with the claim agent, and nothing else. The agent handles the procedure: it finds the policy, asks what happened, asks for the documents, and tells the customer the result. The customer sees no model name, no score, no flag. The panel says: "Demo. Do not upload a real document."

**Right: pipeline panel.** It shows the plumbing live:

- the agent in its OpenShell sandbox, with the policy that confines it;
- each tool call of the agent: document classification, extraction, checks, fraud assessment, routing, with inputs, outputs, model and duration;
- the workflow the claim is in, and the step it is at;
- the extracted claim data, each field with its source page;
- the checks, the fraud signals and the fraud report.

Later the right panel also becomes the handler review screen (confirm or correct each field).

## The claim flow: type first, then risk

1. **Login (demo).** The customer picks one mock customer account.
2. **Policy.** The agent lists the policies of the account. The customer types a number or searches, and picks one.
3. **Story.** The agent asks what happened, when, and the amount claimed.
4. **Type.** The line of business of the policy sets the workflow:
   - **A: Property / household**
   - **B: Health / accident**
   - **C: Travel**
5. **Documents.** The agent asks for the documents the workflow requires. The customer uploads PDFs, scans and photos.
6. **Reading.** For each document: classification, then one vision-language model (Nemotron 3 Nano Omni) looks at the page image and fills the claim schema in JSON. Every field points to its source page. Specialised models (Parse, OCR, tables) come later, where the measures show that the model alone fails.
7. **Checks and fraud signals** (shared by the three workflows): rule-based red flags, document tampering, cross-document consistency. Details in `docs/FRAUD.md`.
8. **Risk routing.** The claim gets a score with reasons, then one of three outcomes:
   - `straight_through`: validated, goes to the claims system;
   - `handler_review`: a handler confirms or corrects;
   - `investigation`: strong fraud signals. The system writes a fraud report.
9. **Answer to the customer.** For `handler_review` and for `investigation` the customer sees the same exact message: "A claims specialist will review your case and contact you." A different message would tell a fraudster that the system flagged the claim. The pipeline panel shows the fraud flag and the report.
10. **Export.** Validated data goes to the claims system (a mock in the demo).

**The system never rejects a claim.** A person decides every refusal.

## What we measure

Accuracy per field against what a handler would type, the share of claims that pass without correction, processing time per claim, GPU usage per claim, and the share of seeded fraud cases the system flags. These numbers are the core of the demo and what we promised to share with NVIDIA.

## Why the NVIDIA stack

The models are open-weight, so a company can host all of them itself. Claim data includes personal and medical information, so in production it must never leave the company. It is the same principle as NemoForge.

Version 1 holds mock data only, so it calls the hosted NVIDIA endpoints with a free developer key. The platform and the sandbox run on a Brev VM. Self-hosted models on a GPU come when we have the GPU budget; the measures of version 1 support that request.

One option for later: run the claims pipeline as a NemoForge workflow. Approvals, audit and guardrails then come from the platform. Not discussed yet.

## Done when this demo works

On the demo instance, with the three mock claim files of `docs/MOCK-DATA.md`:

1. **Travel, clean.** Anna cancels a trip because she is ill. She uploads three documents. All fields are right, no flag. Outcome: `straight_through`. The customer panel confirms it.
2. **Property, review.** Marco has water damage. The invoice is clean but the amount is above the straight-through limit. Outcome: `handler_review`. The pipeline panel shows why.
3. **Health, fraud.** Lukas claims a medical invoice. The policy started five days before, the invoice total was changed, the PDF was edited, and the patient name is not his. Outcome: `investigation`. The customer panel shows the same message as in case 2. The pipeline panel shows a fraud report with every reason and its evidence.
4. The pipeline panel shows every agent tool call live for the three cases, and the sandbox policy of the agent.
5. A visitor can run a claim alone with the kit of mock documents, and the reset deletes what he uploaded.
6. The metrics page shows accuracy per field, pass rate, time per claim and GPU usage per claim on the mock test set. Not built: it is a task of the intern (M4, issue #19).

## Not in the first version

Real customer accounts, real claim data, real documents of visitors, the specialised extraction chain, self-hosted models, confirm and correct by the handler, payment, a real claims system, languages other than English, duplicate detection across claims, photo forensics.
