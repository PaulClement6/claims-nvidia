# Reader Improvement Opportunities

Written 2026-11-27 by Nikola, for discussion with Paul. Nothing here is built, decided or approved. It lists what issues #20, #125 and #126 showed about the Omni reader, and what could come next.

Four marks: **defect** (reproduced by a test), **observed** (seen in saved model answers), **limit** (a known limit of the design or of the method), **idea** (not tested). The evidence is in [`EVALUATION-ISSUE-126.md`](EVALUATION-ISSUE-126.md) (R8 to R12), [`EVALUATION-ISSUE-125.md`](EVALUATION-ISSUE-125.md) and D80, D83, D84 of [`DECISIONS.md`](DECISIONS.md). The sets are small (35, 9 and 8 mock documents): each count rests on 1 to 5 documents.

## 1. Safety and reliability

**A document that gives no field is not sent to a person (defect, D80 (13)).** The low-confidence rule reads stored fields only, and a typed document with no field gives it nothing to read. When the settlement needs no amount from that document (a medical certificate, a medical report, a police report, a luggage report), the claim can be paid with no person. Offline tests pin this (the `_gap` tests of `backend/tests/test_scope_downstream.py`). It exists today for a one-page document; in the document scope one failed or cut call loses every page. No call failed in the 133 requests of #126, so its frequency is not known.

- *Idea:* a status on `Extraction` and `Document` (complete, partial, failed) that the routing reads as one more reason. The default value needs care: "complete" fails open, because it trusts a reader that does not set it. A simpler routing rule ("a typed document with no field") must leave out damage photos.
- This changes the base (models, tools, routing). I raised it in the text of pull request #121.

**Copied values can hide a conflict between pages (observed, document scope).** The model wrote most values into every page block. The reader marks a field when two pages give two values; a copy removes the second value. In 6 fields the page scope saw a conflict and the document scope did not. In 5 the copied value was right. In 1 a name with a date of birth was stored at 0.9 in each of the 3 runs; the page scope stores it at 0.5. Another field of that document (`incident_date`) is stored at 0.5, so a claim with it would still go to a person; the field `customer_name` alone gives no reason.

- *Idea:* compare each value with the snippet of its own block before the conflict rule uses it.

**Wrong values at 0.9 (observed).** Checked evidence shows that a text is on the page, not that it is the right field. On the kit, 4 category fields per run (`cause`, `diagnosis`, `offence`) keep 0.9 (#125). On MP1, "Total with options" passes the label check for `total`; it was stored at 0.5 only because of two other marks. *Idea:* a check for the category fields, and label rules for totals (section 2).

## 2. Field selection and merge logic

**The first value of a field wins (observed, page scope, D80 (12)).** When two pages give two values, the reader keeps the first and marks it 0.5. In 18 field-runs of the page scope on MP1, a later page answer held the right value and the reader kept an earlier one: a subtotal carried forward, a cost estimate, a date of report. All 18 are stored at 0.5, so a person checks them. In 17 of them the later value passed the checks of the reader by itself: the checks alone do not say which value to keep.

This does not show that the 18 cases can be recovered: no reader with another merge rule was run. It also does not explain the result of #126 alone: in the document scope the model saw all pages and chose the final value itself.

- *Idea:* choose between candidates by evidence. Prefer a value whose label names the field over a value whose label names something else ("Subtotal", "carried forward", "with options").
- *Idea:* tie a label to its value by the positions of the words, not by text order (future work of D83).
- **Proposed first step, with no model request:** a paired replay, as in #125. Read the saved answers of #20, #125 and #126 again with the candidate rule. Count right values gained, right values lost and wrong values at 0.8 or more. Then confirm on documents that the rule has never seen.

## 3. Line-item extraction

**A computed value empties the list (observed, both scopes).** On one quote the model returned the 5 right rows in 6 of 6 runs, and one optional row with a unit price that the page does not print (the amount divided by the quantity). One row is not confirmed, so the reader stores no row (D80 (6)): the 5 right rows were lost in every run.

- *Idea:* keep printed and derived values apart. A derived value is never stored as checked evidence. The reader could mark it as derived, or mark the row and keep the others with a flag that the list is not complete (`line_items_complete`; a change of the base contract, as D80 (6) says).

**"Brought forward" rows pass the line check (observed, both scopes).** The check accepts a row when each value is somewhere on the page. On one invoice of 3 pages, 15 wrong rows were stored in 6 runs. In 4 of the 6 lists the stored amounts do not add up to the total, which the sum check T003 of the claim flow can see (the evaluation does not run it). T003 cannot see a wrong description.

- *Idea:* check that the values of a row stand on one printed line, and refuse rows whose label is a carry-over or a subtotal.

**A row that the model leaves out is not seen (limit).** On MP1 no key row was missing from a stored list. *Idea:* count the table rows on the text layer.

## 4. Scans and provenance

**On a scan nothing is checked (limit, D80 (3)).** A scan has no text layer: every field gets 0.5, and no line item is stored.

**The document scope can store a wrong source page on a scan (observed).** The first block that gives a field decides its page, and the model copies values into the block of page 1. One right value was stored with a page that does not print it in the main run of #126 (3 of 3 runs), and one in the pilot. In the page scope the page is the page of the request.

- *Idea:* an OCR pass could give a scan a text layer, so that the checks above could run there; they would be only as reliable as the OCR. Issue #22 tests Nemotron Parse on scans and asks if Parse gives a position for each block, which could become the `box` of the source. Issue #24 (not yet cut into tickets) measures what OCR, page elements and table structure each add.

## 5. Evaluation and later experiments

- **A held-out set (limit).** N1 and MP1 are development sets, made with the generators of this project. A claim beyond them needs documents that no rule or prompt has seen.
- **Output limit (observed).** The largest document answer used 69 % of 2,048 tokens (12 line items, 2 pages). The reader allows 10 pages and the tests used 2 or 3, so a longer document can be cut: not tested. *Idea:* a limit that follows the number of pages.
- **A fallback to page calls after a failed document call (idea).** It does not close the gap of section 1 for a one-page document.

## 6. Priorities

| Improvement | Evidence | Proposed direction | Priority | Roadmap |
|---|---|---|---|---|
| A signal for a document that was not read | defect, D80 (13), offline tests | a status in the contract, or a routing reason | 1: before any change of the reader default | no issue; D80 (13) and the text of PR #121 |
| Evidence-based choice between page values | 18 field-runs on MP1 | offline paired replay, then a held-out set | 2 | none; a possible new issue |
| Scans: a text layer and positions | every scan field at 0.5 (by design); 2 wrong source pages, document scope only | Nemotron Parse or OCR | 2 | #22 (Parse, positions) and #24 measure this; no fix is planned |
| Line items: derived values, carry-over rows | 2 documents of MP1 | printed and derived apart; a row-level check | 2 | none; #24 (table structure) may touch it |
| Label tied to value by position | D83 | word positions of the text layer | 2 | none; future work in D83 |
| A held-out document set | N1 and MP1 are development sets | new layouts, frozen before use | 2 | none; #22 adds hard versions of known documents |
| Category fields | 4 values at 0.9 per kit run | allowed categories or a mapping | 3 | none |
| Copied values, output limit, fallback | 1 of 6 hidden conflicts ended wrong; 69 % of the limit used | section 1 and section 5 | 3: only if the document scope goes on | none |

The first row needs Paul's decision: it changes the base. The others are reader work, and each needs its own measure before it changes a default.
