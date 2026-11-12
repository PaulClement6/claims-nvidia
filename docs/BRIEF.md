# Paul's first brief (2026-11-12)

Kept as the source. `docs/PRODUCT.md` is the current version.

AI in Claims turns the documents in a claim file into checked, structured data that the claims system can use. Today a handler reads the PDFs, scans and photos and types the key facts in by hand. We want the machine to do that reading and the handler to only check it, then show it working on real claim files at the Q1 2027 event.

## How a claim file goes through it

1. **Intake.** A claim file comes in: a claim form, invoices, repair quotes, police or medical reports, ID documents and damage photos. Many are scans or phone photos, not clean PDFs.
2. **Reading the page (NeMo Retriever extraction).** Each page is split into parts:
   - Nemotron OCR v2 reads the text, including scans.
   - The page-elements model finds the titles, paragraphs, tables, charts and stamps.
   - The table-structure model rebuilds tables row by row, which matters for invoices and quotes.
   - Nemotron Parse handles complex layouts and returns the page in reading order.
3. **Understanding the content (Nemotron VLM).** A vision-language model (Nemotron Nano 12B v2 VL or Nano Omni) looks at the page image and the extracted text together. It answers claim questions: which policy, which date and place, which damage, which amounts, who is involved. It can also describe damage in photos.
4. **Structured output.** The model fills a fixed claim schema in JSON: policy number, claimant, incident date, cause, items, amounts and documents present. Each field points to the page it came from.
5. **Checks.** Rules then check the result: totals add up, dates fall inside the policy period, required documents are present, and the same invoice is not used twice. Anything uncertain or inconsistent goes to a handler for review.
6. **Into the claims system.** Validated data goes into the claims tool. The handler sees each field next to the source page and confirms or corrects it.

## What we measure

Accuracy per field against what a handler would type, the share of claims that pass without correction, processing time per claim and GPU usage per claim. These numbers are the core of the demo and what we promised to share with NVIDIA.

## What the extra GPU time is for

- **Benchmark Nemotron 3.5 Super VL (the 4× B200 burst).** We'll test whether the large model reads hard documents clearly better than the small one, which tells us whether the extra cost is worth it.
- **LoRA fine-tune (the 8× H100 burst).** We'll adapt the small VLM to our own claim documents and their wording, so it gets close to the large model at a fraction of the cost.
- **Batch extraction.** We'll process a large backlog of past claims to build the test set and show throughput.

## Why the NVIDIA stack

Everything runs self-hosted on open-weight models, so claim data, which includes personal and medical information, never leaves the company. It is the same principle as NemoForge.

One option for later is to run the claims pipeline as a NemoForge workflow. Approvals, audit and guardrails would then come for free. That idea is Paul's; not discussed yet.

## Added in the interview

- A platform where customers deposit their claims. Three workflows by claim type.
- Fraud detection matters most: fraud costs a lot in insurance.
- Two panels: the customer sees only the claim agent; the second panel shows the pipeline, the workflows and the tools.
- The agent is sandboxed in OpenShell with NemoClaw. It calls document intelligence tools (other models), workflows and tools to bring the claim to its end and detect fraud.
- It must be realistic and work rather well.
- An intern builds features on top of the base we define.
