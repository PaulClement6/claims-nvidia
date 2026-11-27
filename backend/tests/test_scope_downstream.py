"""What happens downstream when a reader gives a document with no field (issue #126).

In the document scope ONE call reads all the pages of a document. When that call
fails, is cut or is unusable, `OmniReader.extract` returns an `Extraction` that has the
document type and no field (`omni._merge` with no block read). The contract has no
"not read": the same value is also the answer for a document that prints nothing. This
file does not assume that the routing sends such a claim to a person. It plays whole
claims through the real components and records what happens.

The seam. `numbers._play`, the function of the page "Business numbers" that plays a
claim story with no agent model: `update_claim`, then for each document the tools
`classify_document` and `extract_document`, then `run_checks`, `check_tampering`,
`check_consistency` and `assess_and_route` of a real `Gateway` on a real (in memory)
`Store`. So the real tools, rules, signals, score, settlement, routing, fraud report,
payment and customer view run. Only the reader differs: a `Hybrid` reader gives the
document under test to a real `OmniReader` whose HTTP client is a mock transport (the
real `classify`, the real `extract` in both scopes and the real `_merge` run), and every
other document to the `FixtureReader` (the perfect reader of the kit). No agent model,
no network, no key: `Endpoint` answers every request, records it, and the tests assert
that each request went to the stub.

Naming. A test that ends in `_gap` pins a SAFETY GAP that exists today: information was
lost, and the claim still goes straight through (it is paid with no person). The test
passes today, because it asserts what the code does; its docstring says what the safe
behaviour would be. Every gap names, in its docstring, whether the page scope has it
too. A test that ends in `_safe` pins a case where a backstop sends the claim to a
person. Nothing here changes any application code.
"""

import ast
import copy
import hashlib
import json
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest import mock

import httpx2 as httpx
import pytest
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader
from reportlab.lib.utils import ImageReader  # type: ignore[import-untyped]
from reportlab.pdfgen import canvas  # type: ignore[import-untyped]

from app import demo, numbers
from app.docintel import labels, omni
from app.docintel.fixture import FixtureReader
from app.docintel.omni import UNVERIFIED, VERIFIED, OmniReader, OmniSettings
from app.docintel.schemas import SCHEMAS
from app.fraud.routing import LOW_CONFIDENCE
from app.handler import decide
from app.models import (
    Claim,
    Document,
    DocumentType,
    ExtractedField,
    Extraction,
    Outcome,
    PipelineEvent,
)
from app.payment import export_record
from app.store import Store
from app.tools import build_gateway
from app.views import CustomerView, customer_view
from tests.reader_contract import MOCK
from tests.test_omni_reader import URL

Json = dict[str, Any]
SCOPES = ("page", "document")
A4_POINTS = (595.27, 841.89)


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """No request can leave the test. The key and the URL are dummies (`.env` is never
    read). The real transport of the HTTP library refuses to send; the only client that
    works is the mock transport of an `Endpoint`. `CLAIMS_READER` is set to the fixture,
    so no code path could build a real reader by mistake."""
    monkeypatch.setenv("NVIDIA_KEY", "test-key-value")
    monkeypatch.setenv("VLM_BASE_URL", URL)
    monkeypatch.setenv("VLM_MODEL", "omni-test")
    monkeypatch.setenv("CLAIMS_READER", "fixture")

    def refuse(self: object, request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"a real request would leave: {request.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)


_RENDERED: dict[tuple[bytes, int | None, int], list[omni.Page]] = {}


@pytest.fixture(autouse=True)
def rendered_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Render each document once for the whole run (the pages are immutable tuples and
    the tests change nothing in them). Only a speed-up."""
    original = omni.pages

    def pages(data: bytes, first: int | None = None, dpi: int = omni.DPI) -> Any:
        key = (data, first, dpi)
        if key not in _RENDERED:
            _RENDERED[key] = original(data, first, dpi)
        return list(_RENDERED[key])

    monkeypatch.setattr(omni, "pages", pages)


def test_a_real_transport_cannot_send_anything() -> None:
    """The guard of this file: the HTTP client of the library has no way out. Only the
    mock transport of an `Endpoint` answers."""
    with pytest.raises(AssertionError, match="a real request would leave"):
        httpx.Client().post(f"{URL}/chat/completions", json={})


# ----------------------------------------------------------------- the stubbed model


class Finished:
    """A 200 answer with this content and this finish reason."""

    def __init__(self, content: Any, reason: str) -> None:
        self.content, self.reason = content, reason


TIMEOUT = object()  # the reply that makes the request time out


def respond(reply: Any, request: httpx.Request) -> httpx.Response:
    """An int is a status, `TIMEOUT` raises a timeout, a `Finished` has its finish
    reason, anything else is the content of a 200 answer (a str as it is, the rest as
    JSON)."""
    if reply is TIMEOUT:
        raise httpx.ReadTimeout("slow", request=request)
    if isinstance(reply, int):
        return httpx.Response(reply, text='{"message": "error"}')
    content, reason = (
        (reply.content, reply.reason)
        if isinstance(reply, Finished)
        else (reply, "stop")
    )
    text = content if isinstance(content, str) else json.dumps(content)
    choice = {"message": {"content": text}, "finish_reason": reason}
    return httpx.Response(200, json={"choices": [choice]})


@dataclass(frozen=True)
class Model:
    """What the model replies: to classify, to the call of page `k` (page scope) and
    to the one call of the document (document scope)."""

    classify: Any
    page: Callable[[int], Any]
    document: Any


class Endpoint:
    """The HTTP side of the model: a mock transport that tells the three kinds of
    request apart by their prompt, records every request and refuses any other URL."""

    def __init__(self, model: Model) -> None:
        self.model = model
        self.requests: list[Json] = []
        self.kinds: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert str(request.url) == f"{URL}/chat/completions"
        body = json.loads(request.content)
        self.requests.append(body)
        text = body["messages"][0]["content"][0]["text"]
        if text == omni.CLASSIFY:
            kind, reply = "classify", self.model.classify
        elif text.startswith("Read every image"):
            kind, reply = "document", self.model.document
        else:
            found = re.match(r"Read the whole image\. It is page (\d+) of", text)
            assert found, text[:80]
            kind, reply = "page", self.model.page(int(found.group(1)))
        self.kinds.append(kind)
        return respond(reply, request)

    def reader(self, scope: str) -> OmniReader:
        client = httpx.Client(transport=httpx.MockTransport(self.handle))
        return OmniReader(
            client=client, sleep=lambda _: None, settings=OmniSettings(scope=scope)
        )

    @property
    def extraction_calls(self) -> int:
        return len([k for k in self.kinds if k != "classify"])


# ---------------------------------------------------------------------- the documents


@dataclass(frozen=True, eq=False)
class Paper:
    """A document: its file name, its bytes and its answer key (the format of the MP1
    keys: `document_type`, `pages`, `fields` with a page and a snippet, `line_items`
    with `line_pages`)."""

    name: str
    data: bytes
    key: Json

    @property
    def kind(self) -> str:
        return str(self.key["document_type"])

    @property
    def pages(self) -> int:
        return int(self.key["pages"])

    def answers(self) -> list[Json]:
        """The right answer for each page, from the key."""
        schema = SCHEMAS[DocumentType(self.kind)]
        found = []
        for number in range(1, self.pages + 1):
            values: Json = {n: None for n in schema.model_fields if n != "line_items"}
            snippets: Json = {}
            for f in self.key["fields"]:
                if f["value"] is not None and f["page"] == number:
                    values[f["name"]] = f["value"]
                    snippets[f["name"]] = f["snippet"]
            if "line_items" in schema.model_fields:
                values["line_items"] = [
                    line
                    for line, page in zip(
                        self.key["line_items"], self.key["line_pages"], strict=True
                    )
                    if page == number
                ]
            found.append({"values": values, "snippets": snippets})
        return found


@cache
def kit(path: str) -> Paper:
    """A document of the kit with its answer key (`data/mock/<path>`)."""
    file = MOCK / path
    key: Json = json.loads(file.with_suffix(".expected.json").read_text())
    data = file.read_bytes()
    pages = len(PdfReader(BytesIO(data)).pages) if file.suffix == ".pdf" else 1
    key.setdefault("line_items", [])
    key["pages"] = pages
    key.setdefault("line_pages", [1] * len(key["line_items"]))
    return Paper(file.name, data, key)


def build_pdf(pages: list[list[str]]) -> bytes:
    """A PDF with a text layer: these lines on each page."""
    out = BytesIO()
    pdf = canvas.Canvas(out, pagesize=A4_POINTS, invariant=1)
    pdf.setFont("Helvetica", 10)
    for lines in pages:
        for i, line in enumerate(lines):
            pdf.drawString(50, 800 - 16 * i, line)
        pdf.showPage()
    pdf.save()
    return out.getvalue()


def build_scan(pages: list[list[str]]) -> bytes:
    """A PDF whose pages are images: no text layer."""
    out = BytesIO()
    pdf = canvas.Canvas(out, pagesize=A4_POINTS, invariant=1)
    font = ImageFont.load_default(size=26)
    for lines in pages:
        image = Image.new("L", (1240, 1754), 255)
        draw = ImageDraw.Draw(image)
        for i, line in enumerate(lines):
            draw.text((70, 70 + 40 * i), line, fill=0, font=font)
        pdf.drawImage(ImageReader(image), 0, 0, *A4_POINTS)
        pdf.showPage()
    pdf.save()
    return out.getvalue()


Field = tuple[str, Any, str]  # name, value, the text printed on the page that shows it
Row = tuple[int, Json]  # page, line item


def row(description: str, quantity: float, unit_price: float, amount: float) -> Json:
    return {
        "description": description,
        "quantity": quantity,
        "unit_price": unit_price,
        "amount": amount,
    }


def paper(
    name: str,
    kind: str,
    pages: list[list[str]],
    fields: list[Field],
    lines: list[Row] | None = None,
    scan: bool = False,
) -> Paper:
    """A document built from the lines of each page, with its key. The page of a field
    is the first page that prints its snippet (checked: a snippet that is printed
    nowhere is an error of the test)."""
    texts = ["\n".join(p) for p in pages]
    located = []
    for field_name, value, snippet in fields:
        shown = [n for n, t in enumerate(texts, 1) if omni._shown(snippet, t)]
        assert shown, (name, field_name, snippet)
        located.append(
            {"name": field_name, "value": value, "page": shown[0], "snippet": snippet}
        )
    rows = lines or []
    key: Json = {
        "document_type": kind,
        "pages": len(pages),
        "fields": located,
        "line_items": [r for _, r in rows],
        "line_pages": [p for p, _ in rows],
    }
    data = build_scan(pages) if scan else build_pdf(pages)
    return Paper(name, data, key)


def text_lines(path: str) -> list[str]:
    """The lines of the text layer of page 1 of a kit PDF."""
    page = PdfReader(MOCK / path).pages[0]
    return [line for line in (page.extract_text() or "").splitlines() if line.strip()]


def reprint(
    path: str, replace: dict[str, str] | None = None, cut: int | None = None
) -> Paper:
    """A kit document printed again with the words of `replace` changed (a name, a
    date). Its fields and lines are those of the kit key with the same change. With
    `cut`, the lines from that one on are printed on a second page, so a field can be
    on page 2. The result has a text layer and mock content only."""
    original = kit(path)
    change = replace or {}

    def swap(text: str) -> str:
        for old, new in change.items():
            text = text.replace(old, new)
        return text

    lines = [swap(x) for x in text_lines(path)]
    if cut is None:
        pages = [lines]
    else:
        pages = [
            [*lines[:cut], f"Page 1 of {2}"],
            [f"Continued, page 2 of {2}", *lines[cut:]],
        ]
    fields = [
        (f["name"], swap(f["value"]) if isinstance(f["value"], str) else f["value"],
         swap(f["snippet"]))
        for f in original.key["fields"]
        if f["value"] is not None
    ]  # fmt: skip
    rows: list[Row] = []
    for item in original.key["line_items"]:
        page = next(
            n for n, p in enumerate(pages, 1) if omni._on_page(item, "\n".join(p))
        )
        rows.append((page, item))
    return paper(original.name, original.kind, pages, fields, rows)


# ------------------------------------------------------------------ the model's replies


def blocks(answers: list[Json]) -> Json:
    """The answer of a document call from one answer per page."""
    return {f"page_{n}": a for n, a in enumerate(answers, 1)}


def serving(answers: list[Json]) -> Callable[[int], Any]:
    """The reply of the call of page k (page scope): the answer of that page."""
    return lambda k: answers[k - 1]


def blank(kind: str) -> Json:
    """The answer for a page that shows none of the values of the type."""
    schema = SCHEMAS[DocumentType(kind)]
    values: Json = {n: [] if n == "line_items" else None for n in schema.model_fields}
    return {"values": values, "snippets": {}}


def cut_text(content: Any) -> str:
    """The JSON of an answer, cut in the middle."""
    text = json.dumps(content)
    return text[: len(text) * 2 // 3]


def invented(answers: list[Json]) -> list[Json]:
    """The same values, with snippets that are printed nowhere."""
    out = copy.deepcopy(answers)
    for a in out:
        a["snippets"] = {n: f"invented text for {n} 9999-ZZ" for n in a["snippets"]}
    return out


def _null(name: str) -> Callable[[Paper, list[Json]], list[Json]]:
    """The model leaves one value out (null) and gives everything else right."""

    def edit(_: Paper, answers: list[Json]) -> list[Json]:
        for a in answers:
            if name in a["values"]:
                a["values"][name] = None
                a["snippets"].pop(name, None)
        return answers

    return edit


def _line_not_on_page(_: Paper, answers: list[Json]) -> list[Json]:
    """One line of the first page that has lines is not printed on that page."""
    first = next(a for a in answers if a["values"].get("line_items"))
    first["values"]["line_items"][0]["description"] = "A line that no page prints"
    return answers


def _total_not_stored(_: Paper, answers: list[Json]) -> list[Json]:
    """The right values and lines, but the snippet of the total is printed nowhere."""
    for a in answers:
        if "total" in a["snippets"]:
            a["snippets"]["total"] = "Total 0000.00 (invented)"
    return answers


def _left_out(count: int) -> Callable[[Paper, list[Json]], list[Json]]:
    """The model silently leaves out the last `count` lines of the whole document
    (every line it gives is right). Total and fields are right."""

    def edit(_: Paper, answers: list[Json]) -> list[Json]:
        left = count
        for a in reversed(answers):
            while left and a["values"].get("line_items"):
                a["values"]["line_items"].pop()
                left -= 1
        assert left == 0
        return answers

    return edit


def _wrong_page(_: Paper, answers: list[Json]) -> list[Json]:
    """Document scope, scan: the fields of the last page are given in the block of
    page 1 (the model counts the pages wrong). The last block is empty."""
    first, last = answers[0], answers[-1]
    for name, value in last["values"].items():
        if value not in (None, []) and name != "line_items":
            first["values"][name] = value
            first["snippets"][name] = last["snippets"][name]
            last["values"][name] = None
    last["snippets"] = {}
    return answers


EDITS: dict[str, Callable[[Paper, list[Json]], list[Json]]] = {
    "null:invoice_date": _null("invoice_date"),
    "null:refund_received": _null("refund_received"),
    "null:total": _null("total"),
    "null:departure_date": _null("departure_date"),
    "line_not_on_page": _line_not_on_page,
    "total_not_stored": _total_not_stored,
    "left_out_1": _left_out(1),
    "left_out_2": _left_out(2),
    "wrong_page": _wrong_page,
}


def model(paper: Paper, behaviour: str) -> Model:
    """What the model does with this document. The behaviours:

    perfect        the right answer of each page
    http500        a 500 for every request (the reader retries twice, then gives up)
    http400        a 400
    timeout        every request times out
    lazy           valid JSON, every value null, no line item, for every page
    length         the right answer, complete JSON, finish reason `length`
    cut            the JSON cut in the middle, finish reason `length`
    cut_json       the JSON cut in the middle, finish reason `stop`
    missing_block  the block of the last page is absent (page scope: that call fails)
    invented       the right values, every snippet invented
    not_json       text that is not JSON
    """
    kind = {"document_type": paper.kind}
    answers = paper.answers()
    last = paper.pages

    def same(reply: Any) -> Model:
        return Model(kind, lambda k: reply, reply)

    if behaviour == "perfect":
        return Model(kind, lambda k: answers[k - 1], blocks(answers))
    if behaviour == "http500":
        return same(500)
    if behaviour == "http400":
        return same(400)
    if behaviour == "timeout":
        return same(TIMEOUT)
    if behaviour == "not_json":
        return same("I cannot read this document.")
    if behaviour == "lazy":
        empty = [blank(paper.kind)] * paper.pages
        return Model(kind, lambda k: empty[k - 1], blocks(empty))
    if behaviour == "length":
        return Model(
            kind, lambda k: Finished(answers[k - 1], "length"),
            Finished(blocks(answers), "length"),
        )  # fmt: skip
    if behaviour == "cut":
        return Model(
            kind,
            lambda k: Finished(cut_text(answers[k - 1]), "length"),
            Finished(cut_text(blocks(answers)), "length"),
        )
    if behaviour == "cut_json":
        return Model(
            kind, lambda k: cut_text(answers[k - 1]), cut_text(blocks(answers))
        )
    if behaviour == "missing_block":
        shown = blocks(answers)
        del shown[f"page_{last}"]
        return Model(kind, lambda k: 500 if k == last else answers[k - 1], shown)
    if behaviour == "invented":
        fake = invented(answers)
        return Model(kind, lambda k: fake[k - 1], blocks(fake))
    if behaviour == "cut_last":
        # the token limit hits: the page scope loses the longest page (the last one),
        # the document scope loses the one answer that holds every page
        return Model(
            kind,
            lambda k: (
                Finished(cut_text(answers[k - 1]), "length")
                if k == last
                else answers[k - 1]
            ),
            Finished(cut_text(blocks(answers)), "length"),
        )
    if behaviour == "classify_500":
        return Model(500, lambda k: answers[k - 1], blocks(answers))
    if behaviour == "classify_junk":
        return Model(
            {"document_type": "invoice"}, lambda k: answers[k - 1], blocks(answers)
        )
    if behaviour == "lazy_rest":
        rest = [answers[0]] + [blank(paper.kind)] * (paper.pages - 1)
        return Model(kind, lambda k: rest[k - 1], blocks(rest))
    if behaviour in EDITS:
        edited = EDITS[behaviour](paper, copy.deepcopy(answers))
        return Model(kind, lambda k: edited[k - 1], blocks(edited))
    raise AssertionError(behaviour)


BEHAVIOURS = (
    "http500",
    "http400",
    "timeout",
    "not_json",
    "lazy",
    "length",
    "cut",
    "cut_json",
    "invented",
)


# ---------------------------------------------------------------------- the claims


@cache
def fixture_reader() -> FixtureReader:
    return FixtureReader()


class Hybrid:
    """The perfect reader of the kit for every document, except the documents that
    have a route: those go to the OmniReader of their route (the stubbed model)."""

    name = "hybrid"

    def __init__(self, routes: dict[str, OmniReader]) -> None:
        self.routes = routes

    def _reader(self, data: bytes) -> Any:
        return self.routes.get(hashlib.sha256(data).hexdigest(), fixture_reader())

    def classify(self, document: Document, data: bytes) -> str:
        return str(self._reader(data).classify(document, data))

    def extract(self, document: Document, data: bytes, workflow: str) -> Extraction:
        out: Extraction = self._reader(data).extract(document, data, workflow)
        return out


@dataclass(frozen=True, eq=False)
class Case:
    """A claim story of the kit with one document under test.

    `folder`: the story of `data/mock/index.json`; `amount`: the claimed amount
    (None: the one of the story); `target`: the file name of the document read by the
    stubbed model; `paper`: the bytes of that document when they are not the kit file;
    `extra`: more documents, read by the stubbed model with a perfect answer; `drop`:
    kit files that this claim does not have."""

    folder: str
    target: str
    amount: float | None = None
    paper: Paper | None = None
    extra: tuple[Paper, ...] = ()
    drop: tuple[str, ...] = ()
    cause: str | None = None

    def target_paper(self) -> Paper:
        return self.paper or kit(f"{self.folder}/{self.target}")


@dataclass
class Played:
    """A claim after the whole chain, and what the model was asked."""

    claim: Claim
    events: list[PipelineEvent]
    view: CustomerView
    endpoints: dict[str, Endpoint]  # by file name; the target and the extra papers
    store: Store  # in memory: a test may let a handler decide the claim

    @property
    def outcome(self) -> Outcome:
        assert self.claim.outcome is not None
        return self.claim.outcome

    @property
    def codes(self) -> list[str]:
        return sorted(s.code for s in self.claim.signals)

    @property
    def payable(self) -> float | None:
        return self.claim.proposal.payable if self.claim.proposal else None

    @property
    def needs_person(self) -> list[str]:
        return self.claim.proposal.needs_person if self.claim.proposal else []

    @property
    def reasons(self) -> list[str]:
        return self.claim.outcome_reasons

    @property
    def paid(self) -> bool:
        return self.claim.payment is not None

    @property
    def a_person_sees_it(self) -> bool:
        """The claim is not paid with no person: a handler (or the customer, first,
        for a missing document) has it."""
        return self.outcome != Outcome.straight_through

    def fields(self, file: str) -> dict[str, ExtractedField]:
        doc = next(
            d for d in self.claim.documents if d.file_metadata["original_name"] == file
        )
        return {f.name: f for f in doc.fields}

    def doc(self, file: str) -> Document:
        return next(
            d for d in self.claim.documents if d.file_metadata["original_name"] == file
        )

    def target_requests(self, file: str) -> list[Json]:
        return self.endpoints[file].requests


def story_of(folder: str, amount: float | None, cause: str | None) -> tuple[Json, Json]:
    scenario = next(s for s in demo._index()["scenarios"] if s["folder"] == folder)
    scenario = copy.deepcopy(scenario)
    story = scenario["stories"][0]
    if amount is not None:
        story["amount"] = amount
    if cause is not None:
        story["cause"] = cause
    return scenario, story


def play(case: Case, behaviour: str, scope: str) -> Played:
    """The case through the real tools. The target (and the extra papers) are read by
    an `OmniReader` of `scope` whose model does `behaviour` (the extra papers are
    always read right); everything else by the fixture reader."""
    scenario, story = story_of(case.folder, case.amount, case.cause)
    target = case.target_paper()
    kit_files = {
        k: v for k, v in demo._kit().items() if k[0] == case.folder
    }  # (folder, file) -> path
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "docs"
        folder.mkdir()
        papers = {case.target: target} | {p.name: p for p in case.extra}
        files = dict(kit_files)
        for name, p in papers.items():
            (folder / name).write_bytes(p.data)
            files[(case.folder, name)] = folder / name
        documents = [d for d in scenario["documents"] if d["file"] not in case.drop]
        scenario["documents"] = documents + [
            {"file": p.name, "document_type": p.kind} for p in case.extra
        ]
        routes: dict[str, OmniReader] = {}
        endpoints: dict[str, Endpoint] = {}
        for name, p in papers.items():
            endpoint = Endpoint(
                model(p, behaviour if name == case.target else "perfect")
            )
            endpoints[name] = endpoint
            routes[hashlib.sha256(p.data).hexdigest()] = endpoint.reader(scope)
        store = Store(":memory:")
        store.export_files = False
        gateway = build_gateway(store, Hybrid(routes), Path(tmp))
        with mock.patch.object(demo, "_kit", lambda: files):
            claim = numbers._play(gateway, Path(tmp), scenario, story)
        events = store.events_since(claim.id, 0)
    for endpoint in endpoints.values():  # every request went to the stub
        assert all(k in ("classify", "page", "document") for k in endpoint.kinds)
    return Played(claim, events, customer_view(claim), endpoints, store)


_RUNS: dict[tuple[Case, str, str], Played] = {}


def run(case: Case, behaviour: str, scope: str) -> Played:
    """`play`, once per (case, behaviour, scope): the tests only read the result."""
    key = (case, behaviour, scope)
    if key not in _RUNS:
        _RUNS[key] = play(case, behaviour, scope)
    return _RUNS[key]


def control(case: Case, scope: str = "page") -> Played:
    """The same claim with the perfect answer of the model."""
    return run(case, "perfect", scope)


# ------------------------------------------------------------------------ the cases

TRAVEL_CERT = Case("claim-1-travel", "medical-certificate.pdf")
TRAVEL_BOOKING = Case("claim-1-travel", "booking-confirmation.pdf")
TRAVEL_CANCEL = Case("claim-1-travel", "cancellation-confirmation.pdf")
HEALTH_REPORT = Case("kit-lukas-clean", "medical-report.pdf")
HEALTH_INVOICE = Case("kit-lukas-clean", "medical-invoice.pdf")
LUGGAGE_REPORT = Case("kit-anna-luggage", "luggage-report.pdf")
LUGGAGE_BOOKING = Case("kit-anna-luggage", "booking-confirmation.pdf")
LUGGAGE_RECEIPT = Case("kit-anna-luggage", "receipt-trolley.pdf")
THEFT_POLICE = Case("kit-anna-theft", "police-report.pdf", amount=1800)
KIT_CASES = {
    "claim-1 certificate": TRAVEL_CERT,
    "claim-1 booking": TRAVEL_BOOKING,
    "claim-1 cancellation": TRAVEL_CANCEL,
    "lukas report": HEALTH_REPORT,
    "lukas invoice": HEALTH_INVOICE,
    "anna luggage report": LUGGAGE_REPORT,
    "anna luggage booking": LUGGAGE_BOOKING,
    "anna luggage receipt": LUGGAGE_RECEIPT,
    "anna theft police report": THEFT_POLICE,
}


# ------------------------------------------------------------- 0. the harness is right


@pytest.mark.parametrize("scope", SCOPES)
@pytest.mark.parametrize("label", sorted(KIT_CASES))
def test_a_perfect_answer_gives_what_the_fixture_reader_gives(
    label: str, scope: str
) -> None:
    """The control of every test below, and the proof that the harness changes nothing:
    the document read by the real OmniReader (a stubbed model that answers the right
    values) gives the same outcome, score, signals, reasons and amount as the same
    claim read by the fixture reader."""
    case = KIT_CASES[label]
    through_omni = control(case, scope)
    # the fixture reader on every document: the same story with no stubbed model
    scenario, story = story_of(case.folder, case.amount, case.cause)
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(":memory:")
        store.export_files = False
        gateway = build_gateway(store, fixture_reader(), Path(tmp))
        scenario["documents"] = [
            d for d in scenario["documents"] if d["file"] not in case.drop
        ]
        reference = numbers._play(gateway, Path(tmp), scenario, story)
    assert through_omni.outcome == reference.outcome
    assert through_omni.codes == sorted(s.code for s in reference.signals)
    assert through_omni.claim.score == reference.score
    assert through_omni.reasons == reference.outcome_reasons
    assert through_omni.payable == (
        reference.proposal.payable if reference.proposal else None
    )
    # every request of the document under test went to the stub
    endpoint = through_omni.endpoints[case.target]
    assert endpoint.requests and endpoint.kinds[0] == "classify"
    assert all(r["model"] == "omni-test" for r in endpoint.requests)


# ---------------------------------------------------------- documents of two pages


def cut_at(path: str, word: str) -> int:
    """The index of the first line of the kit text that holds this word."""
    return next(i for i, line in enumerate(text_lines(path)) if word in line)


CERT = "claim-1-travel/medical-certificate.pdf"
REPORT = "kit-lukas-clean/medical-report.pdf"
INVOICE = "kit-lukas-clean/medical-invoice.pdf"
POLICE = "kit-anna-theft/police-report.pdf"
OTHER_PERSON = {"Anna Keller": "Marco Bianchi"}  # a customer of the mock data


@cache
def cert2(patient: str = "Anna Keller") -> Paper:
    """The certificate of claim file 1 on two pages: the patient and the cause on page
    1, the first day of the incapacity on page 2."""
    change = {} if patient == "Anna Keller" else {"Anna Keller": patient}
    return reprint(CERT, change, cut_at(CERT, "unfit to travel"))


@cache
def report2(patient: str = "Lukas Meier") -> Paper:
    """The medical report of Lukas Meier on two pages: the patient on page 1, the
    diagnosis on page 2."""
    change = {} if patient == "Lukas Meier" else {"Lukas Meier": patient}
    return reprint(REPORT, change, cut_at(REPORT, "Diagnosis"))


@cache
def invoice2() -> Paper:
    """The medical invoice of Lukas Meier (840.00) on two pages: three rows on page 1,
    two rows and the total on page 2."""
    return reprint(INVOICE, None, cut_at(INVOICE, "Follow-up consultation"))


@cache
def police2(person: str = "Anna Keller") -> Paper:
    """The police report of the burglary on two pages: the report number and the
    reporting person on page 1, the offence and the date of discovery on page 2."""
    change = {} if person == "Anna Keller" else {"Anna Keller": person}
    return reprint(POLICE, change, cut_at(POLICE, "Offence"))


CERT_2 = Case("claim-1-travel", "medical-certificate.pdf", paper=cert2())
CERT_2_OTHER = Case(
    "claim-1-travel", "medical-certificate.pdf", paper=cert2("Marco Bianchi")
)
REPORT_2 = Case("kit-lukas-clean", "medical-report.pdf", paper=report2())
REPORT_2_OTHER = Case(
    "kit-lukas-clean", "medical-report.pdf", paper=report2("Jonas Meier")
)
INVOICE_2 = Case("kit-lukas-clean", "medical-invoice.pdf", paper=invoice2())
POLICE_2 = Case("kit-anna-theft", "police-report.pdf", 1800, paper=police2())
POLICE_2_OTHER = Case(
    "kit-anna-theft", "police-report.pdf", 1800, paper=police2("Marco Bianchi")
)
TWO_PAGES = {
    "certificate": CERT_2,
    "medical report": REPORT_2,
    "medical invoice": INVOICE_2,
    "police report": POLICE_2,
}


# ----------------------------------------------------------------- shared assertions

LOSSES = (
    "http500",
    "http400",
    "timeout",
    "not_json",
    "lazy",
    "cut",
    "cut_json",
    "invented",
)  # a document that gives no field in the document scope (one call) and, for a
# page-scope document of one page, in the page scope too: the one call fails

VALIDATED = "Your claim is validated. You will receive a confirmation by e-mail."
TO_A_PERSON = "A claims specialist will review your case and contact you."


def assert_no_field(p: Played, case: Case, behaviour: str = "") -> Document:
    """The document has its type and no field. It has no line item either, except when
    the model only invented its snippets: the lines are checked on the page, not by a
    snippet, so a valid table is kept."""
    document = p.doc(case.target)
    assert document.fields == []
    assert behaviour == "invented" or document.line_items == []
    assert document.document_type == case.target_paper().kind
    return document


def assert_paid_with_no_person(p: Played) -> None:
    """The claim went straight through: validated and paid, and the customer was told
    so. No handler saw it."""
    assert p.outcome == Outcome.straight_through and not p.a_person_sees_it
    assert p.paid and p.claim.status.value == "paid" and p.claim.decisions == []
    assert p.reasons == [] and p.claim.fraud_report is None
    assert p.view.message == VALIDATED and p.view.requests == []


def assert_to_a_person(p: Played) -> None:
    assert p.outcome == Outcome.handler_review and p.a_person_sees_it
    assert not p.paid and p.claim.status.value == "in_review"
    assert p.view.message == TO_A_PERSON


def same_outcome(a: Played, b: Played) -> bool:
    return (a.outcome, a.codes, a.claim.score, a.reasons, a.payable, a.paid) == (
        b.outcome,
        b.codes,
        b.claim.score,
        b.reasons,
        b.payable,
        b.paid,
    )


# --------------------------------- 1, 2, 3, 5. a document with no amount gives no field

NO_AMOUNT = {
    "claim-1 certificate": TRAVEL_CERT,
    "lukas report": HEALTH_REPORT,
    "anna luggage report": LUGGAGE_REPORT,
    "anna theft police report": THEFT_POLICE,
    "two pages: certificate": CERT_2,
    "two pages: medical report": REPORT_2,
    "two pages: police report": POLICE_2,
}
ONE_PAGE = {k: v for k, v in NO_AMOUNT.items() if v.target_paper().pages == 1}


@pytest.mark.parametrize("scope", SCOPES)
@pytest.mark.parametrize("behaviour", LOSSES)
@pytest.mark.parametrize("label", sorted(NO_AMOUNT))
def test_a_document_with_no_amount_that_gives_no_field_goes_straight_through_gap(
    label: str, behaviour: str, scope: str
) -> None:
    """SAFETY GAP. Scenarios 1, 2, 3 and 5 (a call that fails, a valid answer with
    nothing in it, a cut answer, snippets that are invented) on a document of one
    page that carries no amount: a medical certificate, a medical report, a luggage
    report, a police report. The reader gives the type and no field, the claim has no
    low-confidence field and no missing document, the settlement does not need the
    document, so nothing sends the claim to a person: it is paid.

    Safe behaviour: a document that was not read must send the claim to a person
    (a "not read" mark in the contract, or a reason of the routing).

    Page scope: the same for a document of one page (one call in both scopes,
    `omni.OmniReader.extract`). For a document of two pages the page scope loses
    everything only when every page call fails, is empty or is cut. Pre-existing:
    D80 (13), but one call to lose instead of one call per page."""
    case = NO_AMOUNT[label]
    before, after = control(case), run(case, behaviour, scope)
    assert before.doc(case.target).fields  # the control read the document
    assert_no_field(after, case)
    assert_paid_with_no_person(after)
    assert same_outcome(before, after)  # nothing in the claim shows that a read failed
    assert after.claim.payment is not None and before.claim.payment is not None
    assert after.claim.payment.amount == before.claim.payment.amount
    # the export of the paid claim holds no field of that document
    sent = export_record(after.claim)
    assert all(f["document_id"] != after.doc(case.target).id for f in sent.fields)
    # one scope or the other: one page, one call, the same claim
    assert same_outcome(run(case, behaviour, "page"), run(case, behaviour, "document"))


@pytest.mark.parametrize("label", sorted(ONE_PAGE))
def test_a_failed_call_is_retried_twice_then_dropped_and_asks_no_other_model(
    label: str,
) -> None:
    """The reader asks classify once, then the extraction three times (two retries),
    and nothing else: no fallback, no second model, no other kind of call."""
    case = NO_AMOUNT[label]
    p = run(case, "http500", "document")
    assert p.endpoints[case.target].kinds == ["classify", "page", "page", "page"]
    q = run(case, "http400", "document")  # a client error is final: no retry
    assert q.endpoints[case.target].kinds == ["classify", "page"]
    assert all(r["model"] == "omni-test" for r in p.endpoints[case.target].requests)


# ------------------------------ the same documents when they carry the amount: safe


AMOUNT = {
    "claim-1 booking": (
        TRAVEL_BOOKING,
        "No document gives the amount of the loss.",
        None,
    ),
    "claim-1 cancellation": (
        TRAVEL_CANCEL,
        "No document gives the refund of the trip.",
        1500.0,
    ),
    "lukas invoice": (
        HEALTH_INVOICE,
        "No document gives the amount of the loss.",
        None,
    ),
    "lukas invoice, two pages": (
        INVOICE_2,
        "No document gives the amount of the loss.",
        None,
    ),
    "anna luggage receipt": (
        LUGGAGE_RECEIPT,
        "The claimed amount is above what the documents prove.",
        398.0,
    ),
}


@pytest.mark.parametrize("scope", SCOPES)
@pytest.mark.parametrize("behaviour", LOSSES)
@pytest.mark.parametrize("label", sorted(AMOUNT))
def test_a_document_that_carries_the_amount_and_gives_no_field_goes_to_a_person_safe(
    label: str, behaviour: str, scope: str
) -> None:
    """The document that carries the amount (the price of the trip, the refund, the
    total of the invoice, a receipt) gives no field: the settlement has no amount, or
    no refund, or less than the claim, and says why in `needs_person`. The routing
    reads it (`fraud/routing.py:62-64`), so a handler sees the claim. This is the
    backstop that the documents with no amount do not have.

    Page scope: the same for a document of one page."""
    case, why, payable = AMOUNT[label]
    after = run(case, behaviour, scope)
    assert_no_field(after, case, behaviour)
    assert_to_a_person(after)
    assert after.needs_person == [why]
    assert f"Settlement: {why}" in after.reasons
    assert after.payable == payable
    assert not any("low confidence" in r for r in after.reasons)  # no field: no mark
    assert same_outcome(after, run(case, behaviour, "page"))


# ------------- 3 (tainted): the signal that the lost document would have raised


TAINTED = {
    "certificate of another person": (CERT_2_OTHER, 20, 1500.0),
    "report of another person": (REPORT_2_OTHER, 20, 756.0),
    "police report of another person": (POLICE_2_OTHER, 35, 1600.0),
}


@pytest.mark.parametrize("scope", SCOPES)
@pytest.mark.parametrize("behaviour", LOSSES)
@pytest.mark.parametrize("label", sorted(TAINTED))
def test_a_lost_document_hides_the_signal_that_it_would_have_raised_gap(
    label: str, behaviour: str, scope: str
) -> None:
    """SAFETY GAP, with its consequence. A document of two pages names a person who
    is not the insured person (a customer of the mock data). Read right, it gives
    C001 (20 points) and the claim goes to a handler. When the document call fails,
    is cut or is empty, the Extraction has no name, C001 cannot fire (`checks/
    consistency.py:32-45`: it looks at the stored names only), and the claim is paid.

    Safe behaviour: a document that was not read must not look like a document that
    was read and holds nothing.

    Page scope: the page scope gives the same result when EVERY page fails, is empty
    or is cut (7 requests for 2 pages with 500s: 1 classify and 3 for each page). A
    transient error on one call of the document scope is enough here; in the page
    scope it must hit each of the pages. See
    `test_the_same_token_shortage_loses_the_document_in_one_scope_only_gap`."""
    case, points, payable = TAINTED[label]
    before, after = control(case, scope), run(case, behaviour, scope)
    assert "C001" in before.codes and before.outcome == Outcome.handler_review
    assert before.claim.score == points and before.payable == payable
    assert_no_field(after, case)
    assert "C001" not in after.codes
    assert after.claim.score == points - 20  # C003 of the police story stays
    assert_paid_with_no_person(after)
    assert after.payable == payable  # the amount did not change: paid in full
    assert after.claim.payment is not None and after.claim.payment.amount == payable


# ------------------------- 4. what only the document scope loses, and what is safe


@pytest.mark.parametrize("label", sorted(TAINTED))
def test_the_same_token_shortage_loses_the_document_in_one_scope_only_gap(
    label: str,
) -> None:
    """SAFETY GAP, INTRODUCED by the document scope. The answer is cut by the token
    limit (`max_tokens` 2048). In the page scope the limit hits one page answer (the
    longest page): that page is not read, the fields of the other page stay at 0.5,
    and a handler sees the claim. In the document scope the one answer holds every
    page: a cut loses all the pages, the document has no field, C001 cannot fire, and
    the claim is paid. `READER-INTERFACE.md` section 9 gives a worst case of 91 to 103
    percent of 2048 tokens for the 3-page invoice of MP1.

    Safe behaviour: a cut answer must mark the document as not read."""
    case, _, payable = TAINTED[label]
    page, document = run(case, "cut_last", "page"), run(case, "cut_last", "document")
    # page scope: one page lost, a person decides
    assert_to_a_person(page)
    assert "C001" in page.codes
    assert any("low confidence" in r for r in page.reasons)
    assert page.doc(case.target).fields
    assert all(f.confidence < LOW_CONFIDENCE for f in page.doc(case.target).fields)
    # document scope: everything lost, nobody decides
    assert_no_field(document, case)
    assert_paid_with_no_person(document)
    assert (
        document.claim.payment is not None and document.claim.payment.amount == payable
    )
    assert "C001" not in document.codes
    assert document.endpoints[case.target].kinds == ["classify", "document"]
    assert page.endpoints[case.target].kinds == ["classify", "page", "page"]


@pytest.mark.parametrize("label", sorted(TAINTED))
def test_a_complete_answer_marked_length_is_marked_not_final_in_the_document_scope(
    label: str,
) -> None:
    """Was G3 (`..._dropped_by_the_document_scope_only_gap`), closed in the reader. The
    endpoint says `length` but the JSON is whole. The page scope does not read the
    finish reason of a page call, so it stores the fields at 0.9 and C001 sends the
    claim to a handler. The document scope now reads the blocks of that answer too,
    stores the same values (same page, same snippet), but marks the document as
    not complete: every field is below `LOW_CONFIDENCE` and no line item is kept (the
    rule of a page that was not read). The claim goes to a handler there too, by two
    reasons: "a field has low confidence" and C001 (the name is stored, so C001 still
    fires). Nothing is paid with no person in either scope."""
    case, points, _ = TAINTED[label]
    page, document = run(case, "length", "page"), run(case, "length", "document")
    stored_page = {f.name: f for f in page.doc(case.target).fields}
    stored_doc = {f.name: f for f in document.doc(case.target).fields}
    assert page.outcome == Outcome.handler_review and "C001" in page.codes
    assert page.claim.score == points
    assert {f.confidence for f in stored_page.values()} == {VERIFIED}
    # the document scope: the same values and sources, each below 0.8, no line item
    assert set(stored_doc) == set(stored_page) and stored_doc
    assert all(
        (stored_doc[n].value, stored_doc[n].source.page, stored_doc[n].source.snippet)
        == (f.value, f.source.page, f.source.snippet)
        for n, f in stored_page.items()
    )
    assert all(f.confidence < LOW_CONFIDENCE for f in stored_doc.values())
    assert document.doc(case.target).line_items == []
    assert_to_a_person(document)
    assert "C001" in document.codes and document.claim.score == points
    assert any("A field has low confidence" in r for r in document.reasons)
    assert not document.paid
    assert document.endpoints[case.target].kinds == ["classify", "document"]


@pytest.mark.parametrize(
    "case",
    [CERT_2, REPORT_2, POLICE_2, INVOICE_2],
    ids=["certificate", "medical report", "police report", "medical invoice"],
)
def test_a_clean_document_with_a_complete_answer_marked_length_goes_to_a_person_safe(
    case: Case,
) -> None:
    """A document with no taint (and, for the first three, no amount), read by a
    complete JSON answer with the finish reason `length`. In the page scope the answer
    is used (0.9, `straight_through`, paid). In the document scope the fields are
    stored below 0.8 with no line item, so the routing reads "a field has low
    confidence" and the outcome is `handler_review`, never `straight_through`. The
    document is not lost (compare G1): it is marked not final."""
    page, document = run(case, "length", "page"), run(case, "length", "document")
    assert page.outcome == Outcome.straight_through and page.paid
    stored = document.doc(case.target)
    assert stored.fields and stored.line_items == []
    assert all(f.confidence < LOW_CONFIDENCE for f in stored.fields)
    assert_to_a_person(document)
    assert document.outcome != Outcome.straight_through and not document.paid
    assert any("A field has low confidence" in r for r in document.reasons)


MISSING_BLOCK = {k: v[0] for k, v in TAINTED.items()} | TWO_PAGES


@pytest.mark.parametrize("scope", SCOPES)
@pytest.mark.parametrize("label", sorted(MISSING_BLOCK))
def test_one_page_that_was_not_read_marks_the_others_and_goes_to_a_person_safe(
    label: str, scope: str
) -> None:
    """Scenario 4: one block is missing (page scope: the call of that page fails). The
    fields of the other page stay, each at 0.5, and no line item is stored: the
    routing reads the confidences. A missing block is never taken as a page that
    holds nothing.

    Both scopes."""
    case = MISSING_BLOCK[label]
    p = run(case, "missing_block", scope)
    document = p.doc(case.target)
    assert document.fields and all(
        f.confidence < LOW_CONFIDENCE for f in document.fields
    )
    assert document.line_items == []
    assert_to_a_person(p)
    assert any("A field has low confidence" in r for r in p.reasons)
    assert same_outcome(p, run(case, "missing_block", "page"))
    assert p.endpoints[case.target].kinds[0] == "classify"


@cache
def late_report() -> Paper:
    """The report of Jonas Meier on two pages: page 1 holds the letterhead only, the
    names, the diagnosis and the date are on page 2."""
    return reprint(REPORT, {"Lukas Meier": "Jonas Meier"}, 4)


REPORT_LATE_OTHER = Case("kit-lukas-clean", "medical-report.pdf", paper=late_report())


def test_the_fixture_of_the_late_report_has_its_names_on_page_2() -> None:
    key = late_report().key
    assert {f["name"]: f["page"] for f in key["fields"]} == {
        "provider": 1,
        "patient_name": 2,
        "diagnosis": 2,
        "treatment_date": 2,
    }
    control_run = control(REPORT_LATE_OTHER)
    assert "C001" in control_run.codes and control_run.outcome == Outcome.handler_review


@pytest.mark.parametrize("scope", SCOPES)
def test_a_model_that_fills_page_1_and_leaves_the_rest_blank_hides_a_signal_gap(
    scope: str,
) -> None:
    """SAFETY GAP (scenario 2, the lazy answer). The model gives the letterhead of
    page 1 and null for every value of page 2, as a model does that "has seen enough".
    The blocks are valid and blank: they are the same as a page that holds nothing, so
    nothing is marked. The provider stays at 0.9; the name, the diagnosis and the date
    are gone; C001 cannot fire; the claim is paid.

    Safe behaviour: nothing in the Extraction can tell a blank page from a lazy one;
    the sum of what the document should hold (the fields of the schema) is not
    compared with what was read.

    Page scope: the same, if the model gives blank answers for those pages. The
    document scope makes it easier: one answer asks the model to fill every page."""
    case = REPORT_LATE_OTHER
    p = run(case, "lazy_rest", scope)
    assert [(f.name, f.confidence) for f in p.doc(case.target).fields] == [
        ("provider", VERIFIED)
    ]
    assert_paid_with_no_person(p)
    assert "C001" not in p.codes and p.payable == 756.0
    assert same_outcome(p, run(case, "lazy_rest", "page"))


# -------------------------------------------- 6, 7. classification, then extraction

REQUIRED = {
    "claim-1 certificate": (TRAVEL_CERT, "a medical certificate."),
    "lukas report": (HEALTH_REPORT, "a medical report or a medical certificate."),
    "claim-1 booking": (TRAVEL_BOOKING, "a booking confirmation."),
    "anna luggage booking": (LUGGAGE_BOOKING, "a booking confirmation."),
}


@pytest.mark.parametrize("scope", SCOPES)
@pytest.mark.parametrize("behaviour", ["classify_500", "classify_junk"])
@pytest.mark.parametrize("label", sorted(REQUIRED))
def test_a_document_that_is_not_classified_is_a_missing_document_safe(
    label: str, behaviour: str, scope: str
) -> None:
    """Scenario 7. Classification fails (a 500 three times, or a type that is not
    one of the known types): the type is `unknown`, the reader sends no extraction
    request, and the required-documents rule (R001) finds the document missing. The
    claim waits for the customer and goes to a handler. A backstop, but a wrong one:
    the customer uploaded the document and is asked for it again.

    Both scopes: `classify` is the same code and the same request."""
    case, missing = REQUIRED[label]
    p = run(case, behaviour, scope)
    document = p.doc(case.target)
    assert document.document_type == "unknown" and document.fields == []
    assert "R001" in p.codes and p.outcome == Outcome.handler_review
    assert p.claim.status.value == "waiting_customer" and not p.paid
    assert p.view.requests == [missing.removesuffix(".")]
    assert p.view.message is not None
    assert p.view.message.startswith(f"We still need: {missing}")
    # the tool classifies, then `extract` classifies again (a failure is not kept):
    # no extraction request is sent for a document of unknown type
    kinds = p.endpoints[case.target].kinds
    assert set(kinds) == {"classify"}
    assert len(kinds) == (6 if behaviour == "classify_500" else 2)
    assert same_outcome(p, run(case, behaviour, "page"))


@pytest.mark.parametrize("scope", SCOPES)
@pytest.mark.parametrize("behaviour", ["classify_500", "classify_junk"])
def test_a_luggage_report_that_is_not_classified_goes_straight_through_gap(
    behaviour: str, scope: str
) -> None:
    """SAFETY GAP. The luggage report is not a required document when the claim has
    receipts (`workflows/travel.py:17-19`: the proof is a cancellation confirmation, a
    receipt or a luggage report). Its classification fails: the document is `unknown`,
    R001 does not fire, nothing else reads the report, and the claim is paid. The
    report proves the loss with the airline (the traveller, the date).

    Safe behaviour: a document that the customer uploaded and the system could not
    read must reach a person, also when no rule requires it.

    Page scope: the same (the classify call is the same in both scopes)."""
    p = run(LUGGAGE_REPORT, behaviour, scope)
    assert p.doc(LUGGAGE_REPORT.target).document_type == "unknown"
    assert_paid_with_no_person(p)
    assert p.payable == 787.0 and p.codes == []
    # the kit control and this claim differ in nothing that the claim shows but the
    # type of one document
    assert same_outcome(p, control(LUGGAGE_REPORT))


# ------------------------------------------------ 8, 9, 10. lines and totals


@pytest.mark.parametrize("scope", SCOPES)
def test_one_line_that_is_not_confirmed_empties_the_list_and_marks_the_total_safe(
    scope: str,
) -> None:
    """Scenario 8 with a total. One line is not printed on its page: the reader stores
    no line at all and puts the total at 0.5. The routing reads the 0.5 ("a field has
    low confidence: total"), so a handler sees the claim. Both scopes."""
    p = run(INVOICE_2, "line_not_on_page", scope)
    document = p.doc(INVOICE_2.target)
    assert document.line_items == []
    marks = {f.name: f.confidence for f in document.fields}
    assert marks == {
        "invoice_number": VERIFIED,
        "invoice_date": VERIFIED,
        "provider": VERIFIED,
        "patient_name": VERIFIED,
        "total": UNVERIFIED,
    }
    assert_to_a_person(p)
    assert p.reasons == ["A field has low confidence: total (0.50)."]
    assert (
        p.payable == 756.0 and p.needs_person == []
    )  # the total alone gives the amount
    assert same_outcome(p, run(INVOICE_2, "line_not_on_page", "page"))


@pytest.mark.parametrize("scope", SCOPES)
def test_lost_lines_of_a_document_with_no_total_change_nothing_known_limit(
    scope: str,
) -> None:
    """Scenario 8 without a total (a cancellation confirmation has lines and no
    `total`). One line is not on the page: the list is empty and there is no field to
    carry the mark, so every field stays at 0.9. Nothing reads the lines of a
    cancellation (T003 needs a total; the settlement reads the refund field), so the
    claim is paid. Information was lost, but no consumer needs it: a known limit, not
    a gap. Both scopes."""
    p = run(TRAVEL_CANCEL, "line_not_on_page", scope)
    document = p.doc(TRAVEL_CANCEL.target)
    assert document.line_items == []
    assert {f.confidence for f in document.fields} == {VERIFIED}
    assert_paid_with_no_person(p)
    assert same_outcome(p, control(TRAVEL_CANCEL))


def test_only_the_tamper_check_and_the_settlement_read_line_items() -> None:
    """The pin behind the test above: on the path of a claim, `line_items` of a
    document is read by T003 (`checks/tampering.py`) and by the settlement
    (`settlement.py`), and by nothing else (`tools/documents.py` only stores them; the
    reader package, the model and the page "Reader bench" are not on that path). If a
    rule starts to read them, the known limit above must be looked at again."""
    root = Path(__file__).resolve().parents[1] / "app"
    users = {
        p.relative_to(root).as_posix()
        for p in root.rglob("*.py")
        if "line_items" in p.read_text(encoding="utf-8")
    }
    off_path = {"bench.py", "models.py"}
    on_path = {u for u in users - off_path if not u.startswith("docintel/")}
    assert on_path == {"checks/tampering.py", "settlement.py", "tools/documents.py"}


@pytest.mark.parametrize("scope", SCOPES)
def test_a_total_that_is_not_stored_while_the_lines_are_gives_no_amount_safe(
    scope: str,
) -> None:
    """Scenario 9. The lines are stored (all five), the total is not (its snippet is
    printed nowhere). T003 skips (no total), but the settlement finds no amount, and
    the routing reads it: a handler sees the claim. Safe, by the settlement alone."""
    p = run(INVOICE_2, "total_not_stored", scope)
    document = p.doc(INVOICE_2.target)
    assert "total" not in {f.name for f in document.fields}
    assert len(document.line_items) == 5
    assert "T003" not in p.codes
    assert_to_a_person(p)
    assert p.needs_person == ["No document gives the amount of the loss."]
    assert same_outcome(p, run(INVOICE_2, "total_not_stored", "page"))


@pytest.mark.parametrize("scope", SCOPES)
@pytest.mark.parametrize("left_out", ["left_out_1", "left_out_2"])
def test_lines_left_out_with_a_total_fire_t003_and_go_to_a_person_safe(
    left_out: str, scope: str
) -> None:
    """Scenario 10 with a total. The model leaves out the last one or two rows of the
    invoice (each row it gives is right, so every line is confirmed and the total keeps
    0.9). T003 fires (the lines do not add up to the total), the settlement counts the
    lines (a smaller amount) and says why, and a handler sees the claim. A false alarm
    on a right invoice, but the safe direction. Both scopes."""
    p = run(INVOICE_2, left_out, scope)
    document = p.doc(INVOICE_2.target)
    kept = 5 - int(left_out[-1])
    assert len(document.line_items) == kept
    assert {f.name: f.confidence for f in document.fields}["total"] == VERIFIED
    assert p.codes == ["T003"]
    assert_to_a_person(p)
    assert p.claim.score == 30 and p.needs_person == [
        "The lines of an invoice do not add up to its total.",
        "The claimed amount is above what the documents prove.",
    ]
    lines = round(sum(i["amount"] for i in document.line_items), 2)
    assert lines == {1: 540.0, 2: 450.0}[int(left_out[-1])] and lines < 840.0
    assert p.payable == round(lines * 0.9, 2)  # the proposal counts what was read
    assert same_outcome(p, run(INVOICE_2, left_out, "page"))


@pytest.mark.parametrize("scope", SCOPES)
def test_lines_left_out_of_a_receipt_change_nothing_known_limit(scope: str) -> None:
    """Scenario 10 without a total. A receipt has `amount_paid` and lines, and no
    `total`: T003 cannot compare them and the settlement reads `amount_paid`. A row
    that the model leaves out is invisible and harmless. A known limit. Both scopes."""
    case = Case("kit-anna-luggage", "receipt-jacket-shoes.pdf")
    before, after = control(case, scope), run(case, "left_out_1", scope)
    assert len(after.doc(case.target).line_items) == (
        len(before.doc(case.target).line_items) - 1
    )
    assert_paid_with_no_person(after)
    assert same_outcome(before, after)


# ----------------- the investigation of claim file 3 when its invoice is lost

CLAIM_3 = Case("claim-3-health", "medical-invoice.pdf")
CLAIM_3_4000 = Case("claim-3-health", "medical-invoice.pdf", amount=4000)
CLAIM_3_SIGNALS = ["C001", "R003", "R004", "R005", "T001", "T002", "T003"]


def test_claim_file_3_read_right_is_an_investigation_with_seven_signals() -> None:
    p = control(CLAIM_3)
    assert p.outcome == Outcome.investigation and p.claim.score == 130
    assert p.codes == CLAIM_3_SIGNALS
    assert (
        p.claim.fraud_report is not None and p.claim.status.value == "in_investigation"
    )


@pytest.mark.parametrize("scope", SCOPES)
@pytest.mark.parametrize("behaviour", ["http500", "cut", "lazy"])
def test_a_lost_invoice_keeps_the_investigation_at_the_threshold_but_loses_signals(
    behaviour: str, scope: str
) -> None:
    """Scenarios 1 to 3 on the tampered invoice of claim file 3. The invoice (total
    4,450 against lines that give 1,450, a patient who is not the insured person, a
    date before the incident) gives no field: T003, C001 and R005 are lost (70 of the
    130 points). The claim stays an investigation by the margin of the 60 points:
    R003 (20), R004 (10), T001 (20) and T002 (10); the last two come from the bytes of
    the PDF, not from the reading. The fraud report names none of the lost signals.

    Both scopes (one page)."""
    p = run(CLAIM_3, behaviour, scope)
    assert p.outcome == Outcome.investigation and p.claim.score == 60
    assert p.codes == ["R003", "R004", "T001", "T002"]
    report = p.claim.fraud_report
    assert report is not None
    assert {f.code for f in report.findings} == {"R003", "R004", "T001", "T002"}
    assert p.needs_person == ["No document gives the amount of the loss."]
    assert p.payable is None


@pytest.mark.parametrize("scope", SCOPES)
@pytest.mark.parametrize("behaviour", ["http500", "cut", "lazy"])
def test_a_lost_invoice_downgrades_an_investigation_to_a_review_gap(
    behaviour: str, scope: str
) -> None:
    """SAFETY GAP (a signal lost, the outcome lowered). The same claim for CHF 4,000
    (under 95 percent of the limit, so no R004, and C003 for 4,450 against 4,000).
    Read right: 135 points, an investigation with a fraud report and a case for the
    investigator. With the invoice lost: R003, T001 and T002 only, 50 points, an
    ordinary review: no fraud report, no investigation case. The system never refuses a
    claim; it also lost the reason to look hard.

    Safe behaviour: a lost document must not lower the outcome of a claim.

    Page scope: the same (one page). Pre-existing, the same mechanism as D80 (13)."""
    before, after = control(CLAIM_3_4000), run(CLAIM_3_4000, behaviour, scope)
    assert before.outcome == Outcome.investigation and before.claim.score == 135
    assert before.codes == ["C001", "C003", "R003", "R005", "T001", "T002", "T003"]
    assert (
        before.claim.fraud_report is not None and before.claim.investigation is not None
    )
    assert after.outcome == Outcome.handler_review and after.claim.score == 50
    assert after.codes == ["R003", "T001", "T002"]
    assert after.claim.fraud_report is None and after.claim.investigation is None
    assert after.claim.status.value == "in_review"
    assert (
        after.view.message == before.view.message == TO_A_PERSON
    )  # the customer sees none


@pytest.mark.parametrize("scope", SCOPES)
def test_a_total_that_is_not_stored_hides_t003_on_the_tampered_invoice_gap(
    scope: str,
) -> None:
    """SAFETY GAP (scenario 9 on the tampered invoice). Every field and every line is
    read right, but the snippet of the total is printed nowhere: the total is not
    stored. T003 needs the total, so the edited total (4,450 against lines of 1,450)
    is not seen: 100 points instead of 130. The settlement finds no amount. The claim
    here stays an investigation, with margin 40; at CHF 4,000 it would not.

    Safe behaviour: the total is the field that T003 and the settlement need: its
    absence on an invoice should be a reason to look.

    Page scope: the same. Pre-existing."""
    p = run(CLAIM_3, "total_not_stored", scope)
    assert "T003" not in p.codes and p.claim.score == 100
    assert p.outcome == Outcome.investigation
    assert len(p.doc(CLAIM_3.target).line_items) == 5
    q = run(CLAIM_3_4000, "total_not_stored", scope)  # no R004 at 4,000
    assert "T003" not in q.codes and q.claim.score == 90
    assert q.outcome == Outcome.investigation


# ------------------------------------ 11. a field attributed to the wrong page


def scan_invoice(patient: str, total: float, date: str, number: str) -> Paper:
    """A medical invoice of two pages that is a scan (images, no text layer): the
    invoice number, the date, the provider and the patient on page 1, the total on
    page 2. The model reads the scan, so no snippet can be checked."""
    provider = "Medizinzentrum Seeblick AG"
    first = [
        provider,
        "Invoice for medical treatment",
        f"Invoice no.: {number}",
        f"Date: {date}",
        f"Patient: {patient}",
        "Consultation and treatment, see the next page",
        "Page 1 of 2",
    ]
    second = ["Medical services are exempt from VAT.", f"Total CHF {total:,.2f}"]
    fields: list[Field] = [
        ("invoice_number", number, number),
        ("invoice_date", date, date),
        ("provider", provider, provider),
        ("patient_name", patient, patient),
        ("total", total, f"CHF {total:,.2f}"),
    ]
    return paper(
        "medical-invoice.pdf", "medical_invoice", [first, second], fields, scan=True
    )


CLAIM_3_SCAN = Case(
    "claim-3-health",
    "medical-invoice.pdf",
    paper=scan_invoice("Jonas Meier", 4450.0, "2026-10-18", "MZS-2026-3317"),
)
CLEAN_SCAN = Case(
    "kit-lukas-clean",
    "medical-invoice.pdf",
    paper=scan_invoice("Lukas Meier", 840.0, "2026-11-09", "GLB-2026-5521"),
)


@pytest.mark.parametrize("scope", SCOPES)
def test_a_scan_is_never_read_at_0_8_or_more_and_goes_to_a_person_safe(
    scope: str,
) -> None:
    """Scenario 5 (the other case: every field is stored at 0.5). A scan has no text
    layer: nothing checks a snippet, every field is stored at 0.5 with the box of the
    whole page, and the routing reads the low confidence. Both scopes."""
    p = run(CLEAN_SCAN, "perfect", scope)
    document = p.doc(CLEAN_SCAN.target)
    assert {f.confidence for f in document.fields} == {UNVERIFIED}
    assert all(f.source.snippet is None and f.source.box for f in document.fields)
    assert document.line_items == []
    assert_to_a_person(p)
    assert p.reasons[0].startswith("A field has low confidence: ")
    assert p.payable == 756.0  # the total of page 2 gives the amount


@pytest.mark.parametrize("scope", SCOPES)
def test_a_field_given_in_the_block_of_the_wrong_page_keeps_that_page_gap(
    scope: str,
) -> None:
    """SAFETY GAP (scenario 11, a wrong page on a scan). The model gives the total of
    page 2 in the block of page 1. On a scan nothing can check where a value is
    printed: the field is stored at 0.5 with page 1 and the box of page 1. It is
    visible in the fraud report ("medical-invoice.pdf, page 1" for a total that is
    printed on page 2) and in the evidence of R004. The claim goes to a person (0.5),
    but the person reads a wrong source.

    Safe behaviour: a page that the model claims for a value of a scan is a claim,
    and the source says so (non-negotiable 3 asks for the page).

    Page scope: the page of a field is the page of the call, so a wrong page needs the
    model to answer for page 1 with a value that it saw on page 2: impossible for a
    page scope reader that sends one image per call. The same answers give the same
    Extraction in both scopes (tested), so this test is the document scope's."""
    right, wrong = control(CLAIM_3_SCAN), run(CLAIM_3_SCAN, "wrong_page", scope)

    def total_page(p: Played) -> int:
        return p.fields(CLAIM_3_SCAN.target)["total"].source.page

    assert total_page(right) == 2
    assert total_page(wrong) == 1

    def where(p: Played) -> list[str]:
        report = p.claim.fraud_report
        assert report is not None
        return [w for f in report.findings if f.code == "R004" for w in f.where]

    assert right.codes == wrong.codes == ["C001", "R003", "R004", "R005"]
    assert where(right) == ["medical-invoice.pdf, page 2"]
    assert where(wrong) == ["medical-invoice.pdf, page 1"]
    assert wrong.outcome == right.outcome == Outcome.investigation


@pytest.mark.parametrize("scope", SCOPES)
def test_the_wrong_page_reaches_the_payment_export_after_an_approval_gap(
    scope: str,
) -> None:
    """The same wrong page on a clean claim: a handler approves it (the claim is at
    0.5 so a person sees it), the mock payment is made, and the export for the claims
    system lists the total with `page` 1, a page that does not print it.

    Safe behaviour: the export of a field whose page is a claim of the model should
    say so.

    Document scope only, for the reason of the test above."""
    wrong = run(CLEAN_SCAN, "wrong_page", scope)
    decide(wrong.store, wrong.claim.id, "approve", "Sofia Brunner", "checked")
    paid = wrong.store.get_claim(wrong.claim.id)
    assert paid is not None and paid.payment is not None
    exported = export_record(paid)
    total = next(f for f in exported.fields if f["name"] == "total")
    assert total["page"] == 1 and total["value"] == 840.0
    right = run(CLEAN_SCAN, "perfect", scope)
    decide(right.store, right.claim.id, "approve", "Sofia Brunner", "checked")
    again = right.store.get_claim(right.claim.id)
    assert again is not None
    good = next(f for f in export_record(again).fields if f["name"] == "total")
    assert good["page"] == 2


# --------------------------------- 12. a field of a rule that is missing, a refund


@pytest.mark.parametrize("scope", SCOPES)
def test_a_refund_that_is_not_read_goes_to_a_person_safe(scope: str) -> None:
    """The cancellation is read but the refund is left out (null). The settlement
    would pay the price with no refund, so it says "no document gives the refund"
    (found in pull request #122) and the routing reads it. Both scopes."""
    p = run(TRAVEL_CANCEL, "null:refund_received", scope)
    assert "refund_received" not in p.fields(TRAVEL_CANCEL.target)
    assert_to_a_person(p)
    assert p.needs_person == ["No document gives the refund of the trip."]


DATED_BEFORE = reprint(INVOICE, {"2026-11-09": "2026-11-01"})  # before the incident
DATED_BEFORE_CASE = Case("kit-lukas-clean", "medical-invoice.pdf", paper=DATED_BEFORE)


@pytest.mark.parametrize("scope", SCOPES)
def test_a_single_missing_field_makes_its_rule_skip_in_silence_gap(scope: str) -> None:
    """SAFETY GAP (scenario 12). An invoice dated 2026-11-01 for an incident of
    2026-11-06: read right, R005 fires (20 points) and a handler sees the claim. When
    the model leaves the date out (null), R005 has no date and gives no signal
    (`checks/rules.py:316`), all the other fields are at 0.9, and the claim is paid.
    One field is enough: it needs no failure of any call.

    Safe behaviour: a field that the schema asks for and the model did not give
    should be a reason to look when a rule needs it.

    Page scope: the same. Pre-existing."""
    before, after = (
        control(DATED_BEFORE_CASE, scope),
        run(DATED_BEFORE_CASE, "null:invoice_date", scope),
    )
    assert before.codes == ["R005"] and before.outcome == Outcome.handler_review
    assert "invoice_date" not in after.fields(DATED_BEFORE_CASE.target)
    assert after.codes == []
    assert_paid_with_no_person(after)
    assert after.payable == before.payable == 756.0


# ----------------------------- the kit run of the page "Business numbers", one lost


def test_the_kit_run_shows_a_lost_signal_but_not_a_lost_clean_document() -> None:
    """The seam at its highest level: `numbers.play_kit(reader)` plays the 11 stories
    of the kit through any reader and compares them with what the kit expects. With
    the invoice of claim file 3 failing in the OmniReader, the row of that story
    shows the lost signals (`signals_right` is false) and the same outcome. With the
    medical certificate of claim file 1 failing, every row is right: the page that
    measures the cost of a reading error cannot see this loss."""
    invoice = kit("claim-3-health/medical-invoice.pdf")
    certificate = kit("claim-1-travel/medical-certificate.pdf")
    routes = {}
    endpoints = []
    for p in (invoice, certificate):
        endpoint = Endpoint(model(p, "http500"))
        endpoints.append(endpoint)
        routes[hashlib.sha256(p.data).hexdigest()] = endpoint.reader("document")
    out = numbers.play_kit(Hybrid(routes))
    rows = {r["story"]: r for r in out["rows"]}
    assert out["stories"] == 11 and all(r["error"] is None for r in out["rows"])
    third = rows["claim-3-health/4450"]
    assert third["outcome"] == "investigation" and third["outcome_right"]
    assert third["signals"] == ["R003", "R004", "T001", "T002"]
    assert not third["signals_right"] and not third["payable_right"]
    first = rows["claim-1-travel/1500"]
    assert first["outcome"] == "straight_through"
    assert first["outcome_right"] and first["signals_right"] and first["payable_right"]
    assert out["fraud_caught"] == 3 and out["fraud_missed"] == 0
    assert all(e.kinds[0] == "classify" for e in endpoints if e.kinds)
    assert all(
        r["model"] == "omni-test" for e in endpoints for r in e.requests
    )  # nothing left the stub


# --------------------------------------- the evaluation reads documents only


def test_the_evaluation_harness_runs_no_claim_no_routing_no_payment() -> None:
    """The live evaluation (`app/docintel/evaluate.py`) is not blocked by any gap of
    this file: it never builds a claim. Read from its source: it imports no tool, no
    store, no settlement, no payment, no check and no `numbers`, and from the routing
    only the constant `LOW_CONFIDENCE`."""
    tree = ast.parse((Path(demo.__file__).parent / "docintel/evaluate.py").read_text())
    imported: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.setdefault(node.module, set()).update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            for a in node.names:
                imported.setdefault(a.name, set())
    app = {m for m in imported if m == "app" or m.startswith("app.")}
    assert imported["app.fraud.routing"] == {"LOW_CONFIDENCE"}
    assert imported["app.store"] == {"CLAIM_TYPE"}  # a table of the line of business
    assert imported["app"] == {"demo"}  # the list of the kit documents
    assert app == {
        "app",
        "app.docintel",  # the readers: the fixture reader and OmniReader
        "app.docintel.compare",
        "app.docintel.fixture",
        "app.docintel.omni",
        "app.docintel.reader",
        "app.fraud.routing",
        "app.models",
        "app.store",
    }
    forbidden = ("app.tools", "app.settlement", "app.payment", "app.numbers")
    assert not any(m.startswith(forbidden) for m in app)
    assert not any(m.startswith("app.checks") for m in app)


# ======================================================================================
# "No field can reach a confidence of 0.8 or more without checked evidence"
#
# What the code checks (omni.py `_field`, `_merge`; labels.py `verified`):
#   1. the snippet is a text of 1 to 120 characters (after strip);
#   2. the snippet holds the value (`_holds`: a number to the cent, a date as
#      YYYY-MM-DD or DD.MM.YYYY, a text with no difference of case or spaces);
#   3. the page has a text layer, and the snippet is printed on the page of the block
#      that gave it (`_shown`, with every number whole), else the field is not stored;
#   4. `labels.verified(kind, name, value, snippet, page_text)`: the words in front of
#      the value fit the field. True gives 0.9, False gives 0.5. For a field that is not
#      in `labels.SAFEGUARDED` it is always True; for a person it asks only that the
#      value reads as a person (never who the person is); for an organisation, that it
#      does not read as a person.
#   5. `_merge` can only lower the result: 0.5 when a page was not read, when two pages
#      give two values, or (the total) when the lines are not complete.
# This is "the evidence was verified": the text is on the page, near a fitting label.
# It is not "the extraction is right": nothing checks that the text is the value that
# the document means for that field. The tests below pin both statements.
# ======================================================================================

MOCK_PDFS = sorted(
    p.relative_to(MOCK).as_posix()
    for p in MOCK.glob("*/*.pdf")
    if p.parent.name not in {"hard", "n1", "mp1"}
)


def test_the_label_check_is_always_true_for_these_fields() -> None:
    """The fields that no label check covers: every field of the schemas that is not
    in `labels.SAFEGUARDED`. For them `labels.verified` returns True whatever the page
    says, so the whole "checked evidence" is the snippet on the text layer. Of these,
    only `repair_company` is read by a rule (R010, the provider of a closed fraud
    claim); `cause`, `diagnosis`, `offence`, `item_description`, `merchant`,
    `airline` and `damage_description` are read by no rule and no settlement."""
    names = {
        name
        for schema in SCHEMAS.values()
        for name in schema.model_fields
        if name != "line_items"
    }
    always = names - set(labels.SAFEGUARDED)
    assert always == {
        "cause",
        "diagnosis",
        "repair_company",
        "merchant",
        "item_description",
        "offence",
        "airline",
        "damage_description",
    }
    for name in always:
        assert labels.verified("receipt", name, "any text at all", "any text", "x")
        assert labels.verified("receipt", name, 12.5, "12.5", "")
    # the only one that a rule reads (checks/history.py PROVIDER_FIELDS)
    from app.checks.history import PROVIDER_FIELDS

    assert set(PROVIDER_FIELDS) & always == {"repair_company"}


def test_a_person_field_is_verified_by_the_form_of_the_name_not_by_who_it_is() -> None:
    """`labels.verified` for `patient_name`, `customer_name` and `traveller_name` is
    `_person(value)`: the value must not read as an organisation. The words in front of
    it are not read: "Policyholder: Anna Keller" and "Patient: Marco Bianchi" give the
    same verdict for the patient."""
    text = "Patient: Marco Bianchi\nPolicyholder: Anna Keller\n"
    for who in ("Marco Bianchi", "Anna Keller"):
        assert labels.verified("medical_certificate", "patient_name", who, who, text)
    assert not labels.verified(
        "medical_certificate", "patient_name", "Praxis am Lindenhof", "x", text
    )


def test_what_checked_evidence_means_in_the_reader() -> None:
    """The five checks, one by one, on one page with a text layer (`omni._field`)."""
    data = kit("claim-1-travel/booking-confirmation.pdf").data
    page = omni.pages(data)[0]

    def stored(name: str, value: Any, snippet: Any) -> Any:
        return omni._field(
            "D", 1, page, name, value, {name: snippet}, "booking_confirmation"
        )

    right = stored("trip_price", 1840.0, "CHF 1,840.00")
    assert right is not None and right.confidence == VERIFIED
    assert right.source.snippet == "CHF 1,840.00" and right.source.page == 1
    # 1. the snippet must be 1 to 120 characters
    assert stored("trip_price", 1840.0, "") is None
    assert stored("trip_price", 1840.0, "x" * 121) is None
    # 2. it must hold the value
    assert stored("trip_price", 1840.0, "CHF 1,500.00") is None
    # 3. it must be printed on the page, with whole numbers
    assert stored("trip_price", 1840.0, "CHF 1,840.01") is None
    assert stored("trip_price", 840.0, "840.00") is not None  # a row of the table
    # 4. the label: the value is stored, at 0.5
    price_of_a_row = stored("trip_price", 840.0, "840.00")
    assert price_of_a_row.confidence == UNVERIFIED
    # no text layer: the value is stored at 0.5 with a box, no check of the snippet
    blank = omni.Page(b"", "image/png", (1240, 1754), "")
    scan = omni._field(
        "D", 2, blank, "trip_price", 1840.0, {"trip_price": "x 1,840.00"}
    )
    assert scan is not None and scan.confidence == UNVERIFIED
    assert scan.source.snippet is None and scan.source.box == (0.0, 0.0, 1240.0, 1754.0)


@pytest.fixture(scope="module")
def sweep_pages() -> dict[str, list[omni.Page]]:
    return {}


def printed_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def mutations(paper: Paper, pages: list[omni.Page]) -> list[tuple[str, list[Json]]]:
    """For each field of the key, one wrong way to give it: the answer of the model
    changed in one place."""
    found: list[tuple[str, list[Json]]] = []
    base = paper.answers()
    for index, a in enumerate(base):
        text = pages[index].text
        for name, snippet in a["snippets"].items():
            value = a["values"][name]
            others = [
                line
                for line in printed_lines(text)
                if not omni._holds(line, value) and len(line) <= omni.MAX_SNIPPET
            ]

            def with_snippet(new: str, i: int = index, n: str = name) -> list[Json]:
                changed = copy.deepcopy(base)
                changed[i]["snippets"][n] = new
                return changed

            if others:
                found.append((f"{name}: another printed line", with_snippet(others[0])))
            found.append((f"{name}: the whole page", with_snippet(text)))
            start = text.find(snippet.split("\n")[0])
            if start >= 0:
                around = text[max(0, start - 60) : start + len(snippet) + 60]
                if len(around.strip()) > omni.MAX_SNIPPET:
                    found.append((f"{name}: 121 characters", with_snippet(around)))
            moved = copy.deepcopy(base)
            if len(base) > 1:
                target = (index + 1) % len(base)
                moved[target]["values"][name] = value
                moved[target]["snippets"][name] = snippet
                moved[index]["values"][name] = None
                found.append((f"{name}: in the block of another page", moved))
            if isinstance(value, float):
                changed = copy.deepcopy(base)
                changed[index]["values"][name] = value + 1.0
                found.append((f"{name}: another number", changed))
            elif isinstance(value, str) and not value[:4].isdigit():
                changed = copy.deepcopy(base)
                changed[index]["values"][name] = value + " Jr"
                found.append((f"{name}: another text", changed))
    return found


def sweep_papers() -> dict[str, Paper]:
    found = {path: kit(path) for path in MOCK_PDFS}
    found |= {
        f"mp1/{p.name}": kit(f"mp1/{p.name}")
        for p in sorted((MOCK / "mp1").glob("*.pdf"))
    }
    found |= {
        "two pages: certificate": cert2(),
        "two pages: report": report2(),
        "two pages: invoice": invoice2(),
        "two pages: police report": police2(),
    }
    return found


SWEEP = sweep_papers()


SHOWN, HOLDS = omni._shown, omni._holds  # the checks as they are, for the asserts


def sweep(name: str, sweep_pages: dict[str, list[omni.Page]]) -> int:
    """Changes every field of a document one way after the other and checks what the
    reader stores. Gives the number of fields stored at 0.8 or more (so a test can see
    that it looked at something)."""
    paper_ = SWEEP[name]
    pages = sweep_pages.setdefault(name, omni.pages(paper_.data))
    scan = not any(p.text.strip() for p in pages)
    high = 0
    scopes = SCOPES if paper_.pages > 1 else ("page",)
    for scope in scopes:
        for what, answers in mutations(paper_, pages):
            endpoint = Endpoint(
                Model({"document_type": paper_.kind}, serving(answers), blocks(answers))
            )
            out = endpoint.reader(scope).extract(
                Document(id="D-sweep", file="x", pages=paper_.pages),
                paper_.data,
                "travel",
            )
            for f in out.fields:
                if scan:
                    assert f.confidence < LOW_CONFIDENCE, (name, what)
                if f.confidence >= LOW_CONFIDENCE:
                    high += 1
                    page = pages[f.source.page - 1]
                    snippet = f.source.snippet
                    assert snippet and page.text.strip() and f.source.box is None
                    assert 0 < len(snippet.strip()) <= omni.MAX_SNIPPET, (name, what)
                    assert SHOWN(snippet, page.text), (name, what, f.name)
                    assert HOLDS(snippet, f.value), (name, what, f.name)
    return high


@pytest.mark.parametrize("name", sorted(SWEEP))
def test_no_field_reaches_0_8_without_a_snippet_printed_on_its_page(
    name: str, sweep_pages: dict[str, list[omni.Page]]
) -> None:
    """The claim of the earlier phase, tried on the kit, the MP1 set and the four
    documents of two pages: for every field of the key, the answer of the model is
    changed in one place (another printed line, the whole page, a snippet of 121
    characters, the value in the block of another page, another number or text). Every
    field that the reader still stores with a confidence of 0.8 or more has a snippet of
    at most 120 characters that is printed on the text layer of its page and holds its
    value. Both scopes for a document of two pages or more.

    This tests "the evidence was verified". It does not say that the value is right:
    see the traps below."""
    high = sweep(name, sweep_pages)
    scan = not any(p.text.strip() for p in sweep_pages[name])
    assert scan or high > 0  # the checks looked at fields that kept 0.9


def test_the_sweep_would_see_a_reader_that_does_not_check_the_page(
    monkeypatch: pytest.MonkeyPatch, sweep_pages: dict[str, list[omni.Page]]
) -> None:
    """The sweep is not empty: when the reader accepts any snippet as printed on the
    page (a mutation of `omni._shown`), the sweep finds a stored field with a snippet
    that is not on its page, in the block of another page."""
    monkeypatch.setattr(omni, "_shown", lambda snippet, text: True)
    with pytest.raises(AssertionError):
        sweep("two pages: certificate", sweep_pages)


# ---------------------------------------------------------- the traps: 0.9 and wrong


@dataclass(frozen=True, eq=False)
class Trap:
    """A claim whose document is read twice with the same file: right (what the
    document means) and wrong (what the model gives, with checked evidence)."""

    name: str
    field: str
    right: Case
    wrong: Case
    right_value: Any
    wrong_value: Any


def twin(
    name: str,
    kind: str,
    pages: list[list[str]],
    right: list[Field],
    wrong: list[Field],
    lines: list[Row] | None = None,
    wrong_lines: list[Row] | None = None,
) -> tuple[Paper, Paper]:
    """One printed document and two answer keys: what it means, and what a model can
    give with a snippet that is printed on the page."""
    return (
        paper(name, kind, pages, right, lines),
        paper(
            name, kind, pages, wrong, wrong_lines if wrong_lines is not None else lines
        ),
    )


def assert_verified(p: Played, case: Case, name: str) -> None:
    """THE EVIDENCE WAS VERIFIED: the stored field has 0.9, a snippet of at most 120
    characters that is printed on the text layer of its page and holds its value, and
    the label check says yes. Recomputed here from the stored source and the bytes."""
    f = p.fields(case.target)[name]
    page = omni.pages(case.target_paper().data)[f.source.page - 1]
    snippet = f.source.snippet
    assert f.confidence == VERIFIED and f.confidence >= LOW_CONFIDENCE
    assert snippet and 0 < len(snippet) <= omni.MAX_SNIPPET and f.source.box is None
    assert omni._shown(snippet, page.text) and omni._holds(snippet, f.value)
    assert labels.verified(case.target_paper().kind, name, f.value, snippet, page.text)


CERT_LINES = [
    "Praxis am Lindenhof",
    "Medical certificate",
    "Patient: Marco Bianchi",
    "Policyholder: Anna Keller",
    "The patient has an acute illness and no accident was reported.",
    "The patient is unfit to travel from 2026-08-03 until at least 2026-08-17.",
]
CERT_RIGHT: list[Field] = [
    ("patient_name", "Marco Bianchi", "Marco Bianchi"),
    ("cause", "illness", "acute illness"),
    ("unfit_from", "2026-08-03", "unfit to travel from 2026-08-03"),
]
PERSON_RIGHT, PERSON_WRONG = twin(
    "medical-certificate.pdf",
    "medical_certificate",
    [CERT_LINES],
    CERT_RIGHT,
    [("patient_name", "Anna Keller", "Anna Keller"), *CERT_RIGHT[1:]],
)
CAUSE_RIGHT, CAUSE_WRONG = twin(
    "medical-certificate.pdf",
    "medical_certificate",
    [CERT_LINES],
    [("patient_name", "Anna Keller", "Anna Keller"), *CERT_RIGHT[1:]],
    [
        ("patient_name", "Anna Keller", "Anna Keller"),
        ("cause", "accident", "no accident was reported"),
        CERT_RIGHT[2],
    ],
)

SUBCONTRACTOR = "Blaubach Sanitaer & Umbau AG"
FLAGGED = "Sanitaer Blitz GmbH"  # `data/mock/history.json`: two claims closed as fraud
INVOICE_LINES = [
    FLAGGED,
    "Marco Bianchi",
    "Via Nassa 18",
    "Invoice date: 2026-11-10",
    "Invoice no.: SBL-2026-0042",
    "Job: water damage in the kitchen, Via Nassa 18, 6900 Lugano",
    "Emergency call-out and leak detection 1 380.00 380.00",
    "Replace damaged supply pipe under the sink 1 820.00 820.00",
    f"Work done with the subcontractor {SUBCONTRACTOR}",
    "Total CHF 1,200.00",
]
INVOICE_ROWS: list[Row] = [
    (1, row("Emergency call-out and leak detection", 1, 380.0, 380.0)),
    (1, row("Replace damaged supply pipe under the sink", 1, 820.0, 820.0)),
]
INVOICE_RIGHT: list[Field] = [
    ("invoice_number", "SBL-2026-0042", "SBL-2026-0042"),
    ("invoice_date", "2026-11-10", "2026-11-10"),
    ("repair_company", FLAGGED, FLAGGED),
    ("total", 1200.0, "CHF 1,200.00"),
    ("customer_name", "Marco Bianchi", "Marco Bianchi"),
]
COMPANY_RIGHT, COMPANY_WRONG = twin(
    "repair-invoice.pdf",
    "repair_invoice",
    [INVOICE_LINES],
    INVOICE_RIGHT,
    [
        *INVOICE_RIGHT[:2],
        ("repair_company", SUBCONTRACTOR, SUBCONTRACTOR),
        *INVOICE_RIGHT[3:],
    ],
    INVOICE_ROWS,
)
QUOTE_LINES = [
    SUBCONTRACTOR,
    "Marco Bianchi",
    "Via Nassa 18",
    "Quote date: 2026-11-05",
    "Quote no.: BSU-Q-2026-0311",
    "Repair of the water damage in the kitchen, as discussed on site.",
    "Total CHF 1,200.00",
    "Optional extras, not ordered: new worktop and splashback.",
    "Total with options CHF 1,650.00",
]
QUOTE_RIGHT_FIELDS: list[Field] = [
    ("quote_number", "BSU-Q-2026-0311", "BSU-Q-2026-0311"),
    ("quote_date", "2026-11-05", "2026-11-05"),
    ("repair_company", SUBCONTRACTOR, SUBCONTRACTOR),
    ("total", 1200.0, "CHF 1,200.00"),
    ("customer_name", "Marco Bianchi", "Marco Bianchi"),
]
OPTIONS_RIGHT, OPTIONS_WRONG = twin(
    "repair-invoice.pdf",
    "repair_quote",
    [QUOTE_LINES],
    QUOTE_RIGHT_FIELDS,
    [*QUOTE_RIGHT_FIELDS[:3], ("total", 1650.0, "CHF 1,650.00"), QUOTE_RIGHT_FIELDS[4]],
)

# a tampered invoice: three rows, then the two last rows, and a total that the rows
# do not give (840.00 of rows against a printed total of 750.00)
ROWS_LINES = [
    "Gesundheitszentrum Limmatblick AG",
    "Lukas Meier",
    "Invoice no.: GLB-2026-5521",
    "Invoice date: 2026-11-09",
    "Patient: Lukas Meier",
    "Consultation, first visit, 30 min 1 180.00 180.00",
    "X-ray of the wrist, 2 views 1 160.00 160.00",
    "Wrist support, incl. fitting 1 110.00 110.00",
    "Follow-up consultation, 15 min 1 90.00 90.00",
    "Physiotherapy session, 30 min 2 150.00 300.00",
    "Total CHF 750.00",
]
ROWS_FIELDS: list[Field] = [
    ("invoice_number", "GLB-2026-5521", "GLB-2026-5521"),
    ("invoice_date", "2026-11-09", "2026-11-09"),
    (
        "provider",
        "Gesundheitszentrum Limmatblick AG",
        "Gesundheitszentrum Limmatblick AG",
    ),
    ("patient_name", "Lukas Meier", "Patient: Lukas Meier"),
    ("total", 750.0, "CHF 750.00"),
]
ROWS_RIGHT_LINES: list[Row] = [
    (1, row("Consultation, first visit, 30 min", 1, 180.0, 180.0)),
    (1, row("X-ray of the wrist, 2 views", 1, 160.0, 160.0)),
    (1, row("Wrist support, incl. fitting", 1, 110.0, 110.0)),
    (1, row("Follow-up consultation, 15 min", 1, 90.0, 90.0)),
    (1, row("Physiotherapy session, 30 min", 2, 150.0, 300.0)),
]
# the model joins two rows: the words of one row with the numbers of the next
ROWS_WRONG_LINES: list[Row] = [
    *ROWS_RIGHT_LINES[:3],
    (1, row("Follow-up consultation, 15 min", 2, 150.0, 300.0)),
]
ROWS_RIGHT, ROWS_WRONG = twin(
    "medical-invoice.pdf",
    "medical_invoice",
    [ROWS_LINES],
    ROWS_FIELDS,
    ROWS_FIELDS,
    ROWS_RIGHT_LINES,
    ROWS_WRONG_LINES,
)

TWICE_LINES = [
    *ROWS_LINES[:3],
    "Invoice date: 2026-11-01",
    "Invoice date (re-issued): 2026-11-09",
    *ROWS_LINES[4:5],
    "Total CHF 840.00",
]
TWICE_FIELDS: list[Field] = [
    ("invoice_number", "GLB-2026-5521", "GLB-2026-5521"),
    ("invoice_date", "2026-11-01", "Invoice date: 2026-11-01"),
    (
        "provider",
        "Gesundheitszentrum Limmatblick AG",
        "Gesundheitszentrum Limmatblick AG",
    ),
    ("patient_name", "Lukas Meier", "Patient: Lukas Meier"),
    ("total", 840.0, "CHF 840.00"),
]
DATE_RIGHT, DATE_WRONG = twin(
    "medical-invoice.pdf",
    "medical_invoice",
    [TWICE_LINES],
    TWICE_FIELDS,
    [
        TWICE_FIELDS[0],
        ("invoice_date", "2026-11-09", "Invoice date (re-issued): 2026-11-09"),
        *TWICE_FIELDS[2:],
    ],
)
TRIP_LINES = [
    "Wolkenpfad Reisen AG",
    "Booking reference: WPR-482913",
    "Traveller: Anna Keller",
    "Departure: 2026-08-10",
    "Total trip price CHF 1,840.00 for two travellers",
    "Deposit CHF 920.00",
    "Balance CHF 920.00",
    "Total price per traveller CHF 920.00",
]
TRIP_FIELDS: list[Field] = [
    ("booking_reference", "WPR-482913", "WPR-482913"),
    ("traveller_name", "Anna Keller", "Anna Keller"),
    ("departure_date", "2026-08-10", "Departure: 2026-08-10"),
    ("trip_price", 1840.0, "CHF 1,840.00"),
]
PRICE_RIGHT, PRICE_WRONG = twin(
    "booking-confirmation.pdf",
    "booking_confirmation",
    [TRIP_LINES],
    TRIP_FIELDS,
    [*TRIP_FIELDS[:3], ("trip_price", 920.0, "Total price per traveller CHF 920.00")],
)


def traps() -> dict[str, Trap]:
    def pair(
        folder: str, pair_: tuple[Paper, Paper], amount: float | None = None
    ) -> tuple[Case, Case]:
        right, wrong = pair_
        return (
            Case(folder, right.name, amount, paper=right),
            Case(folder, wrong.name, amount, paper=wrong),
        )

    return {
        "a wrong person": Trap(
            "a wrong person",
            "patient_name",
            *pair("claim-1-travel", (PERSON_RIGHT, PERSON_WRONG)),
            "Marco Bianchi",
            "Anna Keller",
        ),
        "a category field": Trap(
            "a category field",
            "cause",
            *pair("claim-1-travel", (CAUSE_RIGHT, CAUSE_WRONG)),
            "illness",
            "accident",
        ),
        "a wrong company": Trap(
            "a wrong company",
            "repair_company",
            *pair("kit-marco-bathroom", (COMPANY_RIGHT, COMPANY_WRONG), 1200),
            FLAGGED,
            SUBCONTRACTOR,
        ),
        "total with options": Trap(
            "total with options",
            "total",
            *pair("kit-marco-bathroom", (OPTIONS_RIGHT, OPTIONS_WRONG), 1650),
            1200.0,
            1650.0,
        ),
        "two rows made into one": Trap(
            "two rows made into one",
            "total",
            *pair("kit-lukas-clean", (ROWS_RIGHT, ROWS_WRONG), 750),
            5,
            4,
        ),
        "a date printed twice": Trap(
            "a date printed twice",
            "invoice_date",
            *pair("kit-lukas-clean", (DATE_RIGHT, DATE_WRONG)),
            "2026-11-01",
            "2026-11-09",
        ),
        "an amount printed twice": Trap(
            "an amount printed twice",
            "trip_price",
            *pair("claim-1-travel", (PRICE_RIGHT, PRICE_WRONG)),
            1840.0,
            920.0,
        ),
    }


TRAPS = traps()


@pytest.mark.parametrize("scope", SCOPES)
@pytest.mark.parametrize("label", sorted(TRAPS))
def test_the_evidence_is_verified_and_the_extraction_is_wrong(
    label: str, scope: str
) -> None:
    """Two statements, kept apart. "The evidence was verified": each wrong field below
    is stored at 0.9, its snippet is printed on the text layer of its page, holds its
    value, is at most 120 characters, and the label check says yes. "The extraction is
    right": it is not. The right reading of the same file has the same confidence and
    the same kind of evidence, so the confidence cannot tell them apart.

    Both scopes (the same answers give the same Extraction in both)."""
    trap = TRAPS[label]
    right, wrong = control(trap.right, scope), control(trap.wrong, scope)
    if label == "two rows made into one":
        assert trap.field == "total"  # the total is right; the lines are not
        assert_verified(right, trap.right, "total")
        assert_verified(wrong, trap.wrong, "total")
        assert len(right.doc(trap.right.target).line_items) == trap.right_value
        assert len(wrong.doc(trap.wrong.target).line_items) == trap.wrong_value
        merged = wrong.doc(trap.wrong.target).line_items[-1]
        text = omni.pages(trap.wrong.target_paper().data)[0].text
        assert omni._on_page(merged, text)  # every number of the row is on the page
        assert merged not in right.doc(trap.right.target).line_items  # but not one row
        return
    assert_verified(right, trap.right, trap.field)
    assert_verified(wrong, trap.wrong, trap.field)
    assert right.fields(trap.right.target)[trap.field].value == trap.right_value
    assert wrong.fields(trap.wrong.target)[trap.field].value == trap.wrong_value
    assert trap.right_value != trap.wrong_value
    for_both = {f.confidence for f in right.doc(trap.right.target).fields}
    assert for_both == {f.confidence for f in wrong.doc(trap.wrong.target).fields}


@pytest.mark.parametrize("scope", SCOPES)
def test_a_wrong_person_with_checked_evidence_hides_c001_gap(scope: str) -> None:
    """SAFETY GAP. The certificate prints "Patient: Marco Bianchi" and, in the next
    line, "Policyholder: Anna Keller". The model gives the policyholder as the patient.
    "Anna Keller" is printed on the page and reads as a person, so it is stored at 0.9:
    the label check for a person does not read the words in front of the name. C001
    does not fire and the claim is paid. Read right, C001 sends it to a handler.

    Safe behaviour: a person field needs the label ("Patient:") as the id fields do.
    Page scope: the same."""
    trap = TRAPS["a wrong person"]
    right, wrong = control(trap.right, scope), control(trap.wrong, scope)
    assert (right.outcome, right.codes, right.claim.score) == (
        Outcome.handler_review,
        ["C001"],
        20,
    )
    assert_paid_with_no_person(wrong)
    assert wrong.codes == [] and wrong.payable == right.payable == 1500.0


@pytest.mark.parametrize("scope", SCOPES)
def test_a_wrong_company_with_checked_evidence_hides_r010_gap(scope: str) -> None:
    """SAFETY GAP. The invoice is from a company that has two claims closed as fraud
    (`data/mock/history.json`) and says "Work done with the subcontractor Blaubach
    ...". The model gives the subcontractor as `repair_company`. `repair_company` is
    not in `labels.SAFEGUARDED`: any text on the page that holds the value is
    verified, at 0.9. R010 reads the company, finds no history, and does not fire. The
    claim is paid. Read right, R010 sends it to a handler.

    Safe behaviour: the field that R010 reads needs a label check too.
    Page scope: the same."""
    trap = TRAPS["a wrong company"]
    right, wrong = control(trap.right, scope), control(trap.wrong, scope)
    assert right.codes == ["R010"] and right.outcome == Outcome.handler_review
    assert_paid_with_no_person(wrong)
    assert wrong.codes == [] and wrong.payable == right.payable == 1000.0


@pytest.mark.parametrize("scope", SCOPES)
def test_a_total_with_options_with_checked_evidence_overpays_gap(scope: str) -> None:
    """SAFETY GAP, a wrong payment. The quote prints "Total CHF 1,200.00" and, for the
    options that were not ordered, "Total with options CHF 1,650.00". The customer
    claims 1,650. The model gives 1,650 as the total (the label contains "Total" and
    the number is printed on the page: 0.9). The documents then "prove" the claim:
    nothing in the claim differs, and CHF 1,450 are paid (1,650 less the deductible),
    CHF 450 more than the quote gives. Read right, the settlement says "the claimed
    amount is above what the documents prove" and a handler decides.

    Safe behaviour: two totals on a page are a reason to look.
    Page scope: the same (one page); in a quote of several pages, the same."""
    trap = TRAPS["total with options"]
    right, wrong = control(trap.right, scope), control(trap.wrong, scope)
    assert right.outcome == Outcome.handler_review and right.payable == 1000.0
    assert right.needs_person == [
        "The claimed amount is above what the documents prove."
    ]
    assert_paid_with_no_person(wrong)
    assert wrong.claim.payment is not None and wrong.claim.payment.amount == 1450.0
    assert wrong.payable == 1450.0 and wrong.codes == []


@pytest.mark.parametrize("scope", SCOPES)
def test_two_rows_made_into_one_with_checked_evidence_hide_t003_gap(scope: str) -> None:
    """SAFETY GAP. The invoice prints five rows (840.00) and a total of 750.00 (an
    edited total). Read right, T003 fires. The model joins the words of the fourth row
    with the numbers of the fifth: each value of that line is printed on the page, so
    the line is confirmed, and the four lines add up to 750.00. T003 is silent, the
    claim for 750 is paid. The list of lines has no confidence of its own; the total
    keeps 0.9.

    Safe behaviour: a line is checked as a row of the table, not as values that are
    somewhere on the page. Page scope: the same."""
    trap = TRAPS["two rows made into one"]
    right, wrong = control(trap.right, scope), control(trap.wrong, scope)
    assert right.codes == ["T003"] and right.outcome == Outcome.handler_review
    assert right.needs_person == ["The lines of an invoice do not add up to its total."]
    assert_paid_with_no_person(wrong)
    assert wrong.codes == [] and wrong.payable == right.payable == 675.0
    lines = wrong.doc(trap.wrong.target).line_items
    assert round(sum(i["amount"] for i in lines), 2) == 750.0


@pytest.mark.parametrize("scope", SCOPES)
def test_a_date_printed_twice_with_checked_evidence_hides_r005_gap(scope: str) -> None:
    """SAFETY GAP. The invoice prints "Invoice date: 2026-11-01" (before the incident of
    2026-11-06) and "Invoice date (re-issued): 2026-11-09". The model gives the second.
    Both dates are printed with a fitting label, so it is stored at 0.9. R005 sees a
    date after the incident and is silent: the claim is paid. Read as the first
    issue, R005 sends it to a handler.

    Safe behaviour: a field printed twice with two values is a conflict to show.
    Page scope: the same (the conflict rule needs two pages)."""
    trap = TRAPS["a date printed twice"]
    right, wrong = control(trap.right, scope), control(trap.wrong, scope)
    assert right.codes == ["R005"] and right.outcome == Outcome.handler_review
    assert_paid_with_no_person(wrong)
    assert wrong.codes == [] and wrong.payable == right.payable == 756.0


@pytest.mark.parametrize("scope", SCOPES)
def test_a_wrong_category_with_checked_evidence_changes_nothing_known_limit(
    scope: str,
) -> None:
    """The model gives `cause` "accident" for a certificate that says "an acute illness
    and no accident was reported". The snippet is printed, holds the word, and `cause`
    has no label check: 0.9. No rule and no settlement reads the `cause` of a document
    (`test_the_label_check_is_always_true_for_these_fields`), so the claim is the same
    as the right one. A known limit: the declared cause of the customer is never
    compared with the cause in the certificate. Page scope: the same."""
    trap = TRAPS["a category field"]
    right, wrong = control(trap.right, scope), control(trap.wrong, scope)
    assert same_outcome(right, wrong)
    assert_paid_with_no_person(wrong)
    assert wrong.fields(trap.wrong.target)["cause"].value == "accident"


@pytest.mark.parametrize("scope", SCOPES)
def test_an_amount_printed_twice_is_caught_by_the_settlement_safe(scope: str) -> None:
    """The booking prints 920.00 three times (deposit, balance, price per traveller).
    The model gives the price per traveller as the trip price: 0.9. The documented loss
    falls to 580 (920 less the refund of 340), below the claim of 1,500, and the
    settlement says so: a handler sees the claim (C003 also fires, 15 points). The
    wrong amount is caught by the settlement, not by the confidence."""
    trap = TRAPS["an amount printed twice"]
    right, wrong = control(trap.right, scope), control(trap.wrong, scope)
    assert_paid_with_no_person(right)
    assert_to_a_person(wrong)
    assert wrong.codes == ["C003"] and wrong.payable == 580.0
    assert wrong.needs_person == [
        "The claimed amount is above what the documents prove."
    ]


# the label check does catch a value taken from a row that has another label


def test_the_label_check_catches_a_value_taken_from_another_label_safe() -> None:
    """The control of the traps: the same page, the same amount (920.00), but the
    snippet is the deposit line. The label "Deposit" is the label of another thing: the
    field is stored at 0.5, and the claim goes to a person with a low-confidence
    reason. What the label check covers is a wrong label; it does not cover a wrong
    meaning behind a label that fits."""
    deposit = paper(
        "booking-confirmation.pdf",
        "booking_confirmation",
        [TRIP_LINES],
        [*TRIP_FIELDS[:3], ("trip_price", 920.0, "Deposit CHF 920.00")],
    )
    case = Case("claim-1-travel", deposit.name, paper=deposit)
    for scope in SCOPES:
        p = control(case, scope)
        price = p.fields(case.target)["trip_price"]
        assert price.value == 920.0 and price.confidence == UNVERIFIED
        assert_to_a_person(p)
        assert "A field has low confidence: trip_price (0.50)." in p.reasons


# ----------------------------------------- what is sent, and what the panel and the
# customer see when a document is lost


def images_of(request: Json) -> int:
    [message] = request["messages"]
    return len([p for p in message["content"] if p["type"] == "image_url"])


@pytest.mark.parametrize("behaviour", ["perfect", "http500", "http400"])
def test_the_document_scope_sends_one_request_with_every_page_and_no_other(
    behaviour: str,
) -> None:
    """The shape of the traffic for a document of two pages: the page scope sends one
    request per page (one image each), the document scope sends one request with both
    images, and after a failure no other request of any kind (no fallback to the pages).
    A 500 is retried twice (three requests); a 400 is final."""
    case = CERT_2
    page = run(case, behaviour, "page").endpoints[case.target]
    document = run(case, behaviour, "document").endpoints[case.target]
    assert page.kinds[0] == document.kinds[0] == "classify"
    assert all(images_of(r) == 1 for r in page.requests[1:])
    tries = {"perfect": 1, "http500": 3, "http400": 1}[behaviour]
    # every page fails the same way: the tries of page 1, then the tries of page 2
    assert page.kinds == ["classify", *["page"] * tries * 2]
    assert document.kinds == ["classify", *["document"] * tries]
    assert [images_of(r) for r in document.requests[1:]] == [2] * tries
    assert all(r["model"] == "omni-test" for r in page.requests + document.requests)


@pytest.mark.parametrize("scope", SCOPES)
@pytest.mark.parametrize("label", sorted(NO_AMOUNT))
def test_the_customer_view_of_a_claim_with_a_lost_document_is_the_normal_one(
    label: str, scope: str
) -> None:
    """Non-negotiable 2 and the gap: the customer reads the same words as after a
    perfect reading ("Your claim is validated ..."), and never a word of the checks.
    The view cannot tell the customer that a document was not read, and must not."""
    case = NO_AMOUNT[label]
    lost, fine = run(case, "http500", scope), control(case, scope)
    assert lost.view.message == fine.view.message == VALIDATED
    assert lost.view.status == fine.view.status == "paid"
    assert lost.view.status_line == fine.view.status_line
    for word in ("fraud", "score", "signal", "suspicious", "tamper"):
        assert word not in lost.view.model_dump_json().lower()


@pytest.mark.parametrize("scope", SCOPES)
@pytest.mark.parametrize(
    "case", [CLAIM_3, CLAIM_3_4000, TRAVEL_BOOKING, TRAVEL_CERT, CERT_2_OTHER]
)
def test_no_outcome_is_a_rejection_and_the_customer_reads_no_fraud_word(
    case: Case, scope: str
) -> None:
    """Non-negotiables 1 and 2 on the claims of this file, lost document or not: the
    outcome is one of the three, no text of the claim holds a word of refusal, and the
    customer view holds no word of fraud, also for an investigation."""
    for behaviour in ("perfect", "http500"):
        p = run(case, behaviour, scope)
        assert p.outcome in set(Outcome)
        words = " ".join([p.view.model_dump_json(), *p.reasons]).lower()
        for banned in ("reject", "deny", "denied", "declin"):
            assert banned not in words
        shown = p.view.model_dump_json().lower()
        for banned in ("fraud", "score", "investigat"):
            assert banned not in shown


@pytest.mark.parametrize("scope", SCOPES)
@pytest.mark.parametrize("label", sorted(NO_AMOUNT))
def test_the_pipeline_shows_no_error_for_a_lost_document_gap(
    label: str, scope: str
) -> None:
    """SAFETY GAP in what a person watching sees. The tool `extract_document` returns
    success: its event holds the type and an empty list of fields, no `error`, and the
    agent gets the type only (`visible=("document_type",)`). Nothing in the events of
    the claim says that a call failed or that the answer was cut. Each tool call still
    writes one event (non-negotiable 5): one classify and one extract for each
    document, then the checks and the routing.

    Safe behaviour: a read that failed should be an event that says so."""
    case = NO_AMOUNT[label]
    p = run(case, "http500", scope)
    document = p.doc(case.target)
    extract = [
        e
        for e in p.events
        if e.stage == "extract_document" and e.inputs["document_id"] == document.id
    ]
    classify = [
        e
        for e in p.events
        if e.stage == "classify_document" and e.inputs["document_id"] == document.id
    ]
    assert len(extract) == len(classify) == 1
    assert extract[0].outputs["fields"] == [] and extract[0].outputs["line_items"] == []
    assert extract[0].outputs["document_type"] == case.target_paper().kind
    assert not any("error" in e.outputs for e in p.events)
    last = ("run_checks", "check_tampering", "check_consistency", "assess_and_route")
    assert tuple(e.stage for e in p.events if e.stage in last) == last


# ------------------------------------------------ what the chat tells the customer


def chat_reply(path: str, behaviour: str, scope: str, tmp: Path) -> str:
    """What the scripted agent (the `local` mode) answers to a customer who uploads
    this kit document, when the model does `behaviour`. The agent reaches the reader
    only through the gateway tools `classify_document` and `extract_document`."""
    from app.agent.scripted import ScriptedAgent

    uploaded = kit(path)
    endpoint = Endpoint(model(uploaded, behaviour))
    routes = {hashlib.sha256(uploaded.data).hexdigest(): endpoint.reader(scope)}
    store = Store(":memory:")
    gateway = build_gateway(store, Hybrid(routes), tmp)
    claim = store.create_claim("C-1001")
    assert claim is not None
    (tmp / claim.id).mkdir()
    (tmp / claim.id / "D-0.pdf").write_bytes(uploaded.data)
    store.save_document(
        claim.id,
        Document(
            id="D-1",
            file=f"{claim.id}/D-0.pdf",
            pages=uploaded.pages,
            file_metadata={"original_name": uploaded.name},
        ),
    )
    reply: str = ScriptedAgent(gateway)._read_document(claim.id, "D-1")
    assert all(r["model"] == "omni-test" for r in endpoint.requests)
    return reply


@pytest.mark.parametrize("scope", SCOPES)
@pytest.mark.parametrize("behaviour", ["http500", "lazy", "cut", "invented"])
def test_the_chat_says_it_read_a_document_that_gave_no_field_gap(
    behaviour: str, scope: str, tmp_path: Path
) -> None:
    """SAFETY GAP, in what the customer is told. The agent of the `local` mode answers
    "Thank you. I read this as a medical certificate." whenever the type is known: it
    does not look at the fields (`app/agent/scripted.py`, `_read_document`). The agent
    of the other modes gets `document_type` only from `extract_document`
    (`tools/documents.py:56`). So the customer, and the agent, are told that a document
    was read when no field was read.

    Safe behaviour: say "I could not read this document, a person will look at it" and
    let the customer send another file. Page scope: the same."""
    path = "claim-1-travel/medical-certificate.pdf"
    assert (
        chat_reply(path, behaviour, scope, tmp_path)
        == "Thank you. I read this as a medical certificate."
    )


@pytest.mark.parametrize("behaviour", ["classify_500", "classify_junk"])
def test_the_chat_says_it_could_not_recognise_a_document_that_was_not_classified(
    behaviour: str, tmp_path: Path
) -> None:
    """The other side: a document whose type is unknown gets an honest sentence, and
    the customer may send it again."""
    path = "claim-1-travel/medical-certificate.pdf"
    assert (
        chat_reply(path, behaviour, "document", tmp_path)
        == "I could not recognise this document."
    )
