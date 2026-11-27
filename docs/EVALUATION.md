# Evaluation: which model drives the claim agent

Sections 1 to 3 measure the hand-written loop of issue #12 (removed by issue #47). The section "Again with the LangGraph agent" measures the graph. The script is the same.

Date: 2026-11-12. Issue #12, decision D17. Endpoint: `https://integrate.api.nvidia.com/v1` with the free developer key.

**Default: `nvidia/nemotron-3.5-lightning-30b-a3b`.** Both models get all 9 claim files right, but Lightning answers a turn twice as fast, uses 40% fewer tokens, and never needed the fallback message.

## How it was measured

`cd backend && uv run python ../scripts/measure_agent.py` (needs `NVIDIA_KEY` in `.env`).

- The backend runs in `nim` mode, in the process of the script (FastAPI `TestClient`, an empty database), with the fixture reader.
- Each of the three claim files is played 3 times with three different customer wordings (`backend/tests/live_claims.py`): all facts in one message, facts in the other order, and a story with no date and no amount, so the agent must ask. The player answers what the agent asks. It uploads the documents one by one, then says "no, that's everything" in its own words. At most two more "I am done" messages if the agent does not close.
- "Right outcome" means: the expected outcome (`straight_through`, `handler_review`, `investigation`) **and** the customer text word for word. "Right date and amount" means the claim holds the date and the amount of the claim file.
- A turn is one customer message or one upload, as the customer panel sees it (it includes every model call and tool call of the turn). The worst turns (30 to 60 s) are slow model calls; 60 s is the timeout of the HTTP client.
- Tool calls are the calls the model asked for. Tokens are prompt plus completion (reasoning included).
- Default settings of the models (reasoning as the endpoint gives it), `max_tokens` 2000.

## Result after the prompt change (final)

**`nvidia/nemotron-3.5-lightning-30b-a3b`**

| Claim file | Right outcome | Right date and amount | Model calls | Tool calls | Seconds per turn (median / worst) | Tokens | 429 |
|---|---|---|---|---|---|---|---|
| travel | 3/3 | 3/3 | 17.3 | 15.7 | 7.3 / 54.4 | 34,918 | 0 |
| property | 3/3 | 3/3 | 17.0 | 13.7 | 4.2 / 12.4 | 32,831 | 0 |
| health | 3/3 | 3/3 | 16.3 | 12.0 | 4.9 / 32.7 | 31,577 | 0 |
| All | **9/9** | 9/9 | 16.9 | 13.8 | 4.9 / 54.4 | 33,109 | 0 |

**`nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`**

| Claim file | Right outcome | Right date and amount | Model calls | Tool calls | Seconds per turn (median / worst) | Tokens | 429 |
|---|---|---|---|---|---|---|---|
| travel | 3/3 | 3/3 | 23.0 | 14.3 | 9.9 / 33.2 | 57,654 | 0 |
| property | 3/3 | 3/3 | 28.3 | 20.3 | 17.2 / 55.7 | 73,394 | 0 |
| health | 3/3 | 3/3 | 16.3 | 11.7 | 8.8 / 42.2 | 36,799 | 0 |
| All | **9/9** | 9/9 | 22.6 | 15.4 | 10.5 / 55.7 | 55,949 | 0 |

Fallback messages ("Sorry, I could not answer just now"): Lightning 0 in 9 claims, Omni 5 in 9 claims. The claim still ended right every time, because the player writes again after a fallback. A real customer would see the fallback and have to write again.

## Before the prompt change (same player, same 9 claim files)

| Model | Right outcome | Model calls per claim | Seconds per turn (median / worst) | Tokens per claim | 429 | Fallback messages |
|---|---|---|---|---|---|---|
| Lightning | 9/9 | 21.1 | 5.7 / 60.0 | 41,520 | 2 | 1 (a 60 s timeout) |
| Omni | 9/9 | 24.0 | 8.5 / 62.7 | 53,929 | 0 | 3 |

### What went wrong, and what was changed (once)

A first run of the measure gave 6/9 for both models. All three failures per model were a bug of the player, not of the models: when the story had no date, the player sent the story again instead of answering the question about the date. The player was fixed (it now answers what the agent asks), and that run is not in the tables.

In the second run (the "before" table), Omni ended 3 turns with the fallback message. A trace of one of them (property file, the turn after "no, that's everything"):

1. In the turn of the last upload, Omni called `classify_document` but not `extract_document`, then wrote "all documents are received". The document stayed unread.
2. At "done" it ran the three checks and `assess_and_route`. The trusted code refused (rule 1: one document is not read), with an error that names the document.
3. Omni then classified and extracted **all** documents again, one call per reply, then ran the three checks again, one call per reply. It reached the limit of 12 model calls before `assess_and_route`.

The enforcement worked (the model could not route a claim with an unread document). The cost was the number of calls. Changes:

- Prompt: "read it before you answer: call classify_document and extract_document, both"; "you may make several tool calls in one reply"; "ask for the date in the customer's own words and convert it yourself" (the model was asking customers for `YYYY-MM-DD`).
- Enforcement: in a turn with an upload, the runner sends the model's answer back once if it has not read the uploaded document (`UNREAD` in `backend/app/agent/llm.py`, removed with the loop by issue #47).

After the change both models are still 9/9. Lightning went from 21.1 to 16.9 model calls per claim and from 1 fallback to 0. Omni went from 24.0 to 22.6 calls, but still ended 5 turns with the fallback, for other reasons: three times its message named a tool (`run_checks`, `classify_document`) and was refused by the output filter, and twice its message was cut off at `max_tokens` because the reasoning tokens used the room. It would need more `max_tokens` or `AGENT_THINKING=off`: not tested, because Lightning does not need it.

## Again with the LangGraph agent (issue #47)

Date: 2026-11-12. Same script, same player, same 9 claim files, 3 runs per file, `nvidia/nemotron-3.5-lightning-30b-a3b` only. The agent is now the graph of `docs/ARCHITECTURE.md` section 1a (`ChatOpenAI`, SQLite checkpointer). The model has no tool that checks or routes, and "done" is decided in code for the Done button and the usual phrases.

| Claim file | Right outcome | Right date and amount | Model calls | Tool calls | Seconds per turn (median / worst) | Tokens | 429 | Fallback messages |
|---|---|---|---|---|---|---|---|---|
| travel | 3/3 | 3/3 | 17.3 | 11.7 | 7.9 / 21.6 | 26,319 | 0 | 0 |
| property | 3/3 | 3/3 | 17.7 | 12.0 | 5.5 / 54.6 | 26,442 | 0 | 0 |
| health | 3/3 | 3/3 | 14.0 | 9.3 | 5.2 / 23.1 | 19,446 | 0 | 0 |
| All | **9/9** | 9/9 | 16.3 | 11.0 | 5.5 / 54.6 | 24,069 | 0 | 0 |

Against the hand-written loop of #12 (Lightning, same player):

| | #12 (loop) | #47 (graph) |
|---|---|---|
| Right outcomes | 9/9 | 9/9 |
| Model calls per claim | 16.9 | 16.3 |
| Tool calls per claim (asked by the model) | 13.8 | 11.0 |
| Seconds per turn (median / worst) | 4.9 / 54.4 | 5.5 / 54.6 |
| Tokens per claim | 33,109 | 24,069 (-27%) |
| Fallback messages | 0 in 9 | 0 in 9 |
| Refused tool calls | not counted | 0 in 9 |

What the numbers say:

- Same result, 27% fewer tokens: each node has a short prompt and 2 to 5 tools, and the conversation in the context holds no tool traffic.
- The model calls did not go down much (16.3 against 16.9). The model still needs one call per tool step and one for each message. In #12 the model asked for the checks and the routing in one reply. Now no model call is spent on them. The graph removes the chance to get the order wrong, and the 2.8 fewer tool calls per claim, but few model calls.
- The median turn is 0.6 s slower. The wifi was slow tonight and the endpoint was busy. The worst turn (54.6 s) is one slow model call, with no timeout and no retry. This is not an effect of the graph: the same worst value was in #12 (54.4 s).
- No tool call was refused (`refused` events): with 5 tools per node the model made no call outside its tools. The code paths that the rules of #12 guard (unread document, checks out of date) were not reached with a real model. They are tested with the fake model and by mutation (remove a rule, a test fails).
- "429" counts model calls that failed with a 429 after the retries of the client. The OpenAI client retries a 429 by itself and does not report it, so a retried 429 is not counted here. There was none that ended in a failure.
- The measured code is the commit before the last one of the ticket ("a claim with the three facts goes straight to documents"). That change does not touch these flows.

## Rate limit of the free key (O15, first measure)

The only 429 answers came from Lightning: 2 of them in one run, after 34 model calls in the 60 seconds before. The client retried after 1 s and the second try worked. About 40 requests per minute looks like the limit of the key, but the limit is not documented: measure again before the event. A claim file makes 17 to 23 model calls, so two visitors at the same time can reach it.

## Omni reader on claim file 1 (issue #18)

Date: 2026-11-17. Decision D80. `OmniReader` (`backend/app/docintel/omni.py`), model `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning` at `https://integrate.api.nvidia.com/v1` (`VLM_MODEL`, `VLM_BASE_URL`), free developer key. One call to classify (page 1), then one call per page. Each page is the full page as a PNG at 150 DPI (1241 x 1754 px for A4). `response_format` `json_schema` (strict), the schema also in the prompt. Thinking off (`chat_template_kwargs.enable_thinking: false`), temperature 0.2, `max_tokens` 2048. Mock data only.

Commands (need `NVIDIA_KEY` in `.env` at the repository root):

- Request formats (spike): `cd backend && uv run python ../scripts/probe_omni.py format`
- Fields of claim file 1, and the claim through the API (local mode, `CLAIMS_READER=omni`): `cd backend && uv run pytest -m live tests/test_omni_live.py -k claim_file_1 -s`
- The reader contract on every mock file: `cd backend && uv run pytest -m live tests/test_omni_live.py -k contract`

### Request formats (spike, one call each, page 1 of the booking of claim file 1)

| Variant | HTTP | Seconds | Answer |
|---|---|---|---|
| Schema in the prompt only | 200 | 6.5 | valid JSON, valid values |
| `response_format` `json_schema` | 200 | 4.9 | valid JSON, valid values |
| `response_format` `json_object` | 200 | 5.1 | valid JSON, valid values |
| `nvext.guided_json` | 400 | | field not supported |
| Thinking on (schema in the prompt) | 200 | 41.8 | cut before the JSON: no valid answer |
| A JPEG phone photo (receipt) | 200 | 3.5 | valid JSON |

One call per variant: the seconds show no real difference between the first three. `json_schema` is chosen because the endpoint forces the form of the answer. The snippet that the model gives for `line_items` (the whole table) is not on the page; the reader does not use it (it confirms each line on the page text).

### Claim file 1: 3 runs, measured

| Document | Field | Result (same in the 3 runs) | Source stored |
|---|---|---|---|
| Booking | `booking_reference` | **missing** | none (refused, see below) |
| | `departure_date` | right | page 1, snippet checked on the text layer |
| | `trip_price` | right | page 1, snippet checked |
| | `traveller_name` | right | page 1, snippet checked |
| Cancellation | `booking_reference` | right | page 1, snippet checked |
| | `cancellation_date` | right | page 1, snippet checked |
| | `refund_received` | right | page 1, snippet checked |
| Medical certificate | `patient_name` | right | page 1, snippet checked |
| | `cause` | **wrong** | page 1, snippet checked |
| | `unfit_from` | right | page 1, snippet checked |

- **Fields: 8 right, 1 wrong, 1 missing, out of 10**, in each of the 3 runs.
- **Line items: 6 of 6 right** (3 of the booking, 3 of the cancellation; 0 expected and 0 read on the certificate). Seen in run 1 and in a fourth run of the field test; runs 2 and 3 did not print them.
- **Sources:** every stored field has page 1 (right) and a snippet that the reader found on the text layer of the page, holding the value: confidence 0.9 for all 9. No stored source was invented. The three PDFs have a text layer, so no field has a whole-page box.
- **Why `booking_reference` is missing on the booking:** the model read the right value (`WPR-482913`) but gave the title of the page (`Booking confirmation`) as its snippet, in every run. The snippet does not hold the value, so the reader refuses the field (D80 (3)). The reader does not search the page for the value itself: that would replace the evidence of the model with a guess of the reader.
- **Why `cause` is wrong:** the expected value is a category (`illness`, from "acute illness"). The model gave the diagnosis printed on the page (`gastroenteritis with fever`), with a true snippet. The schema has no field descriptions yet (D77 (5)). The snippet check cannot catch it: the text is on the page (the limit "true text, wrong meaning" of D80 (3)).
- **Seconds per page** (one-page documents, classify and read together, 12 samples: 3 documents x 4 runs): median **5.5 s**, range 2.3 to 12.9 s. Booking about 6 s, cancellation about 5.6 s, certificate about 2.6 s. The live contract run: 210 s for the 35 mock files and the junk bytes (about 6 s per file).
- **Outcome through the API** (local mode, scripted agent, `CLAIMS_READER=omni`): **`straight_through`, score 0, no signal, no routing reason, in the 3 runs.** The fixture reader gives the same. It is right for the right reasons: every field that the routing reads was right and checked (`departure_date` and `cancellation_date` for C002; `trip_price` and `refund_received` for C003 and the settlement; the names for the consistency check; `unfit_from` for the incident date). The two errors are in fields that no check reads (the cause of the claim comes from the words of the customer, not from the certificate).
- **Contract:** `check_reader_contract(OmniReader())` passed (35 mock files, about 70 model calls, no refused request).
- **Running backend, through the endpoints of the UI** (uvicorn with `CLAIMS_MODE=local`, `CLAIMS_READER=omni`, one more run): the 3 uploads were read; `GET /claims/{id}` (the pipeline panel) holds the same 9 fields, each with its document id, page 1 and its snippet; the outcome is `straight_through`, status `paid`; the customer reads "Your claim is validated." The Reader bench (`POST /bench/read`) shows `booking_reference` of the booking as "missing" and `cause` as "wrong", and 3 of 3 line items twice. The page in a browser was not looked at in this check.
- **In the browser** (Nikola, by hand, `scripts/dev.sh` with `CLAIMS_READER=omni`): Anna Keller, policy TRV-2026-0417, the 3 PDFs uploaded. The 3 documents were classified right; the claim was validated with CHF 1,840.00 less the refund of CHF 340.00 = CHF 1,500.00; the customer read "Your claim is validated."

### Rate limit of the free key with Omni (O15, second measure)

`cd backend && uv run python ../scripts/probe_omni.py rate --per-minute 60 --max 80`: tiny text requests (4 tokens, thinking off), one at a time at a fixed pace, no retry, stop at the first error. 2026-11-17, 16:38:08 to 16:39:29 UTC: **80 answers 200 in 80.5 s, no 429** (about 60 in each 60 s window). So the limit for this model and key is above 60 requests per minute over 80 s; this run gives no upper bound. Not measured: requests with images (a limit on tokens would not show), a longer window, two visitors at the same time. The first measure (about 40 per minute) came from Lightning: the limit can differ per model.

### Limits of this measure

- One claim file, three clean PDFs with a text layer. Scans, photos, other types and the full kit are not measured here (issue #19).
- A right outcome does not prove the reading right: a missing field makes some checks skip in silence (for example, no `refund_received` skips C003).
- The confidences 0.9 and 0.5 are labels, not measured probabilities (D80 (4)).
- The model can give a wrong field with a true snippet (for example a line amount as the trip price); here it did not, on these three files.
- The results of the model can change from run to run; here the fields were the same in the 3 runs.
- The 3 runs above came before the review fixes of 2026-11-17 (D80 (3), (8), (11), (12)). One more live run after the fixes (claim file 1 through the API, 6 model calls): the same result, 8 of 10 fields right (the same missing and wrong field), 6 of 6 line items, every source checked at 0.9, `straight_through`, status `paid`; classify 1.0 to 1.2 s and read 2.4 to 4.8 s per call, 14.9 s for the whole claim. The page and pixel limits do not touch claim file 1 (three one-page A4 PDFs). The whole-number rule for snippets is stricter (it refuses a snippet that cuts a number, for example `CHF 1,840` taken from `CHF 1,840.00`), but it refused no snippet of this run: the old rule and the new rule gave the same answer for each of the 10 snippets that the model gave. Offline, the 90 snippets of the answer keys of the mock PDFs with a text layer all pass too. One run only: the change from run to run is not measured.

## Other things seen

- One run of the first measure ended with 11 model calls failing at once (about 0 s each) after a 74 s call. Its cause is not known: the error text was not kept. The later runs kept the reason of every failure (`fallback` events); the only failure in 36 claims was one 60 s timeout. Not reproduced.
- Reasoning off (`chat_template_kwargs.enable_thinking: false`, from the NVIDIA model card) works on both models (checked with one call each: `reasoning_tokens` is 0, the tool call is the same). The client supports it with `AGENT_THINKING=off`. It was not measured.
- Both models return several `tool_calls` in one answer. The runner runs them in order.

## Sandbox mode against nim mode (issue #14)

Date: 2026-11-12. Same player, same 9 claim files, 2 runs of each of the three claim files per mode (6 claims per mode), `nvidia/nemotron-3.5-lightning-30b-a3b`. Command: `cd backend && uv run python ../scripts/measure_agent.py --mode nim --runs 2` and `--mode sandbox --runs 2`, one after the other (the 40 requests per minute of the key). `nim`: the graph in the backend process. `sandbox`: the same graph in an OpenShell 0.1.5 sandbox on Colima, the tools over MCP, the image already on the Docker engine. Wifi and the model endpoint are not the same from one minute to the next: read the difference between the modes as a size, not as a precise value.

A turn is the time of one HTTP call of the customer panel: a message, an upload, the Done message or the policy choice that starts the chat. In `sandbox` mode the first turn of a claim includes the creation of its sandbox.

| Claim file | Mode | Right outcome | Model calls | Seconds per turn (median / worst) | Tokens | Fallback messages |
|---|---|---|---|---|---|---|
| travel | nim | 2/2 | 17.0 | 5.5 / 19.4 | 25,514 | 0 |
| travel | sandbox | 2/2 | 15.5 | 6.1 / 25.7 | 22,948 | 0 |
| property | nim | 2/2 | 15.5 | 6.3 / 13.7 | 22,808 | 0 |
| property | sandbox | 2/2 | 16.5 | 8.5 / 26.5 | 24,630 | 0 |
| health | nim | 2/2 | 14.0 | 9.6 / 23.2 | 20,654 | 0 |
| health | sandbox | 2/2 | 11.0 | 6.6 / 16.8 | 15,153 | 0 |
| **All** | **nim** | **6/6** | 15.5 | **6.6 / 23.2** | 22,992 | 0 |
| **All** | **sandbox** | **6/6** | 14.3 | **7.4 / 26.5** | 20,910 | 0 |

The sandbox itself, from the `sandbox` events of the same runs:

| What | Seconds |
|---|---|
| Create a sandbox (image already on the engine), 6 claims | median 0.49, worst 0.52 |
| `exec` of a turn (34 turns): median / worst | 7.15 / 26.50 |
| Fixed cost of a turn: `exec` of `true` | 0.05 |
| Fixed cost of a turn: Python start and imports (LangGraph, LangChain, MCP) | 0.9 |
| Delete a sandbox (the `deleted` event of the browser run; a demo reset with one sandbox took 0.18 in all) | 0.12 |

What the numbers say:

- The sandbox costs about 0.5 s once per claim (the creation) and about 1 s per turn (Python and the imports start again in each turn, 0.9 s). The median turn is 0.8 s slower (7.4 against 6.6 s); the model call is the rest of every turn, as in the spike. The per-file differences (property slower, health faster) are the number of model calls and the wifi, not the mode.
- Same result in both modes: 6/6 right outcomes, right date and amount, and 0 fallback messages. The three customer texts were word for word (checked by the test, and by the player).
- The worst turn (26.5 s) is a turn with a slow model call, as in `nim` mode (23.2 s).
- The MCP calls cost milliseconds: the backend and the sandbox are on the same machine.
- Not measured here: a first creation on a host where the image is not yet there (pulling `python:3.12-slim` took 4.4 minutes on the slow wifi, spike notes). Many sandboxes at the same time: see the last section.

## Several visitors at the same time (issues #115 and #117)

Date: 2026-11-17. The Brev VM, `sandbox` mode, `nvidia/nemotron-3.5-lightning-30b-a3b`, the free key. N threads each play one claim file (travel, property, health, then travel again) with the first wording of `backend/tests/live_claims.py`, against the running backend. One run for each line: read the numbers as a size.

| At the same time | Code | Total seconds | Right outcome | Answers 429 | Fallback messages | Worst turn (s) |
|---|---|---|---|---|---|---|
| 1 | before | 60 | 1/1 | 0 | 0 | - |
| 3 | before #115 | - | 2/3 | 0 | 0 | - |
| 3 | after #115 | 40 | 3/3 | 1 | 1 | 12.4 |
| 5 | after #115 | 80 | 3/5 | 21 | 21 | 27.8 |
| 5 | after #117 | 166 | 5/5 | 25 | 0 | 68.7 |

- Before #115, the third claim failed at once with a 500: a read of the store with no lock. It is not a limit of the sandboxes.
- The sandboxes are not the limit: five at the same time started and worked. The limit is the key, about 40 model calls per minute (O15). A claim file makes 13 to 25 model calls.
- After #117 the agent waits 5, 10 and then 20 seconds after a 429 and calls again. No visitor reads the fallback message, but a turn can take one minute, and five claim files take near three minutes.
- For more than three visitors at the same time, the key needs a larger limit. Paul decided for the event: one visitor at a time at the stand (D79).

## Evaluation harness of the reader (issue #19)

Date: 2026-11-18. Decision D81. The harness is `backend/app/docintel/evaluate.py`. The rule for one value is `backend/app/docintel/compare.py`. The page "Metrics" shows the last report (`GET /evaluation/latest`).

### Commands

- Fixture reader, no key, no network: `cd backend && uv run python -m app.docintel.evaluate --reader fixture`
- Omni reader, three runs (needs `NVIDIA_KEY`, `VLM_BASE_URL` and `VLM_MODEL` in `.env` at the root): `cd backend && uv run --env-file ../.env python -m app.docintel.evaluate --reader omni --runs 3`
- `--runs N` makes N runs, with a new reader for each run. `--output FILE` writes a second copy of the report.
- Combine saved runs, with no reader and no model call: `cd backend && uv run python -m app.docintel.evaluate --combine FILE FILE ...`. It writes a new `latest.json` and a dated copy, and never writes over an input. See "Combine saved runs" below.
- The command prints a markdown summary and writes the report to `backend/.dev-data/evaluation/latest.json`, with a copy named `evaluation-<reader>-<UTC time>.json`. `CLAIMS_EVALUATION` changes the folder. The write is atomic: the page never reads half a report.
- Exit code 0: the report is written. Exit code 1: the report is written, but an answer key could not be read (`complete: false`). Exit code 2: no report, because the reader did not start (for example, Omni with no `NVIDIA_KEY`).
- The reports are not in git (`.dev-data/` is in `.gitignore`). The numbers that count go into this file and into the pull request, with the command.

### What is measured

- **The kit.** Every PDF, JPG and PNG in `data/mock/<folder>/`: the same list as `/demo/kit` and the Reader bench. On 2026-11-18 that is 35 documents: 26 of the scenarios and 9 hard versions (6 scans, 3 phone photos). They hold 132 expected fields and 73 expected line items.
- **Kind** (clean, scan, photo) comes from the list `hard` of `data/mock/index.json`. **Workflow** comes from the policy of the scenario; a hard version takes the scenario of its source document.
- **Per document**, the harness calls `classify`, then `extract`, through the `Reader` contract, and times each call with `time.perf_counter`. It touches no claim, no store, no upload and no gateway.

### When a value is right

The kind of a field comes from its schema (#17), not from its value.

| Kind | Fields | Right | Wrong |
|---|---|---|---|
| Amount | every `float` field (`trip_price`, `total`, ...) | The same value to the cent (half up). `1840`, `1840.0`, `"1,840.00"`, `"1'840.00"` and `Decimal("1840.00")` are the same. | Another cent value, another sign. A text that is not a plain number: a currency (`"CHF 1,840.00"`), a decimal comma (`"1.840,00"`), bad groups (`"1,84"`). NaN, infinity, a bool. |
| Date | every `date` field | The same calendar day as `2026-08-10`, `10.08.2026`, `10.8.2026`, `10 August 2026`, `10 Aug 2026`, `10 Sept 2026` or `August 10, 2026`. | A slash date (`08/10/2026` is ambiguous), a two-digit year, `2026-8-10`, `20260810`, a day that does not exist, a date with a time. |
| Identifier | a text named `*_number` or `*_reference` | The same characters. Case, outer spaces, Unicode forms and dash characters (`WPR‑482913` with U+2011) do not count. | A number in place of a text (`482913` loses a leading zero), an inner space, another character. `001840` is not `1840`. |
| Text | any other text | The same text. Case, runs of spaces and dash characters do not count. | Another word, an accent (`Sanitär` is not `Sanitaer`), punctuation. There is no fuzzy match. |

A missing value is a field that is absent, `null` or blank text, or a field of a reader that raised. A missing value is never right (except when the answer key itself is `null`: no field of the kit is).

`compare` in `backend/app/bench.py` (D64) is not used here: it scores `"1,840.00"` against `1840.0` as wrong, the example of the issue, and `"001840"` against `"1840"` as right.

### How the numbers are counted

- **Right, wrong, missing** per expected field. These three add up to the expected fields. A wrong value and a missing value are always shown apart: a wrong amount can pay a claim, a missing one goes to a person.
- **Extra**: a field that the answer key does not expect. It is counted apart and is not in the denominator. A name given twice with the same value is right (the document lists it in `duplicates`); with two values it is wrong.
- **Accuracy** = right values / expected fields.
- **Source page right**: of the values read, how many have a valid source on the expected page. A value read is a right or wrong value; a field that the answer key gives as `null` and that the reader does not give is right, but has no source to check. A valid source names this document, a page that exists, and a snippet or a box (non-negotiable 3). An invalid source is never "page right".
- **Verified** = the value is right and its source is on the expected page. The value and the page are measured apart: a right value on the wrong page counts in the accuracy, but not as verified.
- **Pass rate** = documents with every expected field verified, and with no error of the reader / documents. Extra fields, the type and the line items do not change it.
- **Line items** are compared by position (description as text; quantity, unit price and amount to the cent), and reported apart: lines right / lines expected, and documents with every line right. They are not in the field accuracy and not in the pass rate.
- **Type**: the harness counts how many documents `classify` names right. The confusion table is issue #21.
- **Failures**: a document whose reader raised stays in every denominator, with every field missing and the error in the report. A document whose `expected.json` is absent or broken cannot be scored: the report lists it in `answer_key_errors` and says `complete: false`.
- **Runs**: each run has its own numbers (`run_details`). The page and the tables add the counts of all runs (`pooled`). `summary` gives the mean, the lowest and the highest value of each run, and the spread (highest − lowest).
- **Four measures, kept apart.** Field accuracy (right values / expected fields), the strict document pass rate, classification (the type that `classify` names right / documents) and line items (lines right / lines expected) are four numbers. Only the first two are about the fields. The summary gives each one per run (mean, lowest, highest), and the page "Metrics" shows each in its own tile.

### Combine saved runs

`--combine FILE FILE ...` makes one report from the runs of two or more saved reports, in the order of the files. No reader runs and no model is called. Use it to add runs to a measure without making the first runs again.

- **The numbers are made again from the rows.** For each document of each run, the counts and the pass are made again from its field rows. Then every total (per run, added, by type, by kind, by field) and the mean, lowest and highest are made from them. No percentage of a file is averaged.
- **A file is checked on its own first.** It must be readable JSON of schema version 1, with `runs` equal to its number of runs, numbered 1 to N. The counts it states for each document and each run must match its rows. A file that fails is refused, with the file and the run in the message.
- **The files must match each other.**
  - the same reader and the same `settings` (the configuration, and the model settings that the requests held)
  - the same fingerprint of the reader code, when the files hold one
  - the same `kit`, `complete` and `answer_key_errors`
  - in every run: the same documents, kinds, workflows and pages, and the same answer keys (every expected value and page)
- **Duplicates are refused.** This covers a file given twice, two files with the same content (for example `latest.json` and its dated copy), and a run that is in two files.
- **Fingerprint of the reader code.** Each new report holds `reader_code_sha256`: the SHA-256 of the source file of the reader (for Omni, `omni.py`, with its prompt, its settings and its code; line ends do not count). A report written before this existed has none. It is combined, and the combined report says so in `combine_notes`: its prompt and code are then checked by its settings only.
- **Nothing is half done.** When the files are refused, the command writes nothing: `latest.json` stays as it was. The output must not be one of the input files.
- The combined report holds `combined_from`: the name, the SHA-256, the date and the number of runs of each file. Each run keeps its own `run_id` and `started_at`, and its `source`: the file and the run number in that file.
- **Seconds per document** = the time of `classify` plus `extract`, per document; the mean, median, lowest and highest.
- **HTTP** (Omni only): an event hook of the HTTP client counts every request and every answer by status. A 429 that the reader retried is counted too, so `http_429` is the number of 429 answers. `settings.sent` lists the model settings that the request bodies held (model, temperature, `max_tokens`, thinking, response format). The report keeps no header, no key, no image and no answer of the model.

### Result: fixture reader

`cd backend && uv run python -m app.docintel.evaluate --reader fixture --runs 3`, 2026-11-18, Windows laptop.

| | Documents | Fields right | Wrong | Missing | Source page right | All right | Line items right |
|---|---|---|---|---|---|---|---|
| Clean | 26 | 94 of 94 | 0 | 0 | 94 of 94 | 26 of 26 | |
| Scan | 6 | 25 of 25 | 0 | 0 | 25 of 25 | 6 of 6 | |
| Photo | 3 | 13 of 13 | 0 | 0 | 13 of 13 | 3 of 3 | |
| **All, per run** | **35** | **132 of 132** | **0** | **0** | **132 of 132** | **35 of 35** | **73 of 73** |

The 3 runs gave the same numbers: accuracy, verified accuracy and pass rate 100% in each. Each of the 11 document types is at 100%. Seconds per document: below 0.001 s (the fixture reader reads a hash); one run of the 35 documents took 0.02 to 0.03 s. This result tests the harness, not a model: the fixture reader returns the answer keys. `backend/tests/test_evaluate.py` checks the 100% in CI, and gives the harness readers that are wrong on purpose (a wrong amount, a missing field, a null, a wrong page, a source of another document, a field given twice, a reader that raises, a broken answer key): each one is counted as wrong or missing, never as right.

### Result: Omni reader, 3 runs

Date: 2026-11-18. Three runs on the 35 documents of the kit, one call at a time, with the free developer key, from a Windows laptop on a home network. Mock data only.

Commands (in `backend/`, with `NVIDIA_KEY`, `VLM_BASE_URL` and `VLM_MODEL` in `.env` at the root):

1. Run 1, 15:44 to 15:47 UTC: `uv run --env-file ../.env python -m app.docintel.evaluate --reader omni --runs 1 --output .dev-data/evaluation/omni-run-1.json`
2. Runs 2 and 3, 16:12 to 16:18 UTC: `uv run --env-file ../.env python -m app.docintel.evaluate --reader omni --runs 2 --output .dev-data/evaluation/omni-runs-2-3.json`
3. The three runs in one report, with no model call: `uv run python -m app.docintel.evaluate --combine .dev-data/evaluation/omni-run-1.json .dev-data/evaluation/omni-runs-2-3.json --output .dev-data/evaluation/omni-3-runs.json`

Settings kept fixed. They are the settings of `OmniReader` of #18, unchanged, and the report records them:

- model `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`, at `https://integrate.api.nvidia.com/v1`
- `response_format` `json_schema` (strict), thinking off, temperature 0.2, `max_tokens` 2048
- 150 DPI, one call to classify (page 1), then one call per page
- at most 10 pages and 16 MP per image, timeout 90 s, 2 retries
- prompt version 1 (`EXTRACT` and `CLASSIFY` in `omni.py`)

The request bodies of all 198 calls held the same model, temperature, `max_tokens`, thinking and response format (`settings.sent`). `omni.py` did not change between the runs: git shows no change since #121, and runs 2 and 3 record its fingerprint `d84bcd22…bef4`. Run 1 was written before the harness recorded a fingerprint; `--combine` notes it in `combine_notes`.

**The four measures** (each run gives one value; the counts are added over the 3 runs):

| Measure | Mean | Lowest | Highest | Counts over the 3 runs |
|---|---|---|---|---|
| Field accuracy (right values / expected fields) | 84.8% | 84.8% | 84.8% | 336 right, **24 wrong**, **36 missing** of 396 |
| Strict document pass rate (every field right and on its page) | 48.6% | 48.6% | 48.6% | 51 of 105 |
| Classification (type right) | 88.6% | 88.6% | 88.6% | 93 of 105 |
| Line items (lines right / lines expected) | 72.6% | 72.6% | 72.6% | 159 of 219 |
| Source page right (of the values read) | 100% | 100% | 100% | 360 of 360 |
| Seconds per document | 5.45 | 5.35 | 5.50 | median 5.09, lowest 0.87, highest 12.05 |

Per run: 112 right, 8 wrong, 12 missing of 132 fields; 17 of 35 documents all right; 31 of 35 types right; 53 of 73 lines right. One run took 187 to 193 s.

**By document kind** (counts over the 3 runs; each kind gave the same accuracy in each run):

| Kind | Documents per run | Right | Wrong | Missing | Accuracy | All right | Lines right | Seconds per document |
|---|---|---|---|---|---|---|---|---|
| Clean | 26 | 231 | 18 | 33 | 81.9% | 33 of 78 | 159 of 159 | 4.99 |
| Scan | 6 | 69 | 6 | 0 | 92.0% | 12 of 18 | 0 of 42 | 7.82 |
| Phone photo | 3 | 36 | 0 | 3 | 92.3% | 6 of 9 | 0 of 18 | 4.69 |

**By document type** (counts over the 3 runs; the lowest and the highest run when they differ):

| Type | Documents per run | Accuracy | Right | Wrong | Missing | All right | Lines right | Seconds per document |
|---|---|---|---|---|---|---|---|---|
| cancellation_confirmation | 3 | 100% | 27 | 0 | 0 | 9 of 9 | 27 of 27 | 6.24 |
| repair_invoice | 4 | 100% | 60 | 0 | 0 | 12 of 12 | 24 of 48 | 7.24 |
| repair_quote | 2 | 96.7% (90% to 100%) | 29 | 0 | 1 | 5 of 6 | 24 of 24 | 7.01 |
| medical_invoice | 3 | 93.3% | 42 | 0 | 3 | 6 of 9 | 30 of 45 | 8.44 |
| receipt | 6 | 93.1% (91.7% to 95.8%) | 67 | 0 | 5 | 13 of 18 | 18 of 30 | 4.74 |
| luggage_report | 2 | 87.5% | 21 | 0 | 3 | 3 of 6 | | 3.48 |
| booking_confirmation | 5 | 80.0% | 48 | 0 | 12 | 3 of 15 | 36 of 45 | 7.74 |
| police_report | 2 | 75.0% | 18 | 6 | 0 | 0 of 6 | | 4.36 |
| medical_certificate | 2 | 66.7% | 12 | 6 | 0 | 0 of 6 | | 4.42 |
| medical_report | 2 | 50.0% | 12 | 12 | 0 | 0 of 6 | | 3.82 |
| damage_photo | 4 | 0% | 0 | 0 | 12 | 0 of 12 | | 1.10 |

**The weakest fields** (over the 3 runs):

| Field | Right | Wrong | Missing | What the report shows |
|---|---|---|---|---|
| `damage_photo.damage_description` | 0 of 12 | 0 | 12 | The reader does not read a damage photo (issue #25). `classify` also gives `unknown` for all 4 photos: these are the 12 type errors. |
| `medical_certificate.cause` | 0 of 6 | 6 | 0 | The diagnosis ("gastroenteritis with fever"), not the category `illness`. |
| `medical_report.diagnosis` | 0 of 6 | 6 | 0 | The right diagnosis with one more sentence ("… No fracture on the X-ray."). The text rule has no fuzzy match. |
| `medical_report.provider` | 0 of 6 | 6 | 0 | The name of the doctor, not the clinic. |
| `police_report.offence` | 0 of 6 | 6 | 0 | "Burglary (forced entry)", not `burglary`. |
| `booking_confirmation.booking_reference` | 3 of 15 | 0 | 12 | Refused on the 4 clean PDFs: the model gives the page title as its snippet (as in #18). Right on the scan, where no text layer can refuse it. |
| `receipt.amount_paid` | 13 of 18 | 0 | 5 | Missing on the trolley receipt in the 3 runs, and on the lock-set receipt in runs 2 and 3. |
| `medical_invoice.invoice_number` | 6 of 9 | 0 | 3 | Missing on the clean medical invoice of Lukas in each run. |
| `luggage_report.airline` | 3 of 6 | 0 | 3 | Missing on the phone photo in each run. |
| `repair_quote.total` | 5 of 6 | 0 | 1 | Missing on the quote of Anna in run 1 only. |

**Line items**: every line of the clean PDFs is right (159 of 159). No line is read on a scan or a photo (0 of 60): the reader keeps the lines only when it can confirm each one on the text layer of the page (D80 (6)), and a scan or a photo has none.

**HTTP**: 198 requests (66 per run: 35 to classify, 31 to read a page; a damage photo is not read), every answer 200. No 429, no other error status, no transport error, no reader error. One call at a time is about 20 requests per minute.

**Variation between runs**: the totals are the same in the 3 runs, but 2 of the 132 field results changed: `amount_paid` of the lock-set receipt (right in run 1, missing in runs 2 and 3) and `total` of the quote of Anna (missing in run 1, right in runs 2 and 3). The two changes cancel out in the totals. So the run-to-run variation is at least 2 fields of 132, and a change of #20 that moves the accuracy by one or two fields is not a gain.

**What the numbers say**:

- The 24 wrong values are all text fields where the answer key holds a short category or the clinic, and the model gives what the page prints. No amount, no date and no identifier was wrong. A wrong amount is the costly error, and there is none.
- Most of the 36 missing values are refused snippets (`booking_reference`) and documents that the reader does not read (damage photos). A missing value goes to a person.
- The strict pass rate (48.6%) is much lower than the field accuracy (84.8%): one weak field fails the whole document.
- Scans and photos have a better field accuracy than the clean PDFs (92% against 81.9%). With no text layer, the reader cannot refuse a snippet, and the source is a box of the whole page (D80 (3)). Their values are stored at confidence 0.5, so a person checks them.

**Limits of this measure**:

- Every document of the kit has one page, so "source page right" is 100% by construction here. The wrong page and an invalid source are tested only offline (`backend/tests/test_evaluate.py`).
- 35 mock documents, generated by our own script, with one layout per type: a real document is harder.
- Three runs in one afternoon, on one network; the endpoint load changes with the hour.
- Four of the ten weakest fields (`cause`, `offence`, `diagnosis`, `provider`) raise one question: which value is right, the text that the page prints or the value of the answer key (a category such as `illness` or `burglary`, a short diagnosis, the clinic and not the doctor). That is a question for #20 (field descriptions) and for Paul, not for the harness.
- The reports are in `backend/.dev-data/evaluation/` on the laptop, not in git. SHA-256: `omni-run-1.json` `4d1535ff…7227`, `omni-runs-2-3.json` `0e01a924…0a45`.

### Business numbers with the Omni reader (one run)

Date: 2026-11-18, 16:37 to 16:39 UTC. The page "Business numbers", button "Play the kit" (`POST /numbers/kit`, issue #96, D71): the 11 stories of the kit through the reader, the checks, the routing and the settlement, with no agent model. Same Omni settings as above, `CLAIMS_MODE=local`, `CLAIMS_READER=omni`. One run only.

How it was run: the backend app in one process (FastAPI `TestClient`, as `scripts/measure_agent.py` does), with the variables of `.env`, then one `POST /numbers/kit`. A script outside the repository put the HTTP counter of the harness on the client of `OmniReader`, and played the fixture reader first through the same function (`play_kit`). With the app running (`CLAIMS_READER=omni` in `.env`, `scripts/dev.sh`), the button "Play the kit" makes the same call.

| Reader | Outcomes right | Signal sets right | Amounts right | Seeded fraud caught | False alarms | Seconds | Model requests |
|---|---|---|---|---|---|---|---|
| Fixture (baseline) | 11 of 11 | 11 of 11 | 11 of 11 | 3 of 3 | 0 | < 1 | none |
| **Omni** | **10 of 11** | **8 of 11** | **10 of 11** | **3 of 3** | **0** | 128.3 | 52, all 200; no 429 |

The differences, story by story (the 7 other stories are the same as with the fixture reader):

| Story | Outcome (expected) | Signals (expected) | Amount (expected) | Cause, in the reading |
|---|---|---|---|---|
| `kit-anna-luggage/787` | `handler_review` (`straight_through`) | none (none) | CHF 398 (CHF 787) | `amount_paid` of the trolley receipt is missing (its snippet is refused, as in the harness). The documents then prove CHF 398 against CHF 787 claimed, so a person decides (D67). |
| `claim-2-property/3200` | `handler_review` (same) | R001 (none) | CHF 3,000 (same) | `classify` gives `unknown` for the damage photos, so the workflow sees no damage photo: R001 "A required document is missing". |
| `kit-anna-theft/2640` | `handler_review` (same) | R001 (none) | CHF 2,440 (same) | The same: the damage photo is `unknown`. |
| `kit-marco-bathroom/4740` | `investigation` (same) | R001, T001, T002, T003 (T001, T002, T003) | CHF 1,540 (same) | The same: the damage photo is `unknown`. |

What it says:

- No payment is wrong to the business: the one wrong amount is a proposal that goes to a person (`handler_review`), not a payment by the system. No amount was overpaid, every seeded fraud was caught, and there is no false alarm.
- The costs come from two errors that the field evaluation also shows: the damage photos (type `unknown`, issue #25 and #21) add R001 to 3 stories, and one missing amount sends a clean luggage claim to a person.
- One run only: the field evaluation shows 2 fields of 132 that change from run to run. One of them is the `total` of the quote of the story `kit-anna-theft`, which the settlement reads: it was read in this run (CHF 2,440 right), but it was missing in run 1 of the field evaluation. Another run can give other business numbers.
- The risk of #121 (a document with no field read is paid as complete) did not happen in this run.

## Tuning of the Omni reader (issue #20)

Run from 2026-11-18 to 2026-11-20 on the kit of 35 documents (132 expected fields), with the harness of #19: 2,718 requests in all. The method of each stage was committed before its first request, and an independent reviewer recomputed the numbers of each stage from the saved answers. The details of each stage (method, tables, checks) are in [`EVALUATION-ISSUE-20.md`](EVALUATION-ISSUE-20.md).

### The result

**Selected: the settings of #18 stay the defaults of `OmniReader`: prompt `v1`, 150 DPI, temperature 0.2, thinking off, `max_tokens` 2048, one call per page.**

No variant of resolution, temperature or reasoning beat these settings by the rules written in advance. Three prompts did beat `v1` in S2, but none was taken: `schema-only` accepted more invented values than `v1` in the safety check N1 (24 against 18 of 48, so `v1` fails it too), `example` and `described` were never checked by N1, and part of each S2 gain is the drift that S7 measured.

**The gain over #18 is 0: the selected settings are the settings of #18.** That is a valid result of a tuning: the first version was not beaten by any change that also passes the safety check. The final run S7 gave 114.67 right fields per run, against 112 for the #19 runs of 2026-11-18. The settings are the same, so this is a difference of time (see "Limits"), not a gain of tuning.

### How a variant was judged (the rules written before the runs)

- **One change at a time** against a control, 3 runs per variant on the full kit. The control of S2 was the 3 saved #19 runs (2026-11-18). The control of S3 to S5 was 3 new runs of the settings of #18 on 2026-11-20, the same day as the variants.
- **A variant replaces the control only when all of these are true** (means of the 3 runs):
  1. It gives at least 3 more right fields per run.
  2. Its lowest run is higher than the highest run of the control.
  3. The guardrails hold:
     - no wrong amount, date or identifier;
     - no more wrong values than the control;
     - the reading group loses 1 right field per run at most;
     - classification loses 1 document per run at most;
     - every line item of the clean PDFs stays right (53 of 53);
     - no reader error, and no call left without an answer;
     - at most 1.5 times the seconds per document of the control, or Paul accepts the cost.
  4. The gain stays above 0 when any one scenario is left out.
- When two variants win, the larger gain wins; at the same gain, the cheaper one.
- **No verdict** when a run is incomplete, a call got no answer after the retries, or a check of integrity fails (code, kit, settings, model).
- **Clarified for S5 and later, before the runs:** a call answered after an allowed retry is a success, and the retry is reported. The code of the rules also counted such a retry as an error; S2 to S4 were not judged again.
- **Three groups of fields** are reported apart:
  - **reading:** 117 fields;
  - **business category:** 11 fields (`cause`, `offence`, `diagnosis`, `provider`). The reader gives the text of the page where the answer key expects a category.
  - **not read:** the 4 damage photos (issue #25).

### One row per variant

Measured; mean of 3 runs, the 3 runs in brackets. Field accuracy = right fields of 132. Seconds are the mean seconds per document. The endpoint was faster on 2026-11-20, so seconds compare only within the same day.

| Variant | Date | Right of 132 (runs) | Field accuracy | Wrong | Missing | Strict pass of 35 | Classification of 35 | Clean line items of 53 | Seconds per document | Fields that change between runs |
|---|---|---|---|---|---|---|---|---|---|---|
| #19 baseline (#18 settings) | 2026-11-18 | 112.00 (112, 112, 112) | 84.8 % | 8.00 | 12.00 | 17.00 | 31.00 | 53.00 | 5.45 | 2 |
| S2 prompt `schema-only` | 2026-11-19 | 120.00 (120, 120, 120) | 90.9 % | 8.00 | 4.00 | 25.00 | 31.00 | 53.00 | 4.40 | 0 |
| S2 prompt `described` | 2026-11-19 | 115.33 (114, 115, 117) | 87.4 % | 4.00 | 12.67 | 19.33 | 31.00 | 53.00 | 4.66 | 7 |
| S2 prompt `example` | 2026-11-19 | 119.67 (122, 116, 121) | 90.7 % | 3.67 | 8.67 | 24.67 | 31.00 | 53.00 | 5.43 | 8 |
| S3 72 DPI | 2026-11-20 | 110.67 (110, 110, 112) | 83.8 % | 8.00 | 13.33 | 16.33 | 31.00 | 43.33 | 3.63 | 9 |
| S3 100 DPI | 2026-11-20 | 109.67 (110, 111, 108) | 83.1 % | 7.67 | 14.67 | 15.67 | 31.00 | 46.00 | 3.63 | 9 |
| S3 150 DPI (#18 settings, control of S3 to S5) | 2026-11-20 | 114.00 (114, 114, 114) | 86.4 % | 8.00 | 10.00 | 19.00 | 31.00 | 53.00 | 4.14 | 2 |
| S3 200 DPI | 2026-11-20 | 113.00 (110, 115, 114) | 85.6 % | 8.00 | 11.00 | 18.33 | 31.00 | 53.00 | 4.37 | 8 |
| S4 temperature 0 | 2026-11-20 | 112.33 (112, 113, 112) | 85.1 % | 8.67 | 11.00 | 17.33 | 31.00 | 53.00 | 4.71 | 3 |
| S4 temperature 0.3 | 2026-11-20 | 114.33 (115, 113, 115) | 86.6 % | 8.00 | 9.67 | 19.33 | 31.00 | 53.00 | 6.15 | 3 |
| S4 temperature 0.5 | 2026-11-20 | 110.00 (111, 110, 109) | 83.3 % | 8.00 | 14.00 | 18.00 | 31.00 | 53.00 | 5.92 | 2 |
| S5 thinking, budget 512 | 2026-11-20 | 113.67 (116, 113, 112) | 86.1 % | 8.00 | 10.33 | 22.00 | 31.67 | 52.00 | 9.21 | 18 |
| S5 thinking, budget 2048 | 2026-11-20 | 113.00 (117, 107, 115) | 85.6 % | 8.67 | 10.33 | 21.00 | 31.00 | 50.33 | 16.60 | 17 |
| S7 #18 settings (final) | 2026-11-20 | 114.67 (115, 114, 115) | 86.9 % | 8.00 | 9.33 | 19.67 | 31.00 | 53.00 | 4.40 | 1 |

Accuracy per kind of field (right fields over the 3 runs; per run, 23 amounts, 31 dates, 21 identifiers and 57 texts; the texts include the 11 business-category fields and the 4 damage descriptions):

| Variant | Amount | Date | Identifier | Text |
|---|---|---|---|---|
| #19 baseline (#18 settings) | 91 % | 100 % | 76 % | 77 % |
| S2 prompt `schema-only` | 100 % | 100 % | 100 % | 79 % |
| S2 prompt `described` | 91 % | 100 % | 83 % | 81 % |
| S2 prompt `example` | 99 % | 99 % | 98 % | 80 % |
| S3 72 DPI | 96 % | 100 % | 65 % | 77 % |
| S3 100 DPI | 90 % | 99 % | 76 % | 74 % |
| S3 150 DPI (#18 settings) | 96 % | 100 % | 76 % | 79 % |
| S3 200 DPI | 94 % | 100 % | 76 % | 78 % |
| S4 temperature 0 | 97 % | 100 % | 79 % | 74 % |
| S4 temperature 0.3 | 97 % | 100 % | 81 % | 77 % |
| S4 temperature 0.5 | 90 % | 97 % | 81 % | 74 % |
| S5 thinking, budget 512 | 94 % | 97 % | 97 % | 73 % |
| S5 thinking, budget 2048 | 94 % | 98 % | 97 % | 71 % |
| S7 #18 settings (final) | 99 % | 100 % | 81 % | 77 % |

Values are rounded half up from the exact means: for example 112 / 132 = 84.848 % gives 84.8 %, and 1.5 × 4.1353 s = 6.203 s gives 6.20 s.

### What each stage showed

**S2, the prompt** (against the #19 runs of another day):
- **Measured:** `schema-only` +8.00 right fields per run, `example` +7.67 and `described` +3.33 (narrowly). All three won by the rules; `schema-only` had the largest gain.
- **Estimate:** S7 later measured +2.67 per run for the settings of #18 between 2026-11-18 and 2026-11-20. If a drift of that size also touched S2, the gain of `schema-only` at the same time would be about +5 to +6, and the narrow win of `described` would not hold. This is an estimate, not a measure at the same time.
- **Not adopted:** `schema-only` failed N1 with more accepted invented values than `v1`; `example` and `described` were not checked by N1.
- **Which part of `schema-only` gives the gain is not known:** it drops four parts of `v1` at once. See "Correct values rejected at the check of the source" for what the later answers show.

**S3, the resolution** (against 150 DPI on the same day):
- 72 and 100 DPI lose 3.33 and 4.33 fields per run and fail two guardrails (the reading group and the line items of the clean PDFs). At 72 DPI the model reads characters wrong (`MZ5` for `MZS`); the check of the text layer refuses these values, so they are missing, not wrong.
- 200 DPI gives −1.00 per run, with more change between runs and 41 % more prompt tokens.
- **Measured:** every 200 DPI call used 584 image tokens fewer than its image needs, while one token formula fits all 340 other classify calls exactly. **Interpretation:** the server probably makes the image smaller, to about 186 DPI; which pixels the model sees is not known.

**S4, the temperature** (against 0.2, 2.4 hours earlier the same day): 0 gives −1.67 per run and one more wrong value; 0.3 gives +0.33, a difference that 3 runs cannot show; 0.5 gives −4.00 and fails the guardrail of the reading group. Temperature 0 did not make the runs identical (112, 113, 112).

**S5, reasoning** (thinking on, budgets 512 and 2048, against thinking off):
- −0.33 and −1.00 right fields per run; both fail the line items of the clean PDFs, and the seconds: 9.21 and 16.60 s per document against 4.14 (limit 6.20).
- Thinking made the runs much less stable: 18 and 17 fields change between runs, against 2.
- It gains on the PDFs with text (the snippets that the reader rejects without thinking pass) and loses about as much on scans and photos:
  - values with no snippet;
  - 3 answers that repeated themselves until `max_tokens`, each losing a page;
  - new wrong values on documents with no text layer.
- More reasoning was slower and less stable, and it did not meet the rules.

**S7, the final run of #18:** 114.67 right fields per run (115, 114, 115). By the drift rule of the method (each of right, wrong and missing within 2 of the #19 totals 112, 8, 12) there is drift: +2.67 and −2.67. The margin is small: the S3 control of the same morning was +2.00 against #19.

### Correct values rejected at the check of the source

A stored field must have a snippet that is printed on the page and holds the value (D80). From S3 on, the runs kept every answer of the model, so each missing field could be traced.

- **Verified (S3, S4, S5, S7):** the model returned null for 0 fields in scope.
  - At 150 DPI, the 10 missing fields per run are 4 damage photos (outside the scope) and **6 correct values that the reader refused**, because the snippet does not hold the value (for a booking reference the model gives the title "Booking confirmation") or is not on the text layer.
  - In every stage, most missing fields are of this kind. The exceptions are the misreads at 72 and 100 DPI and the pages lost in S5.
- **What this changes in S2:** S2 explained the missing booking references of `v1` as "the model most probably gave null". The later answers with the same settings do not support that: the model read each reference right, and the reader refused its snippet.
- **What cannot be established:** S2 kept no answers. So the cause of the missing fields in the S2 runs themselves, and which change of `schema-only` gave its gain, cannot be checked. Better snippets are the more probable explanation, not a proven one.
- **Why it matters:** the largest loss of the reader is at the check of the source, not in the reading. A change of the prompt or of the rule of the source might recover these fields; neither was tested in #20.

### N1: a safety check with absent values

The kit has no field that is expected to be empty, so it cannot see a value invented for an absent field. N1 tested that on 9 new mock documents (6 with a text layer, 3 scans or photos), 16 absent fields, 3 runs per prompt. The rule written in advance: 0 accepted incorrect values of 48.

- **Both prompts fail.** `v1` (the settings of #18) accepted **18 of 48**, `schema-only` **24 of 48**.
- **On text pages both prompts accept the same 12 values,** all true text of the page put in the wrong field:
  - a patient number as the invoice number;
  - a "valid until" date as the quote date;
  - the sentence "sent separately by e-mail" as the booking reference;
  - the name of a doctor as the clinic.

  The check of the source proves that the text is on the page, not that it belongs to the field. So these values get the confidence 0.9.
- **Why it matters:** the review limit of the routing is 0.8. A value at 0.9 does not go to a person, so such a claim can be paid with no human check.
- On scans and photos the values are stored at 0.5 and go to a person. `schema-only` also invents values and line items there; `v1` accepted 6 of 18 on scans and photos, `schema-only` 12 of 18.
- **Winning the tuning does not make the reader safe.** The fix is issue #125 (reader safeguards). The 9 documents and their generator go into git there.

### Limits

- **The kit:** 35 documents from one generator, about 11 layouts, no field expected empty. A result on the kit is not a result on real documents.
- **Time:** the hosted endpoint changes between periods, also within one day. With the same settings S7 differs from #19 by +2.67 fields per run, and the hosted model has no pinned version. S2 used a control of another day; S3 to S5 used a control of the same day but some hours earlier.
- **3 runs per variant** cannot show a rare event; reasoning made the runs vary much more.
- **One call per page or per document:** every document of the 35-document kit has one page, so the two ways of calling send the same request on this kit. The real comparison is deferred to issue #126, after the reader safeguards of issue #125.

### How to reproduce

- **In git:** the reader (`backend/app/docintel/omni.py`, with `OmniSettings`, the prompts and `config_id`), the harness (`backend/app/docintel/evaluate.py`), their tests, this section and `EVALUATION-ISSUE-20.md`. A test pins the request bodies of #18, so the default settings send the same requests as #18.
- **One variant, 3 runs:** `cd backend && uv run --env-file ../.env python -m app.docintel.evaluate --reader omni --runs 3 --omni dpi=100 --output .dev-data/evaluation/s3-dpi-100.json`. `--omni` takes `prompt`, `dpi`, `temperature`, `thinking`, `reasoning_budget`, `max_tokens` and `timeout`; the report records the settings, `config_id` and the tokens.
- **Not in git:** the runners of the stages (request limits, logs of every answer, replay) and the saved answers. They are in `backend/.dev-data/issue-20/` on Nikola's machine.
- **The numbers of a new run** will be close to these, not equal: the endpoint changes with time.

## Reader safeguards (issue #125)

The fix of the safety check N1 of #20: a value in the wrong field must not pass at confidence 0.9. Built and measured from 2026-11-24 to 2026-11-25. The design, every number and the checks are in [`EVALUATION-ISSUE-125.md`](EVALUATION-ISSUE-125.md).

### The result

**On fresh answers of the model (2026-11-25, 3 runs per configuration), the reader of #125 stores no incorrect value at 0.8 or more on N1, for the prompts `v1`, `described` and `example`, and it keeps every right field of the kit.** All 9 criteria written before the run pass.

| Set (3 runs) | Incorrect values at >= 0.8 (no person checks them) | Right fields (accuracy) | Right fields lost or downgraded |
|---|---|---|---|
| N1, `v1` + `described` + `example` | **35 -> 0** | 288 -> 298 | 0 |
| kit, `v1` | **18 -> 12** | 341 -> 341 of 396 | **0** |

- **Safety is not accuracy.** The accuracy of the kit is the same before and after; its safety is better: the 2 doctors stored as the clinic (in every run) now go to a person. The 12 kit values left at 0.9 are the 4 category fields per run (`cause`, `diagnosis` x2, `offence`), outside this issue.
- **How:** a new module `backend/app/docintel/labels.py`. A placeholder ("Not answerable", "null", "n/a" ...) is no value and is not stored. On a text page, a field keeps 0.9 only when the label before the value, where the snippet is, names the field (English, German, French, Italian); an identifier must not be a sentence; a provider must not read as one titled person. Else the field gets 0.5 with its value and its source (document, page, snippet) unchanged, and the routing that exists sends the claim to a person. No prompt, setting or request byte changed.
- **Before and after use the same answers.** The model was asked once (360 HTTP attempts, 0 retries, 0 failures); each run was then read by the reader of #20 (commit `0334e55`) and by the reader of #125, with the same request bytes. So the difference is the code alone, not the model or the day.
- **The safety measure is in the harness:** the report counts the incorrect values at `LOW_CONFIDENCE` or more (from `routing.py`), apart from the incorrect values at any confidence, and the right absences apart from the right values; `--set n1` evaluates N1 apart from the kit. A report of #19 or #20 shows "not available", never 0.

### Limits

- **N1 is a development set:** the rules were designed on its answers of 2026-11-19. The live answers are new, the documents are not. A held-out document set is future work.
- **Incorrect values stay stored, at 0.5:** a person checks them; nothing is rejected. Scans and photos have no text layer, so their fields stay at 0.5.
- **The confidence values are flags, not calibrated probabilities.** Some right values with an unusual label or layout get 0.5 too.
- **Paul confirms** that the paired replay meets the "control of the same day" of the issue.

### How to reproduce

- **In git (CI, no key):** `tests/test_n1_replay.py` and `tests/test_evaluation_safety.py` read the saved N1 answers of 2026-11-19 through a stub of the model.
- **A live measure:** `cd backend && uv run --env-file ../.env python -m app.docintel.evaluate --reader omni --runs 3 --set n1 --omni prompt=v1` (without `--set` for the kit).
- **Not in git:** the paired runner and the raw answers of 2026-11-25, in `backend/.dev-data/issue-125/live/` on Nikola's machine (see `EVALUATION-ISSUE-125.md`, section 8).

## One call per page or per document (issue #126)

Measured on 2026-11-26. **Result: the default stays one call per page.** One call per document stored more right fields on the test set, but it does not meet the rules written in advance in #20, and it stored one wrong value at 0.9. The method (committed before the first request), every number, the evidence and the limits are in [`EVALUATION-ISSUE-126.md`](EVALUATION-ISSUE-126.md). Paul has not reviewed this result.

### What was compared

- **Control: the page scope**, one model call per page. **Candidate: the document scope**, one call with every page image of a document (`--omni scope=document`), an experiment that is off by default. Its answer holds one block per page; the checks and the merge of the page scope read the blocks.
- **The set MP1** (`data/mock/mp1/`, `--set mp1`, not part of the kit): 8 mock documents of 2 or 3 pages, 19 pages, 6 text PDFs and 2 scans, 35 scored fields (2 of them absent), 39 line items (35 on the text PDFs).
- **Settings and runs:** the settings of #18 for both scopes (prompt `v1`, 150 DPI, temperature 0.2, thinking off, `max_tokens` 2048). 3 runs per scope in one invocation. 133 requests: 4 in a pilot on 2 other documents, 129 in the main run. Each got a 200 at its first attempt and ended with `stop`.

### The result (main run, 3 runs per scope)

| Measure | One call per page | One call per document |
|---|---|---|
| Right fields per run, of 35 | 25, 27, 24 (mean 25.33) | 28, 28, 28 |
| Field accuracy | 72.4 % (76 of 105) | 80.0 % (84 of 105) |
| Wrong values per run | 9, 8, 9 | 7, 7, 7 |
| **Wrong values at >= 0.8 per run** | 0, 0, 0 | **1, 1, 1** |
| Source page right, of the values stored (right and wrong) | 79.8 % (79 of 99) | 88.2 % (90 of 102) |
| of them: right values only / scans only | 70 of 73 / 16 of 21 | 78 of 81 / 12 of 21 |
| Line items right, text PDFs, of 35 per run | 23, 25, 25 | 28, 23, 29 |
| Seconds per document | 10.44 | 9.29 (-11 %; the run means overlap) |
| Requests per run | 27 | 16 (-41 %) |
| Tokens per run, prompt + completion | 68,573 + 6,016 | 63,296 + 5,714 (-7.5 %) |

The type was right for 8 of 8 documents in every run. The three document runs gave the same stored value, page and confidence for all 35 fields: repeatability on the same 8 documents, not a measure on other documents.

### The verdict

By the rules of "How a variant was judged" above, as written: **the control stays.**

- **Not met:** "at least 3 more right fields per run" (the gain is 2.67); "no wrong amount, date or identifier" (12 in the 3 document runs, all at 0.5; the page scope has 16); "every line item of the clean PDFs stays right" (28, 23, 29 of 35; neither scope stored a line item of one quote, so 35 was out of reach for both). The other seven rules are met.
- **Provisional:** the mapping of four rules (3c, 3e, 3f and 4) to MP1 waits for Paul. The verdict is the same under every reading that the method holds: rules 1 and 3a have no mapping, and each one alone gives "the control stays".

### What the answers show

- **Two effects make the gain, and this test does not separate them.** The page scope keeps the first value of a field, and MP1 prints lures on page 1 (a subtotal carried forward, a cost estimate): in 18 cases a later page answer held the right value. In the document scope the model saw all pages and wrote the final value itself.
- **The model copies values into every page block.** On a text page the checks refuse a copy. A copy can hide a conflict between two pages: in 5 fields the copied value was right, in 1 it was not. A name is printed with a date of birth on page 1 and alone on page 2. The page scope sees two values and stores 0.5; the document scope stores the first form at 0.9, in each of its 3 runs. This is the wrong value at >= 0.8 (wrong by the exact comparison with the answer key).
- **On a scan** the document scope stored one right value with a page that does not print it (in all 3 runs; one more in the pilot), at 0.5.
- **Line items** are scored by position; the scopes differ on one document only. Both stored "Brought forward" rows of a continued table as line items.
- **41 % fewer requests gave 11 % less time:** the page images are most of every request.

### Limits

- 8 mock documents from one generator, in English, 3 runs per scope on one day, one prompt. Each finding rests on 1 to 3 documents. The evaluation runs no claim. The raw answers are not in git (`EVALUATION-ISSUE-126.md`, R15).
- **A document that gives no field** looks like a document that prints nothing (D80 (13)), and such a claim can be paid with no person. No call failed here, so this experiment gives no new evidence. In the document scope one failed or cut call loses every page. This must be fixed in the base, or assessed by Paul, before the document scope can be a default.
