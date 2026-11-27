# Reader safeguards (issue #125): method and results in detail

The short result is in [`EVALUATION.md`](EVALUATION.md), section "Reader safeguards (issue #125)". This file holds the problem, the design, the measures, every number of the offline replays and of the live run, the checks and the limits.

Commits on `nikola-work`: `055a516` (N1 data set and replay tests), `ec056ea` (the safeguards), `984c5e4` (the safety measure of the harness). Live run: 2026-11-25, code `984c5e4`.

## 1. The problem

The safety check N1 of issue #20 (2026-11-19) found that the Omni reader stores a value in the wrong field at confidence 0.9. The reader checked that the snippet is on the page and holds the value. It did not check that the text belongs to the field. On the 6 text pages of N1, `v1` and `schema-only` both accepted the same 4 values in each of 3 runs:

| Document | Field | Value stored at 0.9 | What it is |
|---|---|---|---|
| n2 medical invoice | `invoice_number` | P-60417 | the patient number ("Patient no.:") |
| n3 repair quote | `quote_date` | 2026-12-15 | the end of validity ("Valid until:") |
| n4 booking confirmation | `booking_reference` | sent separately by e-mail | a sentence under the right label |
| n6 medical report | `provider` | Dr. med. Corina Tschudi | a doctor, not the clinic (a letterhead, no label) |

The routing sends a field to a person only below 0.8 (`routing.py`, `LOW_CONFIDENCE`). So these claims could be paid with no person. The same check also found placeholder texts ("Not answerable", "null") stored as values on scans and photos.

## 2. What changed in the reader

All the logic is in one new module, `backend/app/docintel/labels.py`, with a small interface: `is_placeholder(value)` and `verified(document_type, name, value, snippet, text)`. `omni.py` calls it at two places:

- `extract()`: a placeholder value is skipped like `null`, **before** the comparison of pages (`first_seen`), so it is never stored and never makes a disagreement with a real value of another page.
- `_field()`: on a page with a text layer, after the existing checks of the source pass, the field gets `VERIFIED` (0.9) only when `verified()` is true; else `UNVERIFIED` (0.5). **The value and its source (document, page, snippet) stay as they are.** Nothing is dropped. A scan or a photo (no text layer) keeps 0.5, as before.

No prompt, setting or request byte changed: a test pins the request bodies of #18 (`test_omni_settings.py`), and the config id of `v1` is still `b0f2e2ccab2b`.

### The rules of `verified`

| Kind | Fields | Rule |
|---|---|---|
| identifier | invoice, quote, report number, booking, report reference | the label before the value must name the field, and the value must be an identifier: not prose ("sent separately by e-mail"), at least 2 letters or digits. Identifiers with no digit ("XKQZPL") and unusual formats are allowed. |
| date | every date field | the label must name the field. A bare "Date" counts only for the date of the document itself (invoice, quote, purchase, cancellation); an event date needs an event word ("Departure", "examined on", "Discovered"). |
| amount | total, trip price, amount paid, refund | the label must name the field; "Subtotal", "VAT", "Deposit", "Refund" (for the amount paid), "due" and others veto it. |
| organisation | `provider` only | the value must not read as one person: a title ("Dr. med.") with 1 to 4 names and no word of an organisation gives 0.5. "Praxis Dr. med. A. Muster" and "Dr. Muster AG" stay at 0.9. |
| person | patient, customer, traveller | the value must not name a company (a legal form or a word of an organisation). |
| out of scope | `cause`, `diagnosis`, `offence`, descriptions, `repair_company`, `merchant`, `airline`, line items | not checked by meaning: never downgraded here. |

**Where the label comes from.** At each place where the snippet stands on the page, the label is the text just before the value: inside the snippet when the model copied it ("Invoice no.: 4471"), else on the same line, else on the line above. A label elsewhere on the page does not count. Each place gives a verdict: match (the label names the field), veto (it names something else, for example "Patient no.", "Valid until", "Customer no.") or none. The field is verified only with at least one match and no veto. The words are general business vocabulary in English, German, French and Italian, with accents and case ignored, never the words of one document.

**One narrow fallback (approved 2026-11-24).** When the snippet itself gives no label, the other places of the same value on the same page decide, but only with clear evidence:
- every place of the snippet has a neutral label: none, a place and a comma ("Thalwil, 2026-10-25"), or the generic word "Date", wherever it stands on the line;
- at least one place of the value has the label of the field;
- every other place has the label of the field or a neutral one: a veto or a label of another field anywhere stops it.

The stored source stays the snippet of the model. Why: on the kit, the model gives the date of a medical report as the signature line ("Thalwil, 2026-10-25") while the page also says "examined on 2026-10-25". Without the fallback, 3 correct kit dates lost 0.9 in every run.

**"Reported".** A bare "Reported:" is the incident date only on a luggage report (the loss is reported on arrival; the schema `LuggageReport` has `incident_date`, "the date when the event of the report happened"), and only when the page names no incident date of its own ("Date of loss:").

**Placeholders.** A whole value such as "Not answerable", "null", "n/a", "unknown", "k.A.", "inconnu" or "sconosciuto" (about 70 texts in 4 languages, case, spaces and outer punctuation ignored) is no value. A text that only contains such a word is a value. Numbers, dates and 0.0 are never placeholders.

**The confidence is a flag, not a probability.** 0.9 means "the page shows that this value belongs to this field"; 0.5 means "a person checks it". Neither number is calibrated; only the side of 0.8 changes an outcome.

## 3. The safety measure of the harness

`backend/app/docintel/evaluate.py` now records the confidence of each scored field and of each extra field, and counts, per field of the answer key:

- `incorrect_trusted`: incorrect values stored at `LOW_CONFIDENCE` (0.8, imported from `routing.py`) or more, split into `_absent` (a value where the page holds none: the key says null) and `_present` (a wrong value of a field the page holds);
- the incorrect values at any confidence (`incorrect_absent`, `incorrect_present`);
- `right_present` (a right value) apart from `right_absent` (a right absence: nothing stored where nothing is printed);
- `right_trusted` (right values at 0.8 or more) and `extra_trusted` (fields not in the answer key, counted per stored value: nobody can say if they are right).

A field given twice counts once; a wrong copy gives its own confidence. A stored value with no confidence (a report of #19 or #20) makes the safety counts `None`, shown as "not available", never 0; a field with no stored value needs none. The counts flow into every table of the report, the run summary, the checks of a saved report and `--combine`. N1 is evaluated apart from the kit with `--set n1` (report `latest-n1.json`; the kit report and `latest.json` stay as they were).

**Accuracy and safety are two measures.** Accuracy counts right values over expected fields. Safety counts wrong values that no person would check. A reader can keep its accuracy and become safer: that is the result below.

## 4. Offline replay of the historical answers

Before any new request, the saved answers of #20 were read again through the reader with and without the safeguards (switched off, the reader gives exactly the Phase 1 baseline of each of the 12 runs; the comparison covers every historical field of the reports, and the new keys are listed apart). Same answers, no model call:

| Set (3 runs, pooled) | Incorrect at >= 0.8 | Incorrect, any confidence | Right | Right at >= 0.8 | Placeholders stored |
|---|---|---|---|---|---|
| kit S7 (2026-11-20) | 18 -> 12 | 24 -> 24 | 344 -> 344 | 239 -> 239 | 0 -> 0 |
| kit S3 control (2026-11-19) | 18 -> 12 | 24 -> 24 | 342 -> 342 | 234 -> 234 | 0 -> 0 |
| N1 `v1` (2026-11-19) | 12 -> 0 | 18 -> 16 | 97 -> 99 | 46 -> 46 | 2 -> 0 |
| N1 `schema-only` (2026-11-19) | 12 -> 0 | 27 -> 22 | 89 -> 94 | 47 -> 47 | 5 -> 0 |

43 fields changed: 36 incorrect values from 0.9 to 0.5 (the 4 N1 lures in 6 runs, and the 2 doctors stored as provider in the 6 kit runs) and 7 placeholders no longer stored. 0 correct values were lost or downgraded; 0 values, sources or pages changed; the request hashes of every kit call are the saved ones.

## 5. Live validation (2026-11-25)

### Method (written before any request)

The method, the runner and the replay were fixed and hashed before the first request (`METHOD.md` in the local folder of the run, see section 8).

- **One collection, two readers.** The model is asked once per call, with the reader of #125, and every raw answer is kept. Then each run is read again from these answers twice: by the reader of #125 ("after") and by the reader of #20 ("before": `omni.py` of commit `0334e55`, loaded from git; `schemas.py`, `models.py` and the workflows are unchanged since that commit). The replay transport fails on any request whose hash is not the saved one, so both readers sent the same request bytes; the "after" replay must equal the live report.
- **Why this isolates the code.** Before and after use the same answers of the same model on the same day. The only difference is the code of #125, so model drift cannot enter the comparison. The 3 runs show the variance of the model on that day.
- **Plan, in a rotated order:** round 1 kit `v1`, N1 `v1`, N1 `described`, N1 `example`; round 2 kit `v1`, N1 `described`, N1 `example`, N1 `v1`; round 3 kit `v1`, N1 `example`, N1 `v1`, N1 `described`. All other settings are those of #18.
- **Requests:** planned 360 (kit 3 x 66, N1 9 x 18: one classify and one page call per document); hard cap 400 HTTP attempts, counted before each attempt (retries and failures included). Stops: the cap, 5 calls in a row with no 200, any error, a credential in a written file.
- **Validity:** a run counts only when it is complete, every call ends with a 200, every page answer has `finish_reason` `stop` and the harness reports no reader error. Otherwise the verdict is INCOMPLETE.

### Requests

360 HTTP attempts (186 classify, 174 page calls), 0 retries, 0 failed attempts, all 360 answers 200 with `finish_reason` `stop`. 12 of 12 runs complete and valid. From 07:23:50 to 07:36:23 UTC. Model `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`, the model of #20.

### Results per run (before -> after, the same answers)

| Run | Incorrect at >= 0.8 | Incorrect, any confidence | Right | Missing | Right at >= 0.8 | Right absences (of 16) |
|---|---|---|---|---|---|---|
| kit `v1` r1 | 6 -> 4 | 8 -> 8 | 114 -> 114 | 10 | 79 -> 79 | - |
| kit `v1` r2 | 6 -> 4 | 8 -> 8 | 113 -> 113 | 11 | 78 -> 78 | - |
| kit `v1` r3 | 6 -> 4 | 8 -> 8 | 114 -> 114 | 10 | 79 -> 79 | - |
| N1 `v1` r1 | 4 -> 0 | 6 -> 5 | 32 -> 33 | 1 | 15 -> 15 | 10 -> 11 |
| N1 `v1` r2 | 4 -> 0 | 7 -> 6 | 31 -> 32 | 1 | 15 -> 15 | 9 -> 10 |
| N1 `v1` r3 | 4 -> 0 | 6 -> 5 | 32 -> 33 | 1 | 15 -> 15 | 10 -> 11 |
| N1 `described` r1 | 4 -> 0 | 8 -> 7 | 31 -> 32 | 0 | 16 -> 16 | 8 -> 9 |
| N1 `described` r2 | 4 -> 0 | 6 -> 5 | 33 -> 34 | 0 | 16 -> 16 | 10 -> 11 |
| N1 `described` r3 | 3 -> 0 | 7 -> 6 | 32 -> 33 | 0 | 16 -> 16 | 9 -> 10 |
| N1 `example` r1 | 4 -> 0 | 6 -> 5 | 33 -> 34 | 0 | 16 -> 16 | 10 -> 11 |
| N1 `example` r2 | 4 -> 0 | 7 -> 5 | 31 -> 33 | 1 | 15 -> 15 | 9 -> 11 |
| N1 `example` r3 | 4 -> 0 | 6 -> 5 | 33 -> 34 | 0 | 16 -> 16 | 10 -> 11 |

Kit: 132 expected fields per run, 0 absent. N1: 39 expected fields per run, 16 of them absent; every incorrect N1 value is in an absent field.

### Totals

| Set (3 runs) | Incorrect at >= 0.8 | Incorrect, any confidence | Right | Right at >= 0.8 | Placeholders stored | Right fields lost / downgraded |
|---|---|---|---|---|---|---|
| kit `v1` | **18 -> 12** | 24 -> 24 | 341 -> 341 (of 396) | 236 -> 236 | 0 -> 0 | **0 / 0** |
| N1 `v1` | **12 -> 0** | 19 -> 16 (of 48 absent) | 95 -> 98 | 45 -> 45 | 3 -> 0 | 0 / 0 |
| N1 `described` | **11 -> 0** | 21 -> 18 | 96 -> 99 | 48 -> 48 | 4 -> 0 | 0 / 0 |
| N1 `example` | **12 -> 0** | 19 -> 15 | 97 -> 101 | 47 -> 47 | 3 -> 0 | 0 / 0 |
| **N1, all prompts** | **35 -> 0** | 59 -> 49 | 288 -> 298 | 140 -> 140 | 10 -> 0 | 0 / 0 |

### Every changed field (51; no value, source or page changed)

| Change | Count |
|---|---|
| N1 n2 `invoice_number` "P-60417", 0.9 -> 0.5 | 9 runs |
| N1 n3 `quote_date` 2026-12-15, 0.9 -> 0.5 | 9 runs |
| N1 n4 `booking_reference` "sent separately by e-mail", 0.9 -> 0.5 | 8 runs (the model gave no value in `described` r3) |
| N1 n6 `provider` "Dr. med. Corina Tschudi", 0.9 -> 0.5 | 9 runs |
| kit claim-3-health medical report, `provider` "Dr. med. R. Haller", 0.9 -> 0.5 | 3 runs |
| kit kit-lukas-clean medical report, `provider` "Dr. med. N. Frei", 0.9 -> 0.5 | 3 runs |
| N1 n7 `booking_reference` "Not answerable" (0.5), no longer stored | 4 runs (`described` x3, `example` r2) |
| N1 n8 `customer_name` "Not answerable" (0.5), no longer stored | 3 runs (`v1`) |
| N1 n8 `customer_name` "null" (0.5), no longer stored | 3 runs (`example`) |

### Incorrect values that remain (after)

- **Kit, 8 per run:** 4 at 0.9, all out of scope (category fields): `cause` "gastroenteritis with fever" (key "illness"), `diagnosis` x2 (the full text with findings), `offence` "Burglary (forced entry)" (key "burglary"); 2 at 0.5 on scans (the same `cause` and `offence`); 2 at 0.5 in scope (the doctors stored as provider).
- **N1, all at 0.5:** the 4 lures and n8 `invoice_number` "J-88213" (a photo) in every run where the model gave them; n9 `amount_paid` 0.0 (a scan, `v1` r2); n7 `departure_date` 2022-07-01 and `trip_price` 1500.0 (a scan, `described` r1 and r3).

### The predefined criteria

| # | Criterion | Result |
|---|---|---|
| 1 | all 12 runs complete and valid | PASS |
| 2 | within the cap (360 of 400) | PASS |
| 3 | kit 35 documents, N1 9 | PASS |
| 4 | both readers sent the saved request hash for every call | PASS |
| 5 | the replay with the reader of #125 equals the live report | PASS |
| 6 | **N1 `v1`: 0 incorrect values at >= 0.8 in each run** | **PASS** |
| 7 | no placeholder stored | PASS |
| 8 | kit: no field right before is wrong or missing after | PASS |
| 9 | kit: no field right at >= 0.8 before is below 0.8 after | PASS |

### Against #20

| | #20 | Live 2026-11-25 (before -> after) |
|---|---|---|
| kit right / wrong / missing per run | S7: 115/8/9, 114/8/10, 115/8/9 | 114/8/10, 113/8/11, 114/8/10 (the same before and after) |
| kit incorrect at >= 0.8 per run | 6 | 6 -> 4 |
| N1 `v1` incorrect at >= 0.8 per run | 4 | 4 -> 0 |
| N1 `v1` stored incorrect values, of 48 | 18 | 19 -> 16 |
| N1 `described`, `example` | never run on N1 | 4, 4, 3 -> 0 and 4, 4, 4 -> 0 |

The kit gives about 1 right field less per run than S7, within the drift rule of S7 (2 fields). It is the same before and after, so it comes from the model, not from #125.

## 6. Tests and checks

- **Full offline suite** (Windows laptop, 2026-11-25, `pytest -m "not live and not sandbox"`): 4,853 passed, 31 xfailed (the known limits below), 12 deselected (live and sandbox tests), 7 failed. The 7 failures are the known Windows-only ones, also on unchanged `upstream/main`: `test_document_kit` 1 and `test_mock_documents` 2 (CRLF line ends and path separators), `test_sandbox_agent` 1 and `test_sandbox_image` 3 (POSIX file modes). They do not happen on Linux CI. mypy, ruff and ruff format are clean.
- **N1 in CI:** the 9 documents, their answer keys and their generator are in git (`data/mock/n1/`, `scripts/mockdocs/n1.py`), apart from the kit (`demo.NOT_KIT`; the kit stays at 35). `test_n1_replay.py` reads the saved model answers of 2026-11-19 through a stub of the model: no incorrect value at 0.8 or more and no placeholder, for `v1` and `schema-only`. `test_evaluation_safety.py` checks the same numbers through the harness, before and after.
- **Sweep** (`test_label_sweep.py`): every value printed on every text page of the kit and N1, as every field of its type, with 5 snippet shapes, and the real snippets of the S7 runs: 0 false downgrades of right values.
- **Independent test sets.** A synthetic set (2,287 cases, written from the specification only) has 31 known limits, all strict `xfail`, and all conservative: a right value gets 0.5, never a wrong value 0.9. A held-out set (551 cases) passed 548 in its single blind run: one real false accept ("Date reported" taken as the incident date, 2 tests, since fixed) and one bug in the test itself. One row of it was changed after the run, with the reason in the file.
- **Reviews:** an independent review of each phase; every finding was fixed with a test or documented.

## 7. Limits

- **Not universal safety.** The result holds for these documents, this model and these answers.
- **N1 is a development set, not a held-out set.** The safeguards were designed on the N1 answers of 2026-11-19. The live answers are new, but they hit the same 4 lures on the same 9 documents; `described` and `example` are new prompts, not new documents.
- **The paired replay** measures the effect of the code on the same answers. It does not measure how the model behaves on other documents or other days. **Paul confirms** whether it meets the "control of the same day" of the issue, and the "before" reader (the `omni.py` of `0334e55` with the current, unchanged schemas and models).
- **Incorrect values stay stored, at 0.5.** A person checks them; nothing is rejected (non-negotiable 1).
- **4 category errors per kit run stay at 0.9** (`cause`, `diagnosis` x2, `offence`): free-text fields, out of the scope of #125.
- **Scans and photos** have no text layer: no label can be checked there, so their fields stay at 0.5.
- **The confidence values are flags, not calibrated probabilities.**
- **Conservative heuristics:** an unknown label wording ("Fatt. n.", a bare "Price"), a table whose labels are column headers, or a practice named only after its doctor gives 0.5 to a right value: a person checks it. A bare person name with no title as provider stays at 0.9.
- **The labels are read from the text order of pypdf, not from positions.** A label in another column or far from its value is not seen.

## 8. How to reproduce

- **In git (CI, no key):** `cd backend && uv run pytest tests/test_n1_replay.py tests/test_evaluation_safety.py tests/test_labels.py tests/test_label_sweep.py`. These replay the saved N1 answers of 2026-11-19 and check every rule.
- **A new live measure with the harness (needs `NVIDIA_KEY`):** `cd backend && uv run --env-file ../.env python -m app.docintel.evaluate --reader omni --runs 3 --set n1 --omni prompt=v1` (and `prompt=described`, `prompt=example`; without `--set` for the kit). This gives the "after" numbers with the safety measure. It does not give the paired "before".
- **The paired run of section 5** used a local runner (`run_live.py`, `replay_live.py`, `lib125.py`, `METHOD.md`) and kept the raw answers. They are in `backend/.dev-data/issue-125/live/` on Nikola's machine, not in git, like the runners of #20. Commands: `run_live.py offline --out <name>` (a stub, no request), `uv run --env-file ../.env python -B .dev-data/issue-125/live/run_live.py live --out <name> --approved "<who>-<date>"`, then `replay_live.py <name>`.
- **A new run gives close numbers, not equal ones:** the hosted model changes with time.

## 9. Future work

- **Spatial association of label and value.** Read the positions of the words from the PDF text layer (pypdfium2 or pypdf give boxes) and tie a value to the label to its left or above it in the same column. That would handle tables with column headers and layouts with two columns, which the text order cannot. Not built in #125.
- **A held-out document set** with new layouts and new lures, made before any rule sees it, for a claim beyond the 9 N1 documents.
- **The category fields** (`cause`, `diagnosis`, `offence`) need their own check: they are the remaining values at 0.9 on the kit.
