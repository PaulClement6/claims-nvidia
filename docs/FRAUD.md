# Checks, fraud signals, routing

## Principles

- A model finds facts. Code gives the points and decides the outcome.
- Each signal has evidence: the documents and pages it comes from.
- The system never rejects a claim. The strongest outcome is `investigation`.
- The customer never sees a signal, a score or the report. The customer message is the same for `handler_review` and `investigation`.

## Signals of the first version

| Code | Category | Signal | Severity | Points |
|---|---|---|---|---|
| R001 | rule | A required document is missing | medium | 25 |
| R002 | rule | Incident date outside the policy period | high | 40 |
| R003 | rule | Incident within 14 days of the policy start | medium | 20 |
| R004 | rule | Claimed amount at 95% or more of the coverage limit | low | 10 |
| R005 | rule | Invoice dated before the incident | medium | 20 |
| T001 | tampering | PDF last written by an editing tool | medium | 20 |
| T002 | tampering | PDF modified more than one day after its creation | low | 10 |
| T003 | tampering | Line items do not add up to the total | high | 30 |
| C001 | consistency | Name on a document is not the insured person | medium | 20 |
| C002 | consistency | Dates of the documents do not fit the declared incident | medium | 15 |
| C003 | consistency | Claimed amount differs from the documented amount | medium | 15 |
| C004 | consistency | Photo or report does not fit the declared damage (VLM) | medium | 20 |

The points are a first guess. Tune them on the mock test set.

## Routing

1. Score = sum of the points.
2. `investigation` if the score is 60 or more.
3. `handler_review` if the score is 20 or more, or a required document is missing, or the claimed amount is above the straight-through limit of the workflow, or a field has low confidence.
4. `straight_through` in all other cases.

## Fraud report

Written for every `investigation`. The handler must understand it in one minute.

- Outcome and score.
- One line that says why, in plain words.
- Each signal: what the system saw, where (document and page), and what it expected.
- What the system did not check.
- Suggested next steps for the investigator.

A template writes the text from the signals, in every mode. It adds no fact. Later (issue #26) an LLM can write it from the signals only, and it must add no fact.

## Later (not in the first version)

Duplicate invoices and photos across claims, photo forensics (edited or reused photos, EXIF data), font and pixel analysis of scans, links between claims (same provider, same bank account).
