# claims-nvidia

## What this is

This is a demo claims platform for insurance, built on the NVIDIA stack.
A customer talks to a claim agent and uploads documents. This is the first panel.
The second panel shows the full pipeline live: reading, checks, fraud signals and routing.
The system never rejects a claim. A claim has three possible outcomes:
`straight_through` (validated with no review by a person), `handler_review` and `investigation`.

## Start here

The base is built: the full claim flow works, in three modes, and the demo runs on a VM. What is left is the document reading pipeline. Nikola builds it, in a fork of this repository, with pull requests.

| You want | Read |
|---|---|
| Your role, the method (fork, branch, pull request), each task in detail, how to work with a coding agent | `docs/NIKOLA.md` |
| How every part works, the NVIDIA technology under it, why we chose it, what you can challenge or redo | `docs/TECH.md` |
| The rules for every person and every agent | `AGENTS.md` |
| The whole system on one screen, with your parts in lime | The page **Map** of the app |

If you are a coding agent: read `AGENTS.md`, this file, `docs/NIKOLA.md` and `docs/TECH.md`, then the issue that you were given. Explain the claim flow and the issue before you write code.

The method in five lines:

1. Fork `PaulClement6/claims-nvidia` and clone your fork. Never commit on `main`.
2. One issue = one branch (`<issue number>-<short name>`) = one pull request to this repository, with `Closes #<number>`.
3. Write one failing test, make it pass, then refactor. Tests, types and lint pass before the pull request.
4. Every task ends with a number, and the pull request gives the command that produces it.
5. Paul or his coordinating agent reviews and merges.

## Run it in `local` mode

`local` mode needs no GPU, no sandbox and no key. CI runs in this mode.

1. Start the backend (port 8000):
   `cd backend && uv sync && uv run uvicorn app.main:app --reload`
2. In a second terminal, start the frontend:
   `cd frontend && npm install && npm run dev`
3. Open http://localhost:5173.

## Run it in `sandbox` mode

`sandbox` mode runs the same agent graph in an OpenShell sandbox, one per claim. It needs OpenShell 0.1.5 with a connected gateway, Docker (Colima on a Mac), and `NVIDIA_KEY` in `.env`.

1. Once: `infra/sandbox/setup.sh`. It builds the image `claims-agent:1`, and creates the provider `claims-nim` that holds the key. The key never enters a sandbox.
2. Start the backend on the loopback, with the model settings of `.env`:
   `cd backend && CLAIMS_MODE=sandbox BACKEND_PORT=8000 uv run uvicorn app.main:app --host 127.0.0.1 --port 8000`
   (`BACKEND_PORT` must be the port of uvicorn: the sandbox reaches the backend as `host.openshell.internal:<port>`.)
3. Start the frontend as in `local` mode. The sidebar shows the mode. The pipeline panel has a Sandbox section: the policy of the claim, the requests the sandbox denied, and a button "Run confinement test". It also shows the `sandbox` events: created, each turn with its seconds, deleted.

If a piece is missing, the backend stops at startup and names `infra/sandbox/setup.sh`. The demo reset deletes the sandboxes of this app. The tests that need OpenShell: `cd backend && uv run pytest -m sandbox` (the three claim files with the real model, and the confinement checks).

Checks (run each one before you open a pull request):

| Check | Backend (`cd backend`) | Frontend (`cd frontend`) |
|---|---|---|
| Tests | `uv run pytest` | `npm test` |
| Types | `uv run mypy .` | `npm run typecheck` |
| Lint | `uv run ruff check .` | `npm run lint` |

## Work on the VM

The demo runs on a Brev VM in `sandbox` mode. You can watch it at a public URL behind a password (ask Paul for both). You can also work on the VM, in a clone of your fork: `docs/DEPLOY.md`, section "For the intern", gives the steps.

## What is yours

You own the document reading pipeline in `backend/app/docintel/`.
It sits behind one contract, the `Reader` in `backend/app/docintel/reader.py`.
A reader is one class with a `name` and two methods:

- `classify(document, data)` returns a document type, or `"unknown"`. It never raises on unknown bytes.
- `extract(document, data, workflow)` returns an `Extraction`: fields, each with its source (document id, page, snippet).

The base ships a `FixtureReader`. It reads the `expected.json` of each mock file. It calls no model.
You write a second reader that reads the page image with a model. Start from this sketch:

```python
class OmniReader:
    name = "omni"

    def classify(self, document: Document, data: bytes) -> str:
        ...  # a DocumentType value, or "unknown"

    def extract(self, document: Document, data: bytes, workflow: str) -> Extraction:
        ...  # every field has document_id, page >= 1 and a snippet or box
```

1. Put your class in `backend/app/docintel/` (for example `omni.py`).
2. Register it in `get_reader()` in `reader.py`, next to `fixture`.
3. Switch to it: set `CLAIMS_READER=omni` in `.env`.
4. Prove it follows the contract: call `check_reader_contract(OmniReader())` from `backend/tests/reader_contract.py` in a test. It must pass.

What does not change when the reader changes: the checks, the fraud score, the routing and the UI.
They read only the `Extraction` that your reader returns.

## The model

The reader uses Nemotron 3 Nano Omni on the hosted NVIDIA endpoint.
It speaks OpenAI-compatible chat completions. You send the page image in the request.
`.env.example` holds the settings: `VLM_BASE_URL` and `VLM_MODEL`.

To get a free key:

1. Create a developer account on https://build.nvidia.com.
2. Generate an API key.
3. Copy `.env.example` to `.env`. Put the key in `.env` as `NVIDIA_KEY`.

`.env` is in `.gitignore`. Never commit a key.
The NVIDIA stack changes every month. Check the current documentation before you write code against an NVIDIA API.
`docs/READING-LIST.md` has the links to the model documentation.

## Your tasks

Open the page **Map** of the app first: it shows the whole system, and the lime boxes are yours. Each box gives its files and its issue.

| Issue | What to build | The number it gives |
|---|---|---|
| [#17](https://github.com/PaulClement6/claims-nvidia/issues/17) Pydantic schema per document type | One schema per document type of the kit | Every `expected.json` validates |
| [#18](https://github.com/PaulClement6/claims-nvidia/issues/18) Omni reader | Page image to JSON, with the source page of each field, behind the `Reader` contract | The rate limit of the free key (O15) |
| [#19](https://github.com/PaulClement6/claims-nvidia/issues/19) Evaluation harness | Compare the reader output to `expected.json`. Add a metrics page | Accuracy per field, pass rate, time per claim |
| [#20](https://github.com/PaulClement6/claims-nvidia/issues/20) Tuning | Test the prompt, the image resolution, one call per page or per document, reasoning on or off | A table of accuracy and time per variant |
| [#21](https://github.com/PaulClement6/claims-nvidia/issues/21) Classification with Omni | Replace the fixture classifier with Omni | Accuracy on the kit |
| [#22](https://github.com/PaulClement6/claims-nvidia/issues/22) Hard cases | Scans and phone photos. Test Nemotron Parse where Omni fails | The gain per document kind |
| [#62](https://github.com/PaulClement6/claims-nvidia/issues/62) Rails | NeMo Guardrails on what the agent reads: the customer message, the document text, the agent answer. A block sends the claim to a person | An injected document is blocked; false blocks on the kit; time added per turn |

Do #17, #18 and #19 first. The schemas, one reader and the measure make the other tasks testable.
Each issue has its own text: it is the contract. `docs/NIKOLA.md`, section 8, adds the reason, the steps, the experiments to try and the traps of each task.

## The test data

The mock files are in `data/mock/`. Each claim has its own folder, for example `data/mock/claim-1-travel/`.
Each document is a PDF with an `expected.json` next to it: the answer that your reader must find.
Example, from `cancellation-confirmation.expected.json`:

```json
{
  "document_type": "cancellation_confirmation",
  "fields": [
    {
      "name": "booking_reference",
      "value": "WPR-482913",
      "confidence": 1.0,
      "page": 1,
      "snippet": "WPR-482913"
    }
  ],
  "line_items": [{ "description": "Trip price paid", "amount": 1840.0 }]
}
```

To generate the documents again, run:
`cd backend && uv run python ../scripts/generate_mock_documents.py`
The script writes to `data/mock/`: the documents, their `expected.json`, the hard versions (scans, phone photos) and `index.json`. It gives the same bytes on every run on one machine (the scanned images may differ between a Mac and Linux, see D57 and `docs/LESSONS.md`).
To add a document or a scenario, read the header of `scripts/mockdocs/kit.py`.

## Rules that matter most

- Every extracted field carries its source: document and page. A field with no source is not stored.
- The repository holds mock data only. Never add a real document, a real name or real medical data.
- One ticket = one branch = one pull request. The pull request body says `Closes #<number>`.
- You work in a fork. Never commit on `main`. CI (`checks`) must be green before Paul or his coordinating agent merges.
- `AGENTS.md` holds the full rules. GitHub Copilot reads it too.

## Where to read more

| File | Content |
|---|---|
| `AGENTS.md` | Rules, commands, how to work |
| `docs/NIKOLA.md` | The guide of Nikola: role, method, tasks in detail |
| `docs/TECH.md` | How it works and why: components, NVIDIA technology, choices to challenge |
| `docs/PRODUCT.md` | The problem, the two panels, the demo |
| `docs/ARCHITECTURE.md` | Components, the agent and tools, the data model |
| `docs/WORKFLOWS.md` | The three claim workflows |
| `docs/MOCK-DATA.md` | Mock customers, policies and claim files |
| `docs/DECISIONS.md` | Decisions taken and decisions still open |
| `docs/READING-LIST.md` | NVIDIA documentation to read |
| `docs/EVALUATION.md` | The measures: method, tables, conclusions |
| `docs/DEPLOY.md` | The VM: services, install, update, access |
| `docs/LESSONS.md` | What went wrong and what we learned |
| `docs/BACKLOG.md` | All tasks, in order |
