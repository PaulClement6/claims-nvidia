# Issue #126: one call per page against one call per document

The short text is in [`EVALUATION.md`](EVALUATION.md), section "One call per page or per document (issue #126)". This file holds the method. The results come later, in new sections below the method. **The method was written and committed before any request of this experiment.**

## Method (written 2026-11-26, before any request)

No answer of the model to a document of the pilot or of MP1 existed when this text was written.

Code: commit `4a2be6f` on `nikola-work` is the last commit that changes a code file of the experiment. A live run needs a freeze file (`python -m app.docintel.experiment freeze`): it records the commit that is checked out, the SHA-256 of each frozen code file, of each file of MP1 and of this file. The runner refuses a live run when one of them differs, or when the checked-out commit is not the commit of the freeze. The freeze file is local (`backend/.dev-data/issue-126/live/freeze.json`).

Words: **page scope** = one model call per page (the reader of #18 to #125, the default, the **control**). **Document scope** = one model call per document with every page image (the **candidate**). **Logical call** = one call of the reader. **Attempt** = one HTTP request; a retry is a new attempt of the same logical call.

The method has five parts that must not be mixed:

- Part A: the official acceptance criteria (issue #20). Only they give the verdict.
- Part B: how four of these criteria map to the set MP1. The mapping is in force as written; Paul's confirmation is pending.
- Part C: supplementary analyses. They are reported and never change the verdict.
- Part D: safety findings that can block a change of the default. They do not change the verdict of the experiment.
- Part E: rules of integrity and reliability. They say if the experiment is valid at all.

### 1. Question and design

Question of issue #126: on multi-page documents, does one call per document read better than one call per page, by the rules written in advance in #20?

- Two arms, the same reader code, the same settings. Only `OmniSettings.scope` differs: `"page"` (config id `b0f2e2ccab2b`) or `"document"` (config id `54b875de9dfa`).
- Three runs per arm, six runs in **one invocation on one day**, in the order **page, document, document, page, page, document** (tags `r1-page`, `r2-document`, `r3-document`, `r4-page`, `r5-page`, `r6-document`).
- Each run reads the 8 documents of MP1 in the order of their file names, with a new reader and a new HTTP client. So each run classifies every document again.
- The model is asked once per call. Every answer is saved at once (`calls.jsonl`). A replay must give the same six reports with no request.
- **The production default does not change in this experiment.** `OmniSettings()` has `scope="page"`. A verdict "the candidate meets every official rule" is an input for a decision of Paul, not a change of the default.

### 2. Fixed settings (the settings of #18)

| Setting | Value |
|---|---|
| Prompt family | `v1` |
| Resolution | 150 DPI |
| Temperature | 0.2 |
| Reasoning | off (`chat_template_kwargs.enable_thinking: false`), no budget sent |
| Maximum output tokens | 2048 (`max_tokens`), per call, in both scopes |
| Timeout | 90 s (the timeout of the HTTP client, for each phase of an attempt) |
| Retries | 2 after a timeout, a 429 or a 5xx; waits 2 s then 8 s, or `Retry-After` up to 30 s. A 4xx other than 429 and a 200 with no content are final. |
| Structured output | `response_format` `json_schema`, `strict: true` |
| Pacing | none (`--pace` is not passed) |
| Fallback | none: no second call after a failed or unusable document call, no higher token limit |
| Model and endpoint | `VLM_MODEL` and `VLM_BASE_URL` of the environment. Expected: `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning` at `https://integrate.api.nvidia.com/v1` |

Fingerprints:

| Item | Value |
|---|---|
| Config id, page scope | `b0f2e2ccab2b` |
| Config id, document scope | `54b875de9dfa` |
| Prompt SHA-256, page scope | `e8096cae4ff9aa0d4a95bc1c00070f88d6b9a38753c384d1ed10abd6814e0d9a` |
| Prompt SHA-256, document scope | `0c6895e9c62f50c0156e8b717afc0bebe78d95d512c1ce1587c4af5b1331ebb2` |

The two extraction requests differ only by what the scope needs. A report must say "the same settings", never "the same request":

1. Several images in one request, with the text part `Page k of N:` before each image, in page order.
2. Another prompt text ("Read every image. They are the N pages of one <type>"; one entry per image; "that page" for "this page"). The rule sentences are those of `v1`, word for word.
3. Another answer schema: one block per page under `page_1` ... `page_n`.
4. One limit of 2048 output tokens and one timeout for the whole document.
5. The unit of failure is the document, not the page.
6. On a scan, the page of a field is a claim of the model.

The classify request is the same in both scopes. A document of one page takes the page request of #18 in both scopes (MP1 has none).

### 3. The set MP1 (frozen)

8 documents, 19 pages (14 with a text layer, 5 image-only), 6 text PDFs and 2 scans, 35 scored fields = 33 present + 2 absent, 12 fields with more than one accepted source page, 39 line items = 35 on text PDFs + 4 on a scan. Mock data only. Folder `data/mock/mp1/`.

SHA-256 of the 17 files:

```
c9807b3b02151650e4006aba1c74468e30c8c663f0319682a49db3c02bb85b84  manifest.json
3c2bf913c5a5fd5bc8653ab86f64635b159bed86e7ddec97fce70651866f5c50  mp1-01-repair-invoice-carried-forward.expected.json
1438b192851134bb0e520162b2c8a59413d8fd665bdc4bbf7119ec1737d00063  mp1-01-repair-invoice-carried-forward.pdf
5379e397d256e4460c09612d911f276cfccc7add82da0b0686c26e853dbed2e1  mp1-02-medical-invoice-continued.expected.json
cc65a004191657639521c3b8a21e687a62b38fc983959976a45c2ade8306d42a  mp1-02-medical-invoice-continued.pdf
31382d4054aea1fba4cda01dc16c0239e1eaaa45debddf1b7617e20d5c1a98c3  mp1-03-cancellation-confirmation-scan.expected.json
b54813db72d02981437064d6d1ad8b883c60da874cb68e7b3bc1f93bd3de6dec  mp1-03-cancellation-confirmation-scan.pdf
41c5e6b81fef11cc816d1b5342c0087d3d55d13bcd32f6e1fd656b1e1f475a7f  mp1-04-repair-quote-terms.expected.json
d1f76082e1804117e3c83770d7b8d11124e3d9377634f209c49df18194301002  mp1-04-repair-quote-terms.pdf
b93a60d2baa83d4c1c63dce8e9ab800e921243219f1f9c615a92db821eef3f7b  mp1-05-repair-invoice-service-rows.expected.json
3521ec73a7fe2d7ede13c39fe13d612555e0f5939a750dd73527580c75406a36  mp1-05-repair-invoice-service-rows.pdf
8ff6118d8aea726874eb49a8d6b8a0b55bddff625a1ef7d366243eaaf63dd968  mp1-06-medical-report-scan.expected.json
752739a243eafcbc6968b86091a484c21ef1ee5473a87bce9013d4462c8c5984  mp1-06-medical-report-scan.pdf
8f7ad32970d3dd7ea863b4f451bd5718b4004d6052946febebed50c3ad96c3cb  mp1-07-booking-confirmation-no-price.expected.json
8709d7f229b61055454d0744b39106a0148eb1954b6fb6bb2ea511c5f6f78e0d  mp1-07-booking-confirmation-no-price.pdf
5956fa6b949d7ac31bd73feef5c152bccb48f912d7adf57e2eebfa11ecdef335  mp1-08-police-report-two-dates.expected.json
5677ef9bcc7b7251ea6055470a82845e75e195bb92e5ea83b7ec1b9549417963  mp1-08-police-report-two-dates.pdf
```

The set does not change. No document of MP1 is sent before the main run. The pilot uses two other documents.

### 4. Requests and caps

| | Page scope, per run | Document scope, per run | Total |
|---|---|---|---|
| Planned logical calls (classify + extraction) | 8 + 19 = 27 | 8 + 8 = 16 | 3 x 27 + 3 x 16 = **129** |

- **Hard cap of the main run: 145 HTTP attempts.** Every attempt is counted before it is sent: classify, page, document, every retry, every failure. Attempt 146 is never sent.
- **Pilot: 4 planned logical calls, hard cap 8 HTTP attempts** (section 5).
- The caps are constants of the runner and part of the freeze. They do not change.

### Part A. Official acceptance criteria (issue #20, unchanged)

Source: [`EVALUATION.md`](EVALUATION.md), "How a variant was judged (the rules written before the runs)". The evaluator (`backend/app/docintel/acceptance.py`) computes each rule as the analysis script of #20 did. Means are exact fractions over the 3 runs of each arm. The words in quotes are the text of #20; the words after a quote say how it is computed.

The candidate replaces the control **only when all of these are true**:

1. "It gives at least 3 more right fields per run." Mean right fields of the candidate minus mean of the control >= **3**. A right field is a right value or a right absence (35 scored fields per run).
2. "Its lowest run is higher than the highest run of the control."
3. "The guardrails hold":
   - a. "no wrong amount, date or identifier": computed as 0 in the 3 runs of the candidate, at any confidence;
   - b. "no more wrong values than the control": the means;
   - c. "the reading group loses 1 right field per run at most";
   - d. "classification loses 1 document per run at most";
   - e. "every line item of the clean PDFs stays right";
   - f. "no reader error, and no call left without an answer";
   - g. "at most 1.5 times the seconds per document of the control, or Paul accepts the cost". The seconds of a document are the time of its classify and extract calls, the waits of retries included; the value of an arm is the mean of its 3 run means. The evaluator reports met or not met. When this rule is the only one not met, the computed decision is still "the control stays"; an acceptance of the cost by Paul is a separate written decision, reported next to the verdict.
4. "The gain stays above 0 when any one scenario is left out."

Also from #20:

- "No verdict when a run is incomplete, a call got no answer after the retries, or a check of integrity fails (code, kit, settings, model)."
- "A call answered after an allowed retry is a success, and the retry is reported."
- "When two variants win, the larger gain wins": not applicable, there is one candidate.
- When the candidate does not meet every rule, the control stays (the result of #20 itself: no variant won, and the settings of #18 stayed).

**The official threshold of rule 1 is 3 fields. It is not a question and it does not change.**

### Part B. Mappings to MP1 (in force; Paul's confirmation is pending)

Four rules of #20 name a count or a term of the 35-document kit, or need a definition for a document answer. The evaluator uses the mapping in the second column. The official verdict is stated under these mappings and is provisional until Paul confirms them. For each one, the one other reading that Paul could choose is written here now, before any answer exists.

| Rule | Mapping in force (what the evaluator computes) | Other reading, written in advance | Pending |
|---|---|---|---|
| 3e | All **35 line items of the 6 text PDFs** are right in **every** run of the candidate (the kit had 53). The 4 lines of the scan are not in the rule and are reported apart. | Relative: the lowest run of the candidate has no fewer right text lines than the highest run of the control. | P1 |
| 4 | MP1 has no scenario: the gain stays above 0 when **any one document** is left out. | none | P1 |
| 3c | The groups of #20, by document type and field: 31 reading fields and 4 business-category fields on MP1 (`medical_invoice.provider`, `medical_report.diagnosis`, `medical_report.provider`, `police_report.offence`); no damage photo. | none (it is the definition of #20) | with P1 |
| 3f | An **unusable answer** is an extraction answer with status 200 that is cut (finish reason `length`, also when its text is whole JSON), empty, not JSON, or not in the asked form. The asked form: a page answer, and each block of a document answer, is an object with the objects `values` and `snippets`; a document answer has the keys `page_1` to `page_N` and no other key. An unusable answer of the **candidate** counts as "a call left without an answer": **the run stays valid**, the numbers are reported, and the candidate cannot pass. An unusable answer of the **control** gives **no verdict** (rule 3f judges the candidate only, and such an answer would lower the score of the control). | The reading of #125: an unusable answer in either arm makes the run invalid, so there is no verdict. | P2 |

Rules for this part:

- P1 and P2 are open for the **main** run. They do not affect the pilot: the pilot gives no verdict.
- Under both readings of 3f an unusable answer never lets the candidate pass. The readings differ only between "the control stays" and "no verdict".
- A block that has the asked form and whose values fail the schema is not an unusable answer. The reader treats it as it treats a page answer of #18: it drops the failing keys, or it counts the page as not read. The fields then count as missing or stay below 0.8.
- The numbers that the other readings need are in `verdict.json` (the right text lines of each run of both arms; the unusable answers of each run). No new code is needed to state them.
- If Paul chooses another reading **before the first live answer of the pilot**, the code, this text and the freeze are made again, and the change is in git.
- **After the first live answer of the pilot, no acceptance criterion changes.** If Paul then chooses the other reading of a row above, the verdict of the mapping in force is still reported as the result of this method, and the other reading is reported next to it with the date of his decision. No reading that is not in this table can be added after a live answer.

### Part C. Supplementary analyses (reported, never the verdict)

None of these enters the decision. In the evaluator, the decision is made from the official rules only. None is a reason to change the default. Items 1 and 2 are in `verdict.json`. Items 3 to 6 are read after the verdict from the saved run reports and the record of the requests, by a script that does not write `verdict.json`.

1. **Thresholds of 1 and 2 fields** for rule 1: for each, whether rule 1 would be met and whether every rule would then be met.
2. **Five proposed safety gates** (from #125, not approved): (1) no candidate run has more wrong values at 0.8 or more than the highest control run; (2) no value at 0.8 or more for an absent field; (3) no value at 0.8 or more on a page that is not accepted; (4) the share of stored values on an accepted page is not below the control, text PDFs and scans apart; (5) no placeholder stored. Also reported: the fields right at 0.8 or more in every control run and not in every candidate run.
3. **Accepted source pages:** `accepted_page_accuracy`, `verified_accepted`, `pass_rate_accepted`, `wrong_page_trusted`, `unaccepted_page_trusted`, `right_untrusted`. The official source-page number stays the key page.
4. **Per document and per kind:** each number for the 6 text PDFs and the 2 scans apart; right fields per document in each run; on scans, the page that each arm gives for each field.
5. **Cut answers:** the right fields per run with and without the documents whose document answer has the finish reason `length`.
6. **Cost:** tokens (prompt, completion, cached, as the endpoint reports them), seconds per document (mean, median, lowest, highest), requests and retries.

A count that needs a confidence and has none is "not available". A token count that the endpoint did not report is missing. Neither is ever 0.

### Part D. Safety findings that can block a change of the default

These are not part of the comparison and do not change its verdict. A verdict that passes is necessary for a new default and not sufficient.

1. **A document with no field.** A document that gives no field looks like a document that prints nothing (D80 (13), reported in the text of pull request #121). When the settlement of the claim does not read an amount from that document (a medical certificate, a medical report, a police report, a luggage report), the claim can be paid with no person. It exists today in the page scope. The document scope makes it more likely and adds paths (a cut answer loses every page). Offline tests pin it: `backend/tests/test_scope_downstream.py`, the tests that end in `_gap`. It must be fixed in the base, or assessed in writing by Paul, before the document scope can be a default. A fix changes files that the freeze holds, so it lands after the main run, or the freeze is made again before the first live answer.
2. **Page claim on scans.** In the document scope the page of a field of a scan is a claim of the model (D80 (1) chose the page scope for this reason). Paul decides if this is acceptable for a default.

The evaluation reads documents only. It builds no claim and calls no routing, settlement or payment function. So these findings do not make the evaluation unsafe.

### Part E. Integrity and reliability rules

**Two separate answers.** Was the procedure valid? Did the candidate meet the rules? An invalid procedure gives "no verdict" and the candidate is not judged.

| Kind | Examples | Procedure | Candidate |
|---|---|---|---|
| Infrastructure | in either arm, a call with no 200 after the allowed retries (timeout, 429, 5xx) or a 4xx | **no verdict** | not judged |
| Integrity | the run stopped (cap, failing endpoint, crash, lock, a secret in a file); a run report is missing; the report and the record count other requests; the settings are not those of the scope; the six runs did not read the same documents and keys; another reader code within the invocation; the record file is cut; more attempts than the cap; an unusable extraction answer of the control | **no verdict** | not judged |
| Caused by the scope | an unusable extraction answer of the candidate (Part B, 3f) | valid | rule 3f not met: **the control stays** |

A classify answer with a 200 and no known type is a classification error (rule 3d). The reader then asks for the type once more before the extraction. The procedure stays valid.

**Cut answers.** The finish reason of every answer is recorded as the endpoint sent it, in `calls.jsonl` and in the counts of the report. An answer with the finish reason `length` is a cut answer **also when its text is whole JSON that the reader can parse**. In the document scope the reader then stores its fields below 0.8 and no line item. These fields are scored like any stored field, so they can be in the right fields of rule 1. The answer is still an unusable answer for rule 3f, so the candidate cannot pass with it. Part C (5) reports the numbers without these documents. In the page scope the reader does not read the finish reason; a cut page answer of the control gives no verdict (Part B, 3f).

**Stops.** The cap is reached. Five logical calls in a row got no 200: the next call is not sent. Any exception. A secret is found in a written file. A lock file exists. After a stop nothing is sent, and **nothing restarts by itself**.

**One invocation, one day.** The six runs are one invocation. Its start and end (`meta.json`, UTC) must have the same date; do not start within one hour before midnight UTC. An invocation that stopped is reported as incomplete. Runs of two invocations are never joined. A new invocation is all six runs, with a new authorization.

**The printed decision is provisional until a person checked these points.** The runner prints a decision and writes `verdict.json` at the end of the run. The result is **no verdict**, whatever `verdict.json` says, when one of these fails: the model and the endpoint in `meta.json` are those of section 2; the dates; `pace_seconds` is 0; `replay` gives the saved reports; the record holds no key. The person writes the check in the report.

**Secrets.** No header, key, image or prompt is written. A secret in an answer is replaced before it is written, and the output folder is scanned for the key at the end. The key enters only through the environment (`--env-file ../.env`); it is never an argument.

**Before a live run starts** (`preflight`; nothing is sent before it passes): the three environment variables of the reader; the authorization text of this mode, followed by a name and a date; the freeze file, with every hash equal (code, MP1, this file), the same commit, no uncommitted frozen file, the same plan, caps and order; a new output folder; no lock.

**Not in the freeze:** the versions of the Python packages. They are in `backend/uv.lock`, which is in the commit. The record holds the SHA-256 of every image sent, so another renderer would show.

### 5. The pilot

**Purpose:** to learn if the hosted endpoint takes the request of the document scope at all. It is technical. It gives no accuracy number and no verdict. It starts nothing: the main run is another command with another authorization, and the main run does not read the pilot.

**Documents:** two mock documents made by `scripts/mockdocs/pilot.py` into the output folder of the run. They are not in MP1, N1 or the kit.

| File | Type | Pages | Kind |
|---|---|---|---|
| `pilot-01-medical-invoice-two-pages.pdf` | medical_invoice | 2 | text PDF |
| `pilot-02-repair-quote-scan-three-pages.pdf` | repair_quote | 3 | scan |

**Requests, in this order** (one reader, `scope="document"`, the settings of section 2):

| # | Call | Images |
|---|---|---|
| 1 | classify document 1 (page 1) | 1 |
| 2 | document call, document 1 | 2, in page order |
| 3 | classify document 2 (page 1) | 1 |
| 4 | document call, document 2 | 3, in page order |

4 logical calls when every call gets a usable answer at the first attempt. **Never more than 8 HTTP attempts.** A retry, or a second classify call after a classify answer with no known type, uses the spare attempts. The cap stops the pilot before attempt 9.

**Technical checks** (per document; all must pass):

1. The classify call got a 200 (the last classify call for this document).
2. Exactly one logical document call holds every page image in page order (a retry is not a second call).
3. It got a 200.
4. The finish reason is `stop`.
5. Prompt and completion tokens are reported.
6. The prompt tokens are at least one token per block of 32 x 32 px of each image sent (2,145 for each A4 page at 150 DPI): no image was dropped. This is a lower bound.
7. The completion tokens are at most 1,536 (75 % of 2048).
8. The answer is JSON with exactly one block for each page.
9. Every block is a valid page answer for the type of the answer key (the validation of a page answer of #18).

**Reported, never a reason to pass or fail:** the type that the model gave and if it is right; the fields stored and how many are on an accepted page; the line items stored; cached prompt tokens; seconds.

**What follows from the result** (written in advance):

| Result | What follows |
|---|---|
| All checks pass | The request form is confirmed. The main run can be authorized, as frozen. |
| The document request is refused (4xx) | The pilot fails. Another request form is a code change: a new commit, a new freeze, a new authorization. |
| The finish reason is `length`, or more than 1,536 completion tokens | The pilot fails. `max_tokens` stays 2048. Nikola decides if the main run is still useful. |
| The prompt tokens are below the image tokens | The pilot fails: an image was dropped or made smaller. No main run until this is understood. |
| Another check fails (1, 5, 8 or 9) | The pilot fails. A person reads the record. No main run until the cause is understood. |
| A stop (cap, failing endpoint) | The pilot is incomplete. A new pilot needs a new authorization. |

**What the pilot never does:** it changes no prompt rule, no answer key, no acceptance criterion, no cap. Its answers are not used to judge quality and not used to tune anything.

### 6. What can change, and when

- After the freeze and before the first live answer: a change needs a new commit, a new freeze, and a note of the reason.
- After the first live answer of the pilot: only a change of the request form that the pilot proves necessary, with a new commit, a new freeze and a new pilot. No acceptance criterion, no MP1 file, no setting of section 2, no cap.
- After the first live answer on MP1: nothing.
- This file is part of the freeze. It is not edited between the freeze and the end of the main run. The results are added after it; the frozen text stays in the git history at the commit of the freeze.

### 7. Commands

All from `backend/`. The two live commands need the exact authorization text, with a name and a date after it. Do not pass `--pace`.

```bash
# offline, no request, no key
uv run python -m app.docintel.experiment dry-run pilot --out .dev-data/issue-126/dry/<name>
uv run python -m app.docintel.experiment dry-run main  --out .dev-data/issue-126/dry/<name>

# once, after the last commit: what a live run is held to
uv run python -m app.docintel.experiment freeze \
    --out .dev-data/issue-126/live/freeze.json --method ../docs/EVALUATION-ISSUE-126.md

# LIVE, the pilot: 4 planned calls, at most 8 attempts
uv run --env-file ../.env python -m app.docintel.experiment pilot \
    --out .dev-data/issue-126/live/pilot-1 \
    --freeze .dev-data/issue-126/live/freeze.json \
    --authorize "issue126-pilot-4-requests-cap-8 <who> <date>"

# LIVE, the main run: 129 planned calls, at most 145 attempts. A separate decision.
uv run --env-file ../.env python -m app.docintel.experiment main \
    --out .dev-data/issue-126/live/main-1 \
    --freeze .dev-data/issue-126/live/freeze.json \
    --authorize "issue126-main-129-requests-cap-145 <who> <date>"

# offline, after a main run
uv run python -m app.docintel.experiment replay  .dev-data/issue-126/live/main-1
uv run python -m app.docintel.experiment verdict .dev-data/issue-126/live/main-1
```

Exit codes: 0 done (for the pilot: every technical check passed; for the main run: the procedure is valid). 2: the preflight refused the run and nothing was sent. 3: the run stopped, the pilot checks did not all pass, or the files give no valid experiment.

---

## Results (written 2026-11-26, after the runs)

**Everything above this line is the method as it was frozen before the first request. It was not edited.** The frozen file is in git at commit `aa0820c`, with the SHA-256 `a6268f8f7e091b50a696842f5c4fcb0bb56036546102df647147c495da7f9d36`. To get it back: `git show aa0820c:docs/EVALUATION-ISSUE-126.md | sha256sum`. That commit is also the code that ran the pilot and the main run. Everything below this line was written after the answers.

Words used below: **control** = the page scope. **Candidate** = the document scope. **Trusted** = stored at confidence 0.8 or more: the routing finds no low-confidence reason in this field. **Field-run** = one field in one run. **Key page** = the page that the answer key names for a field. **Lure** = a value that a document prints on purpose where a reader can take it for another field (a subtotal carried forward, a cost estimate, a date of report).

### R1. Result

**The default stays one call per page.** The procedure was valid. The candidate stored more right fields in each of its runs, but it does not meet rules 1, 3a and 3e of #20. The verdict is provisional, because Paul has not confirmed the mappings of Part B. It is the same under every reading that Part B holds: rules 1 and 3a have no mapping, and each one alone gives "the control stays". Paul has not reviewed this result.

| | Control (page) | Candidate (document) |
|---|---|---|
| Right fields per run, of 35 | 25, 27, 24 (mean 25.33) | 28, 28, 28 |
| Gain | | +2.67 (rule 1 asks for 3) |
| Wrong values at 0.8 or more, per run | 0, 0, 0 | 1, 1, 1 |

### R2. What ran

| | Pilot | Main run |
|---|---|---|
| Date (UTC) | 2026-11-26, 15:40:13 to 15:40:39 | 2026-11-26, 15:49:09 to 15:57:02 |
| Documents | 2 mock documents outside MP1 (a text PDF of 2 pages, a scan of 3 pages) | the 8 documents of MP1 |
| Scope | document only | page, document, document, page, page, document |
| Planned calls / HTTP attempts / cap | 4 / 4 / 8 | 129 / 129 / 145 |
| Status 200 | 4 | 129 |
| Retries, errors, calls not sent | 0 | 0 |
| Finish reason | `stop` for all 4 | `stop` for all 129 |
| Authorization (Nikola) | `issue126-pilot-4-requests-cap-8 Nikola 2026-11-26` | `issue126-main-129-requests-cap-145 Nikola 2026-11-26` |

Both ran on commit `aa0820c`, with the freeze file whose SHA-256 is `894e11f2c7b25e027f5b89a51124e6ac9c63cf2d0b569553f80cfeb603b5df69`, the model `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning` and the endpoint `https://integrate.api.nvidia.com/v1`. Issue #126 sent 133 requests to the model in all: 4 and 129.

### R3. Integrity of the main run

The procedure is valid. No condition for "no verdict" of Part E is met.

- 129 planned calls, 129 attempts, each at its first attempt. Per run: 27, 16, 16, 27, 27, 16.
- No unusable answer by the definition of Part B (rule 3f): 0 of 57 page answers and 0 of 24 document answers. None is cut, empty, not JSON or in another form.
- Each request has the same hash in every run of its scope. The six reports name the same reader code, the same documents and the same answer keys.
- The checks that Part E asks for after the run: the model and the endpoint are those of section 2; start and end have the same UTC date; `pace_seconds` is 0; the output holds no key. They were made from the files on 2026-11-26 by Nikola's agent, which ran the experiment, and again by an independent automated reviewer. A person has not repeated them by hand.
- `experiment replay` reads the six runs again from the saved answers with no request: the same reports.
- The method was committed at 15:12:06 UTC, before the pilot (15:40) and the main run (15:49).

### R4. The pilot

The pilot passed: 9 of 9 technical checks for both documents.

| # | Call | Images | Seconds | Prompt tokens | Completion tokens |
|---|---|---|---|---|---|
| 1 | classify, text PDF | 1 | 1.10 | 2,242 | 11 |
| 2 | document call, text PDF | 2 | 6.54 | 4,933 | 696 |
| 3 | classify, scan | 1 | 2.66 | 2,242 | 11 |
| 4 | document call, scan | 3 | 12.49 | 7,099 | 936 |

- The endpoint accepted several images in one request, a text part before each image, and the keyed schema. Each image was counted at its full size (2,145 prompt tokens per A4 page). The largest request body was about 4.8 MB.
- Both answers had exactly the asked page blocks. This does not prove that the endpoint enforces the schema: the prompt asks for the same form.
- Text PDF: 4 of 4 present fields right at 0.9 on the right page, the absent field left empty, 5 of 5 line items.
- Scan: 5 of 5 values right, all at 0.5 by the rule for a scan, no line item stored by the rule for a scan. **One stored source page is wrong:** the quote date is stored with page 1 and only page 3 prints it.
- The pilot showed a behaviour that the plan did not expect: the model wrote every field value into every page block (R9).
- The pilot gives no accuracy number. Its answers were not used to change anything.

### R5. The six runs of the main run

| Measure | r1 page | r4 page | r5 page | Control: mean (range) | r2 doc | r3 doc | r6 doc | Candidate: mean (range) |
|---|---|---|---|---|---|---|---|---|
| Right fields, of 35 | 25 | 27 | 24 | 25.33 (24 to 27) | 28 | 28 | 28 | 28.00 (28) |
| Field accuracy | 71.4 % | 77.1 % | 68.6 % | 72.4 % (76/105) | 80.0 % | 80.0 % | 80.0 % | 80.0 % (84/105) |
| Right values, of 33 present | 24 | 26 | 23 | 24.33 | 27 | 27 | 27 | 27.00 |
| Right absences, of 2 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| Wrong values | 9 | 8 | 9 | 8.67 | 7 | 7 | 7 | 7.00 |
| Missing | 1 | 0 | 2 | 1.00 | 0 | 0 | 0 | 0 |
| Fields that the key does not name | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Wrong at 0.8 or more | 0 | 0 | 0 | 0 | 1 | 1 | 1 | 1 |
| Right present at 0.8 or more | 19 | 19 | 18 | 18.67 | 22 | 22 | 22 | 22.00 |
| Source page = key page, of stored values | 26/33 | 28/34 | 25/32 | 79.8 % (79/99) | 30/34 | 30/34 | 30/34 | 88.2 % (90/102) |
| Line items right, text PDFs, of 35 | 23 | 25 | 25 | 24.33 | 28 | 23 | 29 | 26.67 |
| Line items right, scan, of 4 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Type right, of 8 | 8 | 8 | 8 | 8 | 8 | 8 | 8 | 8 |
| Documents with every field right on its key page, of 8 | 0 | 2 | 0 | 0.67 | 4 | 4 | 4 | 4 |
| Seconds per document, mean | 10.58 | 11.18 | 9.57 | 10.44 | 9.69 | 8.82 | 9.35 | 9.29 |
| Prompt tokens | 68,573 | 68,573 | 68,573 | 68,573 | 63,296 | 63,296 | 63,296 | 63,296 |
| Completion tokens | 5,904 | 6,198 | 5,945 | 6,016 | 5,627 | 6,030 | 5,486 | 5,714 |
| Requests | 27 | 27 | 27 | 27 | 16 | 16 | 16 | 16 |

Denominators:

- A right field is a right value of a present field or a right absence. 35 fields per run = 33 present + 2 absent. A right absence is never counted as a right value.
- The source page is counted over the values that were stored, right or wrong: 33, 34 and 32 in the page runs, 34 in each document run.
- Line items are right by position: an extra row early in a list makes every later row wrong. 39 line items = 35 on the 4 text PDFs that have lines + 4 on one scan.
- Seconds per document: the classify call and the extraction of one document. The value of a scope is the mean of its 3 run means.

**The three document runs gave the same stored result for all 35 fields: the same value, page and confidence.** This is repeatability across three runs on the same eight documents. It is not three samples of documents. The line items of mp1-01 and the completion tokens did differ between the document runs. The page runs differ in 5 fields of 4 documents.

Text PDFs and scans apart:

| Right fields | Control: r1, r4, r5 | Candidate: r2, r3, r6 |
|---|---|---|
| The 6 text PDFs, of 28 | 21, 22, 20 (mean 21.00) | 24, 24, 24 |
| The 2 scans, of 7 | 4, 5, 4 (mean 4.33) | 4, 4, 4 |

The gain is on the text PDFs (+3.00). On the two scans the candidate is not better (-0.33).

### R6. Per document

| Document | Fields | Control: r1, r4, r5 | Candidate: r2, r3, r6 | Difference of the means | Expected before the run (`expected_to_favour` in the manifest of MP1) |
|---|---|---|---|---|---|
| mp1-01 repair invoice, carried forward (3 pages) | 5 | 4, 4, 4 | 5, 5, 5 | +1.00 | document |
| mp1-02 medical invoice, continued (2) | 5 | 4, 4, 4 | 5, 5, 5 | +1.00 | document |
| mp1-03 cancellation, scan (3) | 3 | 2, 3, 2 | 3, 3, 3 | +0.67 | neutral |
| mp1-04 repair quote with terms (3) | 5 | 4, 4, 4 | 3, 3, 3 | -1.00 | page |
| mp1-05 repair invoice, service rows (2) | 5 | 4, 5, 4 | 5, 5, 5 | +0.67 | page |
| mp1-06 medical report, scan (2) | 4 | 2, 2, 2 | 1, 1, 1 | -1.00 | page |
| mp1-07 booking, no price (2) | 4 | 4, 4, 3 | 4, 4, 4 | +0.33 | neutral |
| mp1-08 police report, two dates (2) | 4 | 1, 1, 1 | 2, 2, 2 | +1.00 | document |
| Sum | 35 | 25, 27, 24 | 28, 28, 28 | +2.67 | |

The sample is 8 documents. The candidate is better on 6 and worse on 2.

### R7. The acceptance criteria

| Rule | Control | Candidate | Result |
|---|---|---|---|
| 1. At least 3 more right fields per run | 25.33 | 28.00, a gain of 8/3 = 2.67 | **not met** |
| 2. Its lowest run is higher than the highest run of the control | highest 27 | lowest 28 | met |
| 3a. No wrong amount, date or identifier | 16 in 3 runs | **12 in 3 runs** (4 per run) | **not met** |
| 3b. No more wrong values than the control | 8.67 | 7.00 | met |
| 3c. The reading group loses 1 right field per run at most | 23.67 | 26.00 | met |
| 3d. Classification loses 1 document per run at most | 8 | 8 | met |
| 3e. Every line item of the clean PDFs stays right | 23, 25, 25 of 35 | **28, 23, 29 of 35** | **not met** |
| 3f. No reader error, no call left without an answer | 0 | 0 | met |
| 3g. At most 1.5 times the seconds per document | 10.443 | 9.287 (ratio 0.89; the numbers of the evaluator) | met |
| 4. The gain stays above 0 when any one document is left out | | lowest gain 1.67 | met |

**Decision of the evaluator: the control stays.** Two automated reviewers (R16) computed every rule again from the run reports with their own code: the same numbers.

The 4 wrong amounts or dates of the candidate per run, all at 0.5: `quote_date` of mp1-04 (the key has no quote date; the date of a site visit was stored), `total` of mp1-04, `treatment_date` of mp1-06, `incident_date` of mp1-08. Three of them are wrong in the control too (`treatment_date` in 2 of its 3 runs, the two others in 3 of 3). Only `total` of mp1-04 is right in every run of the control.

The pending mappings of Part B do not change the decision:

| Row of Part B | Mapping in force | Other reading written in Part B |
|---|---|---|
| 3e (P1) | not met | not met: the lowest run of the candidate has 23 right text lines, the highest run of the control 25 |
| 4, 3c (P1) | met | no other reading |
| 3f (P2) | no unusable answer in either scope | the same |

Rules 1 and 3a are applied as #20 wrote them, and each one alone gives "the control stays".

Three points for Paul, with no effect on this decision: the evaluator counts a date stored for an absent field in rule 3a (without it the candidate has 9); rule 3a asks for zero on a set that holds lures, where the control has more of these errors than the candidate; in this run neither scope stored a line item of mp1-04 (R11), so neither scope could reach 35 of 35.

### R8. Raw answers, validation, merge and stored result

The reader makes a stored result from the raw answers in three steps: the model answers, the checks refuse a value whose evidence fails, and the merge keeps the first value of a field that passes. The harness scores the stored result. The table counts the 33 present fields.

| Per run | Control: r1, r4, r5 | Candidate: r2, r3, r6 |
|---|---|---|
| The right value is in at least one raw page answer or block | 31, 32, 31 (mean 31.33) | 29, 28, 28 (mean 28.33) |
| The right value is stored | 24, 26, 23 (mean 24.33) | 27, 27, 27 |

The first row counts a value in any page answer or block. In the document scope a value copied into a block counts too (R9), so the row does not say what the model read on one page.

The control, 21 field-runs with the right value in a raw answer and not stored:

| Cause | Field-runs | Fields |
|---|---|---|
| The merge kept the value of an earlier page; a later page answer held the right value | 18 | `total` of mp1-01 (3) and mp1-02 (3); `refund_received` of mp1-03 (2); `diagnosis` of mp1-06 (1); `incident_date` (3), `customer_name` (3) and `offence` (3) of mp1-08 |
| The check refused the right value (the snippet did not hold it), nothing stored | 3 | `total` of mp1-05 (2), `departure_date` of mp1-07 (1) |

In 17 of the 18 merge cases the later value passed the checks of the reader by itself. All 18 stored values have 0.5, so a person checks them.

The candidate, 4 field-runs: `total` of mp1-04 (3; R9) and `incident_date` of mp1-08 (1).

The difference between the scopes, field by field:

| | Field-runs | Per run | Fields |
|---|---|---|---|
| Candidate right, control not | 14 | +4.67 | In all 14 the raw page answers of the control held the right value: 11 lost in the merge (`total` of mp1-01 and mp1-02, `offence` of mp1-08, `refund_received` of mp1-03), 3 refused by a check (mp1-05, mp1-07) |
| Control right, candidate not | 6 | -2.00 | `total` of mp1-04 (3); `diagnosis` (2) and `treatment_date` (1) of mp1-06 |
| Net | 8 | +2.67 | |

What this shows: on MP1 the candidate stored more right values, although its raw answers held the right value for fewer fields (28.33 against 31.33 of 33).

- In the page scope, a lure of an earlier page came first in 18 field-runs. The merge kept it, and a later page answer held the right value.
- In the document scope, the model saw every page. It usually wrote the final value into every block (R9), so the lure did not come first. Example: for `total` of mp1-01 the page answer of page 1 gives the subtotal 2,435.00; the block of page 1 of the document answer gives 6,766.00, which only page 3 prints.

Two effects are in this gain: the first-value rule of the merge, and the use of all pages by the model. This experiment does not separate them. A page scope with another merge rule was not run, so nothing is known about it. A better choice between the candidate values of two pages is a possible later improvement, with its own measure on the kit and on N1. It is not part of #126.

MP1 was built with these lures: the manifest names them, and 3 documents were marked "document" before the run (R6). 11 of the 14 field-runs above are on documents with such a lure. The size of the gain is a property of this set.

### R9. Safety and provenance

**One wrong value was stored at 0.9, in the candidate only.** `customer_name` of mp1-08: the key is "Dominik Haefeli". Page 1 prints the name with a date of birth, and page 2 prints the name alone.

| | Page answer or block 1 | Page answer or block 2 | Stored |
|---|---|---|---|
| Control (3 of 3 runs) | "Dominik Haefeli (born 1979-02-23)" | "Dominik Haefeli" | the first value at **0.5**: two pages gave two values |
| Candidate (3 of 3 runs) | "Dominik Haefeli (born 1979-02-23)" | "Dominik Haefeli (born 1979-02-23)", with the snippet "Signed: Dominik Haefeli" | the same value at **0.9**: the two blocks agree |

The model copied the value of page 1 into the block of page 2. The merge compares values, the values are equal, and no conflict is seen. The label check of a person field tests only that the value is not a company. The value is wrong by the answer key and by the comparison rule of the harness (a person could read it as the right name with extra text). It is one failure pattern on one field of one document, seen in each of the three document runs. It is not three kinds of failure. In this document another field is stored at 0.5 (`incident_date`), so the routing would still send this claim to a person; the field `customer_name` alone gives no reason for it.

**The model copies values into the page blocks.** The prompt asks for null when a page does not print a value. In the 24 document answers 231 of 249 possible field values are not null. 68 of them are a right value in the block of a page that does not print it. Every field is in every block for 5 of the 8 documents in all 3 runs. The page answers have 186 values that are not null, and 2 of them are a right value on a page that does not print it.

- On a text page the checks handle a copy: a value whose snippet is not on the page of its block is refused. The reader refused 66 of 189 values of the candidate on text pages (17 of 142 for the control). No stored value of a text page has a snippet that the page does not print.
- A copy can hide a conflict. The mechanism is general: a copy removes the second value that the conflict rule needs. In 6 fields the control saw a conflict and the candidate held one copied value: in 5 the copied value was the right one, and 1 is the wrong value at 0.9 above. Six fields give no rate. In two document runs the model also copied the wrong `incident_date` of mp1-08 into both blocks; only the label check kept it at 0.5.
- A copy can lose a right value. `total` of mp1-04: the model put the right total into the block of page 1 with a snippet that page 2 prints. The check refused it. The block of page 2 gave "Total with options", which was stored at 0.5. The control stored the right total.

**A source page on a scan can be wrong in the candidate.** `refund_received` of the scan mp1-03: the value is right, and it is stored with page 1 in 3 of 3 runs. Only page 3 prints it. The pilot showed the same for a quote date. On a scan nothing can check the page of a block, and the first block that gives a field decides the page. These fields have 0.5. In the control no stored value of a scan has a page that does not print it.

What the checks stopped, in both scopes: an invented price for the absent `trip_price` of mp1-07 (1,575 in the page answers, 4,200 in the document answers; never stored); invented subtotals of mp1-05 in the page answers; every stored lure with a label has 0.5.

Checked evidence does not prove that a value is right. A snippet on the page shows that the text is printed there. "Total with options" is printed on its page, and its label passes the check.

### R10. Source pages, with numerators

Pooled over the 3 runs of a scope. The key page is the one page that the answer key names for a field; it gives the official number. "Accepted" counts every page that the answer key lists for a field; it is supplementary.

| Stored values | Control: on the key page | Control: on an accepted page | Candidate: on the key page | Candidate: on an accepted page |
|---|---|---|---|---|
| All | 79/99 (79.8 %) | 82/99 (82.8 %) | 90/102 (88.2 %) | 93/102 (91.2 %) |
| Text PDFs | 63/78 | 66/78 | 78/81 | 78/81 |
| Scans | 16/21 | 16/21 | 12/21 | 15/21 |
| Right values only | 70/73 | 73/73 | 78/81 | 78/81 |
| Wrong values only | 9/26 | 9/26 | 12/21 | 15/21 |

- The official number is higher for the candidate (88.2 % against 79.8 %). The reason is in the wrong values: the control stores more wrong values, and most of them come from another page than the key page.
- For the right values the order is the other way: every right value of the control is on an accepted page (73 of 73). The candidate has 3 right values on a page that does not print them (78 of 81): the refund of the scan, in each run.
- No value at 0.8 or more is on a page that is not accepted, in either scope.

### R11. Line items

| Document (key rows) | Control: stored / right, r1, r4, r5 | Candidate: stored / right, r2, r3, r6 |
|---|---|---|
| mp1-01 (12 over 3 pages) | 14 / 5, 13 / 7, 13 / 7 | 12 / 10, 14 / 5, 12 / 11 |
| mp1-02 (6 over 2 pages) | 6 / 6 in each run | 6 / 6 in each run |
| mp1-03, scan (4) | 0 | 0 |
| mp1-04 (5) | 0 / 0 in each run | 0 / 0 in each run |
| mp1-05 (12 over 2 pages) | 12 / 12 in each run | 12 / 12 in each run |

- **Only mp1-01 separates the scopes.** Every other document has the same count in all six runs. So the difference in line items rests on one document.
- **mp1-01, "Brought forward" rows.** The continuation pages start with a row "Brought forward from page n". Both scopes returned it as a line item, or put its label on the numbers of the next row, or moved the descriptions by one row. 15 wrong rows were stored in 6 runs (10 control, 5 candidate). The line check accepted them: each value of such a row is somewhere on its page. The subtotal and "carried forward" rows were never returned as line items.
- **Scoring by position.** Run r3 holds all 12 key rows and scores 5, because 2 extra rows move the others. Run r2 holds 10 and scores 10. Counted as a set, the key rows among the stored rows are 84 of 105 (control) and 87 of 105 (candidate).
- **mp1-04, a computed unit price.** In 6 of 6 runs the model returned the 5 key rows right. It also returned the optional row of page 2 with a unit price of 106.25. Page 2 prints 1,275.00 for 12 pieces and no unit price: the model computed it. One row is not confirmed, so the reader stored no row at all, as its rule says (D80 (6)), and `total` got 0.5. The 5 right rows were not stored in any run.
- **Raw and stored.** Of the 105 text key rows of a scope, the raw answers held 99 (control) and 102 (candidate) exactly. The stored lists held fewer right rows because of the two points above.
- **Scans.** The reader stores no line item of a scan (no text layer to confirm a row). Both scopes: 0 of 4. The model returned the 4 rows right in each document run.
- **`total` next to a wrong list.** In the candidate `total` of mp1-01 kept 0.9 in 3 of 3 runs while the stored list was wrong. The reader does not compare the rows with the total. The sum check T003 of the claim flow does; the evaluation does not run it.
- A check of the returned rows cannot prove that the model returned every row of a table.

### R12. Efficiency

| Per run | Control | Candidate | Change | Note |
|---|---|---|---|---|
| Requests | 27 | 16 | -40.7 % | by design |
| Extraction requests | 19 | 8 | -57.9 % | by design |
| Prompt tokens | 68,573 | 63,296 | -7.7 % | measured; the same in each run of a scope |
| Completion tokens | 6,016 | 5,714 | -5.0 % | measured; the ranges of the runs overlap |
| Prompt + completion tokens | 74,589 | 69,010 | -7.5 % | measured |
| Seconds per document | 10.44 | 9.29 | -11.1 % | measured |
| of them: classify | 1.30 | 1.75 | | the same request in both scopes; 4 slow answers in the document runs |
| of them: extraction | 9.15 | 7.54 | -17.6 % | measured |

- **41 % fewer requests gave 11 % less time and 8 % fewer tokens.** The page images are most of every prompt, the answers are almost as long, and the classify call stays. The saving in time or cost is not in proportion to the request count.
- The run means of the seconds per document overlap between the scopes (9.57 to 11.18 s against 8.82 to 9.69 s). For the extraction alone they do not overlap.
- Seconds per document over the 24 readings of a scope (8 documents, 3 runs): control median 11.27, lowest 3.90, highest 22.91; candidate median 9.19, lowest 4.02, highest 17.22.
- Cached prompt tokens: 0 for the first use of each request, 97.7 % or more for each repeat. This comes from sending the same requests again. A new document is not cached. The effect of the cache on the seconds was not separated from the order of the runs.
- The largest document answer used 1,421 of 2,048 output tokens (69 %; mp1-05, 12 line items). The same document used 1,111 in another run. No answer was cut.
- Reasoning tokens: 0 in all answers.

### R13. Supplementary analyses (never the verdict)

The thresholds of 1 and 2 and the five gates are supplementary. Nobody approved them. They do not change the verdict.

| Analysis | Result |
|---|---|
| Rule 1 with a threshold of 1 or 2 | rule 1 would be met; "every rule met" stays false, because 3a and 3e are not met |
| Gate 1: wrong values at 0.8 or more not above the control | not met: 0, 0, 0 against 1, 1, 1 |
| Gate 2: no value at 0.8 or more for an absent field | met |
| Gate 3: no value at 0.8 or more on a page that is not accepted | met |
| Gate 4: share of stored values on an accepted page not below the control | text PDFs met (66/78 against 78/81); scans not met (16/21 against 15/21) |
| Gate 5: no placeholder stored | met |
| Right at 0.8 or more in every control run and not in every candidate run | 1 field (`customer_name` of mp1-04, at 0.5 in the candidate). The reverse: 5 fields |
| Right value on an accepted page | control 25, 27, 24; candidate 27, 27, 27 |
| Cut answers | none |

### R14. Limits

- 8 mock documents from one generator, made by the people who built the reader. Several contain lures for the weak points of both scopes. All are in English. None is a photo.
- The documents have 2 or 3 pages. The reader allows up to 10 pages. The largest document answer used 69 % of the output limit, so a longer document can be cut. This was not tested.
- 3 runs per scope, on one day, in one invocation, at temperature 0.2, with one model. The runs repeat the same documents.
- Each finding of R9 and R11 rests on 1 to 3 documents.
- One prompt for the document scope. Another wording could change the copying. It was not tested.
- The page of a field on a scan is a claim of the model in the document scope (Part D, point 2; observed in R9). Paul decides if that is acceptable for a default.
- The raw answers are not in git. Without the folder of R15 nobody can check these numbers from git alone.
- The evaluation reads documents. It builds no claim and runs no routing, settlement or payment.
- "Wrong" follows the answer key and the comparison of the harness, which has no fuzzy match: a name with a date of birth is wrong, and "lumbardisc herniation" is wrong.
- What a scan page prints is known from the answer key and from a reviewer who read the page images. The system cannot check it.
- Speed was measured in one invocation on a shared endpoint. No rate limit and no error was seen.
- Valid JSON in every answer does not prove that the endpoint enforces the schema.
- **No call failed, so this experiment gives no new evidence about a document that gives no field** (Part D, point 1; D80 (13)). That gap stays open. It must be fixed in the base, or assessed in writing by Paul, before the document scope can be a default.

### R15. How to reproduce, and where the evidence is

In git: the reader with `scope`, the harness, the runner, the rules, MP1 and its generator, the tests, this file.

- Offline, no key: `cd backend && uv run python -m app.docintel.experiment dry-run main --out .dev-data/issue-126/dry/main` plays the plan with a stub (129 requests to the stub). It says nothing about the model.
- A new live measure needs a new freeze and its own authorization (section 7). Its numbers will be close to these, not equal: the hosted model changes with time.
- With the saved answers: `uv run python -m app.docintel.experiment replay .dev-data/issue-126/live/main-1` reads the six runs again with no request and compares them with the saved reports. It needs the code of commit `aa0820c`, or a later commit that does not change the reader or the harness.

Not in git, like the raw answers of #20 and #125: the folder `backend/.dev-data/issue-126/` on Nikola's machine.

| File | Content | SHA-256, first 16 characters |
|---|---|---|
| `live/freeze.json` | the freeze of the method, the code and MP1 | `894e11f2c7b25e02` |
| `live/pilot-1/calls.jsonl`, `pilot.json` | the 4 answers and the checks of the pilot | `1c9e824edf73f06d`, `ac68a91383b1e6da` |
| `live/main-1/calls.jsonl` | the 129 raw answers, one line per attempt | `04718d027d9bf6b2` |
| `live/main-1/run-r1-page.json` to `run-r6-document.json` | the six reports of the harness | `a24de33ebad06f66`, `b318c29ec3394026`, `0289d62c02eff29d`, `52528bb7386d240d`, `77d654d4e779ffe1`, `34d058982f06237d` |
| `live/main-1/verdict.json`, `verdict.md`, `meta.json` | the output of the evaluator, the run data | `993777060c8b81dc` (`verdict.json`), `a35b961b2c97e336` (`meta.json`) |
| `EVIDENCE-SHA256.txt` | the full SHA-256 of every file under `live/`; check with `sha256sum -c EVIDENCE-SHA256.txt` | |
| `analysis/` | the scripts of the independent reviews | |

### R16. Independent reviews

The reviews were automated: AI agents (Claude Sonnet) that Nikola's agent started, each with its own code, with the saved files only and with no request to the model. They are not reviews by a person.

- After the main run, five reviewers: a recount of every field (210 field rows, no difference from the harness); safety and provenance; line items; speed and tokens; the rules and the integrity checks.
- Before this text was committed, two reviewers: every number of this text against the files, and the claims and the wording of this text.

No reviewer found an error in the harness, the evaluator or the record. Their findings are in R8 to R12. The second review of the text corrected an earlier wording of R8 that claimed too much: the gain has two causes that this experiment does not separate.
