# claims-nvidia

## What this is

This is a demo claims platform for insurance, built on the NVIDIA stack.
A customer talks to a claim agent and uploads documents. This is the first panel.
The second panel shows the full pipeline live: reading, checks, fraud signals and routing.
The system never rejects a claim. A claim has three possible outcomes:
`straight_through` (validated with no review by a person), `handler_review` and `investigation`.

## Run it in `local` mode

`local` mode needs no GPU, no sandbox and no key. CI runs in this mode.

1. Start the backend (port 8000):
   `cd backend && uv sync && uv run uvicorn app.main:app --reload`
2. In a second terminal, start the frontend:
   `cd frontend && npm install && npm run dev`
3. Open http://localhost:5173.

Checks (run each one before you open a pull request):

| Check | Backend (`cd backend`) | Frontend (`cd frontend`) |
|---|---|---|
| Tests | `uv run pytest` | `npm test` |
| Types | `uv run mypy .` | `npm run typecheck` |
| Lint | `uv run ruff check .` | `npm run lint` |

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

## Your six tasks

| Issue | What to build | The number it gives |
|---|---|---|
| [#17](https://github.com/PaulClement6/claims-nvidia/issues/17) Pydantic schema per document type | One schema per document type of the kit | Every `expected.json` validates |
| [#18](https://github.com/PaulClement6/claims-nvidia/issues/18) Omni reader | Page image to JSON, with the source page of each field, behind the `Reader` contract | The rate limit of the free key (O15) |
| [#19](https://github.com/PaulClement6/claims-nvidia/issues/19) Evaluation harness | Compare the reader output to `expected.json`. Add a metrics page | Accuracy per field, pass rate, time per claim |
| [#20](https://github.com/PaulClement6/claims-nvidia/issues/20) Tuning | Test the prompt, the image resolution, one call per page or per document, reasoning on or off | A table of accuracy and time per variant |
| [#21](https://github.com/PaulClement6/claims-nvidia/issues/21) Classification with Omni | Replace the fixture classifier with Omni | Accuracy on the kit |
| [#22](https://github.com/PaulClement6/claims-nvidia/issues/22) Hard cases | Scans and phone photos. Test Nemotron Parse where Omni fails | The gain per document kind |

Do #17, #18 and #19 first. The schemas, one reader and the measure make the other tasks testable.
Each issue has its own text. Read it before you start.

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
The script writes to `data/mock/`. It gives the same bytes on every run.
To add a document, read the header of `scripts/generate_mock_documents.py`.

## Rules that matter most

- Every extracted field carries its source: document and page. A field with no source is not stored.
- The repository holds mock data only. Never add a real document, a real name or real medical data.
- One ticket = one branch = one pull request. The pull request body says `Closes #<number>`.
- Never push to `main`. CI must be green before the human merges.
- `AGENTS.md` holds the full rules. GitHub Copilot reads it too.

## Where to read more

| File | Content |
|---|---|
| `AGENTS.md` | Rules, commands, how to work |
| `docs/PRODUCT.md` | The problem, the two panels, the demo |
| `docs/ARCHITECTURE.md` | Components, the agent and tools, the data model |
| `docs/WORKFLOWS.md` | The three claim workflows |
| `docs/MOCK-DATA.md` | Mock customers, policies and claim files |
| `docs/DECISIONS.md` | Decisions taken and decisions still open |
| `docs/READING-LIST.md` | NVIDIA documentation to read |
| `docs/BACKLOG.md` | All tasks, in order |
