"""OmniReader (issue #18): reads a document with Nemotron 3 Nano Omni.

Each full page goes to the model as an image. The answer follows the schema of
the type (issue #17) and gives, for each value, the text it was read from.

One call per page is the default. `scope="document"` (issue #126, an experiment)
sends all the pages of a document in one call. The answer then holds one block per
page, and the checks and the merge of the page mode read the blocks.
"""

import base64
import hashlib
import json
import logging
import math
import os
import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date
from io import BytesIO
from itertools import islice
from typing import Any, NamedTuple

import httpx2 as httpx
import pypdfium2 as pdfium  # type: ignore[import-untyped]
from PIL import Image
from pydantic import BaseModel, ValidationError
from pypdf import PdfReader

from app.docintel import labels
from app.docintel.schemas import SCHEMAS
from app.models import Document, DocumentType, ExtractedField, Extraction, Source
from app.workflows import WORKFLOWS

log = logging.getLogger(__name__)  # status codes only: never a value or a snippet

DPI = 150
# Bounds (D80): a document may be long or a page huge, the work stays bounded. A
# document with more pages is read in part, and a person checks it.
MAX_PAGES = 10  # pages rendered and sent per document: at most 1 + 10 model calls
MAX_PIXELS = 16_000_000  # per image sent; A4 at 150 DPI is 2.2 MP
# Issue #126, a guess like the two bounds above, not measured: the image bytes of one
# document call. 10 scanned A4 pages are about 13 MB. A page that does not fit is not
# sent and counts as not read, like a page after MAX_PAGES.
MAX_DOCUMENT_BYTES = 24_000_000
TIMEOUT = 90.0  # seconds, per model call
MAX_TOKENS = 2048
MAX_TOKENS_LIMIT = 65536  # the largest max_tokens that the NIM API takes
RETRIES = 2  # after a timeout, a 429 or a 5xx
WAITS = (2.0, 8.0)  # seconds before each retry, when no Retry-After
MAX_WAIT = 30.0
MAGIC = {b"%PDF-": "application/pdf", b"\x89PNG\r\n\x1a\n": "image/png"}
MAGIC[b"\xff\xd8\xff"] = "image/jpeg"
KNOWN = {t.value for t in DocumentType}
# Provisional, not calibrated (D80): they say on which side of the routing threshold
# (0.8) a field falls. Checked evidence passes; the model alone needs a person.
VERIFIED = 0.9  # the snippet is on the text layer of the page and holds the value
UNVERIFIED = 0.5  # no text layer: only the model says where the value is
MAX_SNIPPET = 120  # characters; a longer "snippet" (a paragraph) proves nothing
# unknown has no schema; the description of a damage photo is issue #25
NOTHING_TO_READ = {DocumentType.unknown, DocumentType.damage_photo}
EXTRACT = """Read the whole image. It is page {page} of {pages} of a {kind}.
Answer with JSON only, in this form: {{"values": {{...}}, "snippets": {{...}}}}.
`values` follows the JSON schema below. Every key is required. Use null for a \
value that is not on this page: never guess.
Dates are YYYY-MM-DD. Amounts are plain numbers: no currency, no thousands separator.
`line_items` lists every line of the table on this page, or [] when there is none.
For each value that is not null, `snippets` maps its key to the exact text of the \
page that shows it, copied character by character, as short as possible.
JSON schema of `values`:
{schema}"""
# Issue #20: the prompt with only the form of the answer. The rule of the snippets
# is the rule of v1, word for word: with no snippet, no field has a source and none
# is stored (non-negotiable 3). The variant drops only the rules of the values.
EXTRACT_SCHEMA_ONLY = """Page {page} of {pages} of a {kind}.
Answer with JSON: {{"values": {{...}}, "snippets": {{...}}}}. `values` follows the \
JSON schema below.
For each value that is not null, `snippets` maps its key to the exact text of the \
page that shows it, copied character by character, as short as possible.
{schema}"""
_CAUSES = sorted({c for w in WORKFLOWS.values() for c in w.all_causes()})
# Issue #20: what each field means, from the business (docs/WORKFLOWS.md, the causes
# of the workflows, Paul on pull request #122: `cause` is a category, `diagnosis` is
# short, `provider` is the clinic), never from a value of the kit. A test checks
# that no description holds a value of an answer key, apart from the causes.
DESCRIPTIONS = {
    "booking_reference": "The reference of the booking, as the travel company "
    "prints it.",
    "traveller_name": "The full name of the person who travels.",
    "departure_date": "The date when the trip starts.",
    "trip_price": "The total price of the trip that the traveller paid.",
    "cancellation_date": "The date when the trip was cancelled.",
    "refund_received": "The amount that the travel company pays back to the "
    "traveller after the cancellation.",
    "patient_name": "The full name of the patient.",
    "cause": "The cause of the claim, as one category of the insurance: "
    + ", ".join(_CAUSES[:-1])
    + f" or {_CAUSES[-1]}. Not the medical details.",
    "unfit_from": "The first day when the patient is not fit to travel or to work.",
    "diagnosis": "The diagnosis in short: the name of the illness or the injury "
    "only, with no findings, tests or treatment.",
    "treatment_date": "The date of the treatment.",
    "provider": "The clinic, practice or hospital that gives the treatment. Not "
    "the name of a doctor.",
    "invoice_number": "The number of the invoice, as printed.",
    "invoice_date": "The date of the invoice.",
    "quote_number": "The number of the quote, as printed.",
    "quote_date": "The date of the quote.",
    "repair_company": "The name of the company that does the repair.",
    "customer_name": "The full name of the customer.",
    "total": "The total amount to pay for all the lines.",
    "merchant": "The name of the shop that sold the items.",
    "purchase_date": "The date of the purchase.",
    "item_description": "What was bought, in a few words.",
    "amount_paid": "The total amount that the customer paid.",
    "line_items": "Every line of the table of items or costs.",
    "description": "The text of the line.",
    "quantity": "The number of units.",
    "unit_price": "The price of one unit.",
    "amount": "The amount of the line, with its sign as printed.",
    "report_number": "The number of the police report, as printed.",
    "incident_date": "The date when the event of the report happened.",
    "offence": "The kind of offence that the police recorded, as a short general "
    "legal term, with no details of how it happened.",
    "report_reference": "The reference of the report of the airline, as printed.",
    "airline": "The name of the airline.",
    "damage_description": "What is damaged and how, in a few words.",
}
# Issue #20: one worked example, text only, for a document that is not in the kit.
# Every name, number and date is invented. Tests check that no name, no number of
# 4 characters or more and no run of 3 words of it is in a text of the kit.
EXAMPLE_TEXT = """Quellmatt Dental Care GmbH, Quellmattstrasse 27, 6205 Eich
Invoice QM-30741 | Date 09.03.2026
Patient: Livia Zwyssig | Dentist: Dr. med. dent. Tobias Wyrsch
Dental check-up 1 x 64.20 64.20
Panoramic image 2 x 23.45 46.90
Total CHF 111.10"""
EXAMPLE_ANSWER: dict[str, Any] = {
    "values": {
        "invoice_number": "QM-30741",
        "invoice_date": "2026-03-09",
        "provider": "Quellmatt Dental Care GmbH",
        "patient_name": "Livia Zwyssig",
        "total": 111.1,
        "line_items": [
            {
                "description": "Dental check-up",
                "quantity": 1.0,
                "unit_price": 64.2,
                "amount": 64.2,
            },
            {
                "description": "Panoramic image",
                "quantity": 2.0,
                "unit_price": 23.45,
                "amount": 46.9,
            },
        ],
    },
    "snippets": {
        "invoice_number": "Invoice QM-30741",
        "invoice_date": "Date 09.03.2026",
        "provider": "Quellmatt Dental Care GmbH",
        "patient_name": "Patient: Livia Zwyssig",
        "total": "Total CHF 111.10",
    },
}
EXAMPLE_END = (
    "This was an example only. Now answer for the image of this request, with the "
    "JSON schema above."
)
EXAMPLE = (
    "\n\nAn example, for another document (a medical invoice, not this page).\n"
    f"The text of its page:\n{EXAMPLE_TEXT}\nThe answer for it:\n"
    + json.dumps(EXAMPLE_ANSWER)
    + f"\n{EXAMPLE_END}"
)
CLASSIFY = (
    "What kind of document is this page? Answer with JSON only: "
    '{"document_type": "<type>"}. The type is one of: '
    + ", ".join(t.value for t in DocumentType)
    + '. Answer "unknown" when it is none of them.'
)
CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {"document_type": {"type": "string", "enum": sorted(KNOWN)}},
    "required": ["document_type"],
    "additionalProperties": False,
}
PROMPTS = ("v1", "schema-only", "described", "example")
# Issue #126: what one extraction call holds. `page` is #18: one call per page.
# `document`: one call with every page image and one block per page in the answer.
SCOPES = ("page", "document")
# The prompt of a document call. The rules of the values and of the snippets are
# those of EXTRACT (v1), word for word, with "that page" for "this page": the scope
# of the call is the only thing that the comparison of #126 changes.
EXTRACT_DOCUMENT = """Read every image. {shown} of one {kind}, in order: the first \
image is page 1.
Answer with JSON only, in this form: {{"page_1": {{"values": {{...}}, "snippets": \
{{...}}}}, "page_2": {{...}}}}, with one entry for each image.
For each page, `values` follows the JSON schema below. Every key is required. Use \
null for a value that is not on that page: never guess.
Dates are YYYY-MM-DD. Amounts are plain numbers: no currency, no thousands separator.
`line_items` lists every line of the table on that page, or [] when there is none.
For each value that is not null, `snippets` maps its key to the exact text of that \
page that shows it, copied character by character, as short as possible.
JSON schema of `values`:
{schema}"""
ALL_PAGES = "They are the {pages} pages"
FIRST_PAGES = "They are the first {sent} of the {pages} pages"  # a document cut short
PAGE_MARK = "Page {page} of {pages}:"  # the text part in front of each page image


@dataclass(frozen=True)
class OmniSettings:
    """What the requests of the reader hold (issue #20). The defaults are the
    settings of #18 (`ISSUE_18`): with them the requests are those of #18, byte for
    byte. DPI, temperature and thinking apply to both calls (classify and page).
    `prompt` is the prompt of a page call: `v1` (#18), `schema-only` (only the
    form of the answer), `described` (`v1` with a description of each field) or
    `example` (`described` with one worked example). `scope` (issue #126) is what
    one extraction call holds: `page` (#18) or `document` (every page of a document
    of two pages or more in one call; only with the prompt `v1`)."""

    dpi: int = DPI
    temperature: float = 0.2
    max_tokens: int = MAX_TOKENS  # thinking included: the budget is part of it
    thinking: bool = False
    reasoning_budget: int | None = None  # tokens of thinking; only with thinking on
    timeout: float = TIMEOUT
    prompt: str = "v1"
    scope: str = "page"

    def __post_init__(self) -> None:
        # A value of another type would make another body or another config id for
        # one setting (150.0 for 150), or turn thinking on ("off" is true).
        budget = self.reasoning_budget
        wholes = (self.dpi, self.max_tokens, 1 if budget is None else budget)
        reals = (self.temperature, self.timeout)
        if (
            any(type(v) is not int for v in wholes)
            or any(type(v) not in (int, float) for v in reals)
            or type(self.thinking) is not bool
            or type(self.prompt) is not str
            or type(self.scope) is not str
        ):
            raise TypeError(
                "dpi, max_tokens and reasoning_budget are int, temperature and "
                "timeout are numbers, thinking is a bool, prompt and scope are str"
            )
        # 0 and 0.0 are one setting: one body, one config id
        object.__setattr__(self, "temperature", float(self.temperature))
        object.__setattr__(self, "timeout", float(self.timeout))
        problems = [
            (not 50 <= self.dpi <= 300, "dpi must be 50 to 300"),
            (not 0 <= self.temperature <= 2, "temperature must be 0 to 2"),
            (
                not 1 <= self.max_tokens <= MAX_TOKENS_LIMIT,
                f"max_tokens must be 1 to {MAX_TOKENS_LIMIT}",
            ),
            (
                not (math.isfinite(self.timeout) and self.timeout > 0),
                "timeout must be a finite number more than 0",
            ),
            (self.prompt not in PROMPTS, f"prompt must be one of {', '.join(PROMPTS)}"),
            (self.scope not in SCOPES, f"scope must be one of {', '.join(SCOPES)}"),
            (
                self.scope == "document" and self.prompt != "v1",
                "scope document needs the prompt v1",
            ),
            (budget is not None and not self.thinking, "a budget needs thinking on"),
            (
                budget is not None and not 0 < budget < self.max_tokens,
                "reasoning_budget must be 1 or more and less than max_tokens",
            ),
        ]
        found = [text for wrong, text in problems if wrong]
        if found:
            raise ValueError("; ".join(found))


ISSUE_18 = OmniSettings()


def values_schema(schema: type[BaseModel], described: bool = False) -> dict[str, Any]:
    """The JSON schema of the values of a type (#17); with `described`, each field
    has its description (#20). A description changes only the text sent: the answer
    is still validated by the schema of #17 as it is."""
    values = schema.model_json_schema()
    if described:
        for part in [values, *values.get("$defs", {}).values()]:
            for name, field in part["properties"].items():
                field["description"] = DESCRIPTIONS[name]
    return values


def answer_schema(schema: type[BaseModel], described: bool = False) -> dict[str, Any]:
    """The JSON schema of a page answer: the values of the type (#17) and a
    snippet per value. `$defs` move to the root, where `#/$defs/...` resolves."""
    values = values_schema(schema, described)
    defs = values.pop("$defs", None)
    answer: dict[str, Any] = {
        "type": "object",
        "properties": {
            "values": values,
            "snippets": {"type": "object", "additionalProperties": {"type": "string"}},
        },
        "required": ["values", "snippets"],
        "additionalProperties": False,
    }
    if defs:
        answer["$defs"] = defs
    return answer


def page_prompt(
    prompt: str, kind: str, page: int, pages: int, values: dict[str, Any]
) -> str:
    """The text of a page call for a prompt of `PROMPTS`; `v1` is the text of #18."""
    template = EXTRACT_SCHEMA_ONLY if prompt == "schema-only" else EXTRACT
    text = template.format(page=page, pages=pages, kind=kind, schema=json.dumps(values))
    return text + EXAMPLE if prompt == "example" else text


def document_schema(schema: type[BaseModel], pages: int) -> dict[str, Any]:
    """The JSON schema of a document answer (issue #126): one page answer per page
    sent, under the keys `page_1` to `page_<pages>`. Every key is required and no
    other key is allowed, so the form names each page once."""
    page = answer_schema(schema)
    defs = page.pop("$defs", {})
    names = [f"page_{n}" for n in range(1, pages + 1)]
    return {
        "type": "object",
        "properties": {name: {"$ref": "#/$defs/page"} for name in names},
        "required": names,
        "additionalProperties": False,
        "$defs": {"page": page} | defs,
    }


def document_prompt(kind: str, sent: int, pages: int, values: dict[str, Any]) -> str:
    """The text of a document call: `sent` page images of a document of `pages`
    pages (fewer are sent only when the document is over a bound)."""
    shown = ALL_PAGES if sent == pages else FIRST_PAGES
    return EXTRACT_DOCUMENT.format(
        shown=shown.format(sent=sent, pages=pages), kind=kind, schema=json.dumps(values)
    )


def prompt_sha256(prompt: str, scope: str = "page") -> str:
    """SHA-256 of every text that a prompt of `PROMPTS` sends, apart from the
    schemas of #17: the classify prompt, the page template and, when the prompt
    uses them, the descriptions and the example. With the scope `document`, also
    the texts of a document call (a document of one page still uses the page
    template)."""
    parts = [CLASSIFY, EXTRACT_SCHEMA_ONLY if prompt == "schema-only" else EXTRACT]
    if prompt in ("described", "example"):
        parts.append(json.dumps(DESCRIPTIONS, sort_keys=True))
    if prompt == "example":
        parts.append(EXAMPLE)
    if scope == "document":
        parts += [EXTRACT_DOCUMENT, ALL_PAGES, FIRST_PAGES, PAGE_MARK]
    return hashlib.sha256("\n\0\n".join(parts).encode()).hexdigest()


def config_id(settings: OmniSettings) -> str:
    """A short name of a configuration: the same settings and prompt texts give the
    same id. The model and the endpoint are recorded apart in the report. The scope
    `page` is left out, so a configuration of #18 to #125 keeps the id that the
    saved reports hold."""
    named = asdict(settings)
    named["prompt_sha256"] = prompt_sha256(settings.prompt, settings.scope)
    if settings.scope == "page":
        del named["scope"]
    return hashlib.sha256(json.dumps(named, sort_keys=True).encode()).hexdigest()[:12]


class Page(NamedTuple):
    image: bytes
    mime: str
    size: tuple[int, int]  # pixels of the image sent
    text: str  # the text layer of the page; "" for a scan or a photo


def _fit(width: float, height: float) -> float:
    """The factor (at most 1) that keeps an image within MAX_PIXELS, even when
    each side is rounded up: (width * f + 1) * (height * f + 1) <= MAX_PIXELS."""
    area, sides = max(width * height, 1.0), width + height
    root = math.sqrt(sides * sides + 4 * area * (MAX_PIXELS - 1))
    return min(1.0, (root - sides) / (2 * area))


def pages(data: bytes, first: int | None = None, dpi: int = DPI) -> list[Page]:
    """The first pages (MAX_PAGES by default) as whole images of at most MAX_PIXELS,
    or [] when the bytes are not a readable PDF or image. `dpi` renders a PDF page;
    a photo is sent as it is."""
    first = MAX_PAGES if first is None else first
    mime = next((m for head, m in MAGIC.items() if data.startswith(head)), None)
    try:
        if mime == "application/pdf":
            pdf_pages = islice(PdfReader(BytesIO(data)).pages, first)
            texts = [p.extract_text() or "" for p in pdf_pages]
            pdf = pdfium.PdfDocument(data)
            try:
                found = []
                for i in range(min(len(pdf), first)):
                    width, height = pdf[i].get_size()  # points
                    scale = dpi / 72 * _fit(width * dpi / 72, height * dpi / 72)
                    image = pdf[i].render(scale=scale).to_pil()
                    png = BytesIO()
                    image.save(png, "PNG")
                    text = texts[i] if i < len(texts) else ""
                    found.append(Page(png.getvalue(), "image/png", image.size, text))
                return found
            finally:
                pdf.close()
        if mime is not None:
            with Image.open(BytesIO(data)) as image:
                size = image.size
                image.verify()
            factor = _fit(*size)
            if factor == 1.0:
                return [Page(data, mime, size, "")]
            # Pillow refuses to open a decompression bomb (above about 179 MP)
            with Image.open(BytesIO(data)) as image:
                image.thumbnail((int(size[0] * factor), int(size[1] * factor)))
                small = BytesIO()
                image.convert("RGB").save(small, "JPEG", quality=90)
                return [Page(small.getvalue(), "image/jpeg", image.size, "")]
    except Exception:  # pdfium, pypdf and Pillow raise many kinds of errors
        return []
    return []


def _length(data: bytes) -> int:
    """The number of pages of a document that `pages` could read."""
    if not data.startswith(b"%PDF-"):
        return 1
    pdf = pdfium.PdfDocument(data)
    try:
        return len(pdf)
    finally:
        pdf.close()


def _json(content: str | None, pairs: Callable[[list[Any]], Any] | None = None) -> Any:
    """The JSON of an answer, or None. One ```json fence around the whole answer
    is removed; nothing else is repaired. `pairs` gets the (key, value) pairs of
    each object, the outer object last."""
    if content is None:
        return None
    text = content.strip()
    if text.startswith("```"):
        first, _, rest = text.partition("\n")
        if first not in ("```", "```json") or not rest.rstrip().endswith("```"):
            return None
        text = rest.rstrip().removesuffix("```")
    try:
        return json.loads(text, object_pairs_hook=pairs)
    except (ValueError, RecursionError):  # also an answer nested too deep to read
        return None


def _blocks(content: str | None, count: int) -> tuple[list[Any], bool]:
    """The block of each of the `count` pages of a document answer (issue #126), in
    page order, and whether the answer names these pages and nothing else.

    A block is found by its key, `page_1` to `page_<count>`: the order of the keys
    does not count. A page with no block gets None: nobody read that page. A key
    that is not one of these pages (`page_0`, `page_9`, `pages`) is never read as a
    page and makes the answer incomplete. No page number is made up, moved into the
    range or guessed. An answer that is not an object, or that gives one page key
    twice (which of the two is the page?), gives no block at all."""
    twice: list[bool] = []

    def unique(found: list[Any]) -> dict[Any, Any]:
        twice.append(len({key for key, _ in found}) != len(found))
        return dict(found)

    answer = _json(content, unique)
    if not isinstance(answer, dict) or twice[-1]:  # the outer object comes last
        return [None] * count, False
    names = [f"page_{n}" for n in range(1, count + 1)]
    return [answer.get(name) for name in names], set(answer) == set(names)


Answer = tuple[BaseModel, dict[str, Any], bool]  # what `_read` gives for one page


def _read(schema: type[BaseModel], answer: Any) -> Answer | None:
    """(values, snippets, line items refused) of a page answer, or None when the
    answer as a whole cannot be trusted: not JSON, not the asked form, or no valid
    values even after the failing keys are dropped."""
    if not isinstance(answer, dict):
        return None
    values, snippets = answer.get("values"), answer.get("snippets")
    if not isinstance(values, dict) or not isinstance(snippets, dict):
        return None
    try:
        return schema.model_validate_json(json.dumps(values)), snippets, False
    except ValidationError as e:
        bad = {str(err["loc"][0]) for err in e.errors() if err["loc"]}
    # A key that fails is dropped, never converted: "1840" stays unread. An
    # unknown key is not stored and is never mapped to a known one.
    kept = {
        k: v for k, v in values.items() if k in schema.model_fields and k not in bad
    }
    for name in schema.model_fields:
        kept.setdefault(name, [] if name == "line_items" else None)
    try:
        return (
            schema.model_validate_json(json.dumps(kept)),
            snippets,
            "line_items" in bad,
        )
    except ValidationError:
        return None


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


# A number: "1,840.00", "1'840.00", "-1,500.00". A separator stands between two
# digits; a minus is a sign only where no letter or digit comes before it, so
# "WPR-482913" and "2026-08-04" hold no negative number.
NUMBER = re.compile(r"(?:(?<!\w)[-−])?\d(?:[',]?\d)*(?:\.\d+)?")


def _numbers(text: str) -> list[float]:
    """The numbers of a text."""
    found = NUMBER.findall(text)
    return [float(n.replace(",", "").replace("'", "").replace("−", "-")) for n in found]


def _holds(text: str, value: Any) -> bool:
    """Does this text show the value? A number to the cent, a date as YYYY-MM-DD or
    DD.MM.YYYY, a text with no difference of case or spaces."""
    if isinstance(value, bool):
        return False
    if isinstance(value, date):
        return value.isoformat() in text or value.strftime("%d.%m.%Y") in text
    if isinstance(value, int | float):
        return any(abs(n - value) < 0.005 for n in _numbers(text))
    return _norm(str(value)) in _norm(text)


def _shown(snippet: str, text: str) -> bool:
    """Is the snippet on the page, with every number of the page that it touches
    whole? "250.00" is not shown by a page that only has "1,250.00", nor
    "1,500.00" by "-1,500.00"."""
    needle, page = _norm(snippet), _norm(text)
    if not needle:
        return False
    spans = [m.span() for m in NUMBER.finditer(page)]
    start = page.find(needle)
    while start != -1:
        end = start + len(needle)
        if all(start <= a and b <= end for a, b in spans if a < end and b > start):
            return True
        start = page.find(needle, start + 1)
    return False


def _on_page(line: dict[str, Any], text: str) -> bool:
    """Is every value of a line item on the text layer of the page? A page with no
    text layer confirms nothing."""
    return bool(text.strip()) and all(
        _holds(text, v) for v in line.values() if v is not None
    )


def _field(
    document_id: str,
    number: int,
    page: Page,
    name: str,
    value: Any,
    snippets: dict[str, Any],
    kind: str = "",
) -> ExtractedField | None:
    """The field with its source, or None when its source cannot be given.

    With a text layer, the snippet must be on the page and hold the value: it is
    stored as checked evidence. The words in front of the value must also fit the
    field (#125): if not, value and source stay and a person checks it. With no text
    layer (a scan, a photo), nothing can check the snippet: the source is the whole
    page (a box) and a person checks it. `kind` is the type of the document.
    """
    snippet = snippets.get(name)
    if not isinstance(snippet, str) or not 0 < len(snippet.strip()) <= MAX_SNIPPET:
        return None
    if not _holds(snippet, value):  # the answer contradicts itself
        return None
    if page.text.strip():
        if not _shown(snippet, page.text):  # an invented source
            return None
        source = Source(document_id=document_id, page=number, snippet=snippet.strip())
        fits = labels.verified(kind, name, value, snippet, page.text)
        confidence = VERIFIED if fits else UNVERIFIED
    else:
        width, height = page.size
        box = (0.0, 0.0, float(width), float(height))
        source = Source(document_id=document_id, page=number, box=box)
        confidence = UNVERIFIED
    stored = value.isoformat() if isinstance(value, date) else value
    return ExtractedField(name=name, value=stored, confidence=confidence, source=source)


def _retry_after(header: str | None, default: float) -> float:
    try:
        seconds = float(header or "")
    except ValueError:
        return default
    if math.isnan(seconds):  # "nan" is a float: min and max would keep it
        return default
    return min(max(seconds, 0.0), MAX_WAIT)


class OmniReader:
    name = "omni"

    def __init__(
        self,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        settings: OmniSettings = ISSUE_18,
    ) -> None:
        env = os.environ
        for var in ("NVIDIA_KEY", "VLM_BASE_URL", "VLM_MODEL"):
            if not env.get(var):
                raise RuntimeError(f"CLAIMS_READER=omni needs {var} (see .env.example)")
        self.model = env["VLM_MODEL"]
        self._url = env["VLM_BASE_URL"].rstrip("/") + "/chat/completions"
        self._headers = {"Authorization": f"Bearer {env['NVIDIA_KEY']}"}
        self.settings = settings
        self._client = client or httpx.Client(timeout=settings.timeout)
        self._sleep = sleep
        self._types: dict[str, str] = {}  # SHA-256 of the bytes -> type

    def classify(self, document: Document, data: bytes) -> str:
        """From page 1. Only a type the model gave is kept, so a failure is retried
        on the next call; `extract` reuses the type, so both agree."""
        digest = hashlib.sha256(data).hexdigest()
        if digest in self._types:
            return self._types[digest]
        found = pages(data, first=1, dpi=self.settings.dpi)
        if not found:
            return DocumentType.unknown.value
        answer = _json(self._post(CLASSIFY, found[0], CLASSIFY_SCHEMA))
        kind = answer.get("document_type") if isinstance(answer, dict) else None
        if kind not in KNOWN:
            return DocumentType.unknown.value
        self._types[digest] = str(kind)
        return str(kind)

    def _post(self, prompt: str, page: Page, schema: dict[str, Any]) -> str | None:
        """The content of the answer to a prompt and one page image, or None."""
        parts = [{"type": "text", "text": prompt}, _image_part(page)]
        return self._send(parts, schema)[0]

    def _post_document(
        self, prompt: str, sent: list[Page], pages: int, schema: dict[str, Any]
    ) -> tuple[str | None, bool]:
        """The content of the answer to a prompt and every page image of `sent`
        (issue #126), or None; and whether the endpoint cut the answer at the token
        limit (finish reason `length`). In front of each image, a text part names
        its page: "Page 2 of 3:"."""
        parts: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for number, page in enumerate(sent, 1):
            mark = PAGE_MARK.format(page=number, pages=pages)
            parts += [{"type": "text", "text": mark}, _image_part(page)]
        content, finish = self._send(parts, schema)
        cut = content is not None and finish == "length"
        if cut:
            log.warning("omni document answer cut at the token limit")
        return content, cut

    def _send(
        self, parts: list[dict[str, Any]], schema: dict[str, Any]
    ) -> tuple[str | None, str | None]:
        """The content of the answer and its finish reason, or (None, None). The
        answer must follow `schema` (`response_format`, D80). Retries a timeout, a
        429 and a 5xx a few times with a bounded wait; any other error is final."""
        form = {"name": "answer", "schema": schema, "strict": True}
        s = self.settings
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": parts}],
            "temperature": s.temperature,
            "max_tokens": s.max_tokens,
            "chat_template_kwargs": {"enable_thinking": s.thinking},
            "response_format": {"type": "json_schema", "json_schema": form},
        }
        if s.reasoning_budget is not None:  # a key of the NIM API; #18 sent none
            body["reasoning_budget"] = s.reasoning_budget
        status, wait = "none", 0.0
        for attempt in range(RETRIES + 1):
            if attempt:
                self._sleep(wait)
            wait = WAITS[min(attempt, len(WAITS) - 1)]
            try:
                r = self._client.post(self._url, json=body, headers=self._headers)
            except httpx.TransportError as e:  # a timeout too
                status = type(e).__name__
                continue
            status = str(r.status_code)
            if r.status_code == 200:
                try:
                    choice = r.json()["choices"][0]
                    content = choice["message"]["content"]
                except (ValueError, LookupError, TypeError):
                    choice, content = None, None
                if isinstance(content, str) and content.strip():
                    finish = choice.get("finish_reason") if choice else None
                    return content, finish if isinstance(finish, str) else None
                status = "200 with no content"  # e.g. reasoning used every token
                break
            if r.status_code != 429 and r.status_code < 500:
                break
            wait = _retry_after(r.headers.get("retry-after"), wait)
        log.warning("omni call failed: %s (model %s)", status, self.model)
        return None, None

    def extract(self, document: Document, data: bytes, workflow: str) -> Extraction:
        """The fields that have a source, and the line items when every line is
        confirmed. Never raises: a page that fails gives no field."""
        found = pages(data, dpi=self.settings.dpi)
        kind = DocumentType(self.classify(document, data) if found else "unknown")
        schema = SCHEMAS.get(kind)
        if schema is None or kind in NOTHING_TO_READ:
            return Extraction(document_type=kind)
        length = _length(data)
        if self.settings.scope == "document" and len(found) > 1:
            answers, complete = self._document_answers(schema, kind, found, length)
        else:  # a document of one page is read as in #18, in both scopes
            answers, complete = self._page_answers(schema, kind, found, length), True
        return _merge(document.id, kind, schema, found, length, answers, complete)

    def _page_answers(
        self, schema: type[BaseModel], kind: DocumentType, found: list[Page], pages: int
    ) -> list[Answer | None]:
        """One call per page (#18): the answer of each page, None when it is not
        an answer that can be trusted."""
        described = self.settings.prompt in ("described", "example")
        form = answer_schema(schema, described)
        sent = values_schema(schema, described)
        answers = []
        for number, page in enumerate(found, 1):
            prompt = page_prompt(self.settings.prompt, kind.value, number, pages, sent)
            answers.append(_read(schema, _json(self._post(prompt, page, form))))
        return answers

    def _document_answers(
        self, schema: type[BaseModel], kind: DocumentType, found: list[Page], pages: int
    ) -> tuple[list[Answer | None], bool]:
        """One call for the document (issue #126): the answer of each page of
        `found`, read from its block, and whether the answer is complete: it names
        exactly the pages sent and the endpoint did not cut it. No answer, or an
        answer in another form (most cut answers: their JSON does not close), gives
        None for every page. An answer that the endpoint marks as cut and that is
        whole JSON all the same is read, as the page scope reads it, but it is not
        complete: a person checks every field and no line is kept. Dropping it would
        leave a document with no field, which nothing marks (D80 (13)). There is no
        second call and no call per page after a failure: the scope of the call is
        what #126 compares."""
        sent = _within(found)
        prompt = document_prompt(kind.value, len(sent), pages, values_schema(schema))
        form = document_schema(schema, len(sent))
        content, cut = self._post_document(prompt, sent, pages, form)
        blocks, exact = _blocks(content, len(sent))
        answers = [_read(schema, block) for block in blocks]
        return answers + [None] * (len(found) - len(sent)), exact and not cut


def _image_part(page: Page) -> dict[str, Any]:
    url = f"data:{page.mime};base64,{base64.b64encode(page.image).decode()}"
    return {"type": "image_url", "image_url": {"url": url}}


def _within(found: list[Page]) -> list[Page]:
    """The first pages whose images fit together in one document call
    (MAX_DOCUMENT_BYTES); page 1 is always sent."""
    total = 0
    sent: list[Page] = []
    for page in found:
        total += len(page.image)
        if sent and total > MAX_DOCUMENT_BYTES:
            break
        sent.append(page)
    return sent


def _merge(
    document_id: str,
    kind: DocumentType,
    schema: type[BaseModel],
    found: list[Page],
    length: int,
    answers: list[Answer | None],
    complete: bool = True,
) -> Extraction:
    """The extraction of a document from the answer of each page of `found` (None:
    the page was not read). `length` is the number of pages of the document.
    `complete` is False when a document answer held something that is not one of
    its pages. Both scopes end here: the checks of a field and of a line, the
    conflict of two pages and the marks for a person are the same code."""
    has_lines = "line_items" in schema.model_fields
    fields: dict[str, ExtractedField] = {}
    lines: list[dict[str, Any]] = []
    lines_complete = True
    all_read = complete and len(found) == length  # False above MAX_PAGES
    first_seen: dict[str, Any] = {}
    disagree: set[str] = set()  # two pages give two values
    for number, (page, answer) in enumerate(zip(found, answers, strict=True), 1):
        if answer is None:  # this page may hold values and lines nobody read
            all_read = False
            continue
        values, snippets, lines_refused = answer
        for name in schema.model_fields:
            value = getattr(values, name)
            # A placeholder ("Not answerable") is no value: skipped like null,
            # so it never makes a conflict with the real value of another page.
            if name == "line_items" or value is None or labels.is_placeholder(value):
                continue
            same = _norm(value) if isinstance(value, str) else value
            if first_seen.setdefault(name, same) != same:
                disagree.add(name)
            if name in fields:
                continue
            field = _field(document_id, number, page, name, value, snippets, kind.value)
            if field is not None:
                fields[name] = field
        if has_lines:
            read = [
                line.model_dump()
                for line in values.line_items  # type: ignore[attr-defined]
                if any(v is not None for v in line.model_dump().values())
            ]
            if lines_refused or not all(_on_page(i, page.text) for i in read):
                lines_complete = False
            lines += read
    # Not final, so a person checks it (D80): every field when a page was not
    # read (it may hold a value that changes the claim); a field that two pages
    # give differently; the total when the list of lines is not complete.
    doubt = set(fields) if not all_read else disagree
    if has_lines and not (lines_complete and all_read):
        # An incomplete list would look complete to T003 and to the settlement:
        # no list at all.
        lines = []
        doubt = doubt | {"total"}
    for name in doubt & set(fields):
        field = fields[name]
        fields[name] = field.model_copy(
            update={"confidence": min(field.confidence, UNVERIFIED)}
        )
    return Extraction(
        document_type=kind, fields=list(fields.values()), line_items=lines
    )
