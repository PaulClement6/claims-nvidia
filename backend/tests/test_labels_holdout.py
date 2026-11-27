"""A second check of the reader safeguards (issue #125, phase 2).

Written from the specification (the public names of `app.docintel.labels`, the field
definitions and the reader contract), on other layouts: a hotel folio, an airline
irregularity report, a quote, a two-page invoice and a booking overview. Each case
builds a real PDF with a text layer, runs `OmniReader.extract` with a stubbed model
answer and checks the confidence (0.9 or 0.5), the value and the source. A second
group calls `labels.verified` and `labels.is_placeholder` directly. No network:
every model call goes to an httpx MockTransport.

This is NOT an independent held-out set. It ran once before the rules were final
(548 of 551 cases passed); after that run one row was changed to fit a later
decision, and the rules were corrected on its result. The German, French and
Italian layouts and rows (that changed row among them) went out with the word
tables after the review of pull request #128: the file is English only now. A real
held-out set, with documents that the author of the rules has not seen, is still
to do.
"""

import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest
from reportlab.pdfgen import canvas  # type: ignore[import-untyped]

from app.docintel import labels
from app.docintel.omni import UNVERIFIED, VERIFIED, OmniReader
from app.docintel.schemas import SCHEMAS
from app.models import Document, DocumentType

URL = "https://vlm.holdout.test/v1"


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVIDIA_KEY", "holdout-key-value")
    monkeypatch.setenv("VLM_BASE_URL", URL)
    monkeypatch.setenv("VLM_MODEL", "omni-holdout")


class Stub:
    """Answers the requests in order: classify first, then one answer per page. A
    request beyond the planned answers, or to another URL, fails the test."""

    def __init__(self, *replies: dict[str, Any]) -> None:
        self.replies: Iterator[dict[str, Any]] = iter(replies)
        self.count = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert str(request.url) == f"{URL}/chat/completions"
        self.count += 1
        reply = next(self.replies, None)
        assert reply is not None, "an unexpected model request"
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(reply)}}]}
        )

    def reader(self) -> OmniReader:
        client = httpx.Client(transport=httpx.MockTransport(self.handle))
        return OmniReader(client=client, sleep=lambda _s: None)


def build_pdf(pages: list[list[str]]) -> bytes:
    """A PDF with a text layer: one string per line, top down; "" is a blank line."""
    out = BytesIO()
    pdf = canvas.Canvas(out)
    for lines in pages:
        pdf.setFont("Helvetica", 10)
        y = 800
        for line in lines:
            if line:
                pdf.drawString(50, y, line)
            y -= 14
        pdf.showPage()
    pdf.save()
    return out.getvalue()


def page_answer(kind: str, fields: dict[str, tuple[Any, str]]) -> dict[str, Any]:
    """A model answer for a page: the given (value, snippet) per field, null for
    every other field, no line items."""
    schema = SCHEMAS[DocumentType(kind)]
    values: dict[str, Any] = {name: None for name in schema.model_fields}
    if "line_items" in values:
        values["line_items"] = []
    values.update({name: value for name, (value, _) in fields.items()})
    return {
        "values": values,
        "snippets": {name: snippet for name, (_, snippet) in fields.items()},
    }


def read(
    tmp_path: Path, kind: str, pages: list[list[str]], answers: list[dict[str, Any]]
) -> Any:
    path = tmp_path / "doc.pdf"
    path.write_bytes(build_pdf(pages))
    stub = Stub({"document_type": kind}, *answers)
    out = stub.reader().extract(
        Document(id="D-hold", file=path.name, pages=len(pages)),
        path.read_bytes(),
        "travel",
    )
    assert stub.count == 1 + len(pages)
    assert out.document_type == kind
    return out


# --------------------------------------------------------------------------------
# The layouts: (document type, pages of lines)
# --------------------------------------------------------------------------------

LAYOUTS: dict[str, tuple[str, list[list[str]]]] = {
    "folio": (
        "booking_confirmation",
        [
            [
                "Alpenrose Hotel & Spa AG",
                "Dorfstrasse 8, 3818 Grindelwald",
                "",
                "Guest Folio",
                "Guest: Mr. Ruedi Haldimann",
                "Folio No.: 88317",
                "Booking reference: HLF-77Q4T",
                "Arrival: 15.06.2027",
                "Departure: 18.06.2027",
                "Room 214, 3 nights",
                "Room charge 3 x 310.00   930.00",
                "Breakfast 3 x 24.00   72.00",
                "Deposit paid CHF 300.00",
                "Total price CHF 1'002.00",
            ]
        ],
    ),
    "pir": (
        "luggage_report",
        [
            [
                "Nordwind Airlines Ltd. - Baggage Service Zurich",
                "Property Irregularity Report",
                "File reference: ZRH-NW-27-045511",
                "Passenger: Elena Marchetti",
                "Ticket number: 724 2209918345",
                "Flight: NW 412 on 23.07.2027",
                "Date of incident: 23.07.2027",
                "Date reported: 24.07.2027",
                "Date of birth: 02.02.1979",
                "Bag tag: NW 884420",
                "Phone: +41 44 555 01 90",
                "Description: black hard-shell suitcase, delayed",
                "Airline: Nordwind Airlines Ltd.",
            ]
        ],
    ),
    "quote_en": (
        "repair_quote",
        [
            [
                "Hartmann Auto Body Ltd",
                "Quote",
                "Quote no.: HQ-55102",
                "Quote date: 03.03.2027",
                "Valid until: 02.04.2027",
                "Customer: Pia Hartmann-Roth",
                "Order no.: 88-1204",
                "VAT no.: CHE-555.666.777",
                "Labour 6 h   720.00",
                "Paint materials   410.00",
                "VAT 8.1%   91.53",
                "Total CHF 1'221.53",
            ]
        ],
    ),
    "invoice_two_pages": (
        "repair_invoice",
        [
            [
                "Carrosserie Bachmann AG",
                "Invoice no. B-27-3392",
                "Date: 30.07.2027",
                "Customer: Marco Fontana",
                "Replace side panel   1   1'640.00",
                "Paint work   1   500.00",
                "Subtotal   2'140.00",
                "Carried forward to page 2",
            ],
            [
                "Carrosserie Bachmann AG",
                "Invoice no. B-27-3392",
                "Customer: Marco Fontana",
                "Rental car 3 days   390.00",
                "Subtotal   2'530.00",
                "VAT 8.1%   204.93",
                "Total CHF 2'734.93",
            ],
        ],
    ),
    "booking_overview": (
        "booking_confirmation",
        [
            [
                "Booking overview (agency copy)",
                "Booking reference: XKQZPL",
                "Booking reference: ABCDEF-GH",
                "Booking reference: pending",
                "Booking reference: to be confirmed by e-mail",
                "Booking reference: BK 2027 00417",
                "Booking reference: a1b2c3",
                "Booking reference: unavailable",
                "Booking reference: the number is on your ticket",
            ]
        ],
    ),
}


@dataclass(frozen=True)
class Case:
    layout: str
    name: str
    value: Any
    snippet: str
    expect: float | None  # VERIFIED, UNVERIFIED, or None: the field is not stored
    page: int = 1

    def param(self) -> Any:
        label = f"{self.layout}|{self.name}|{self.snippet[:36]}|{self.expect}"
        return pytest.param(self, id=label)


def C(layout: str, name: str, value: Any, snippet: str, expect: float | None) -> Case:
    return Case(layout, name, value, snippet, expect)


V, U, X = VERIFIED, UNVERIFIED, None  # verified, unverified, not stored

CASES: list[Case] = [
    # ---- hotel folio
    C("folio", "booking_reference", "HLF-77Q4T", "Booking reference: HLF-77Q4T", V),
    C("folio", "booking_reference", "HLF-77Q4T", "HLF-77Q4T", V),
    C("folio", "traveller_name", "Ruedi Haldimann", "Guest: Mr. Ruedi Haldimann", V),
    C(
        "folio",
        "traveller_name",
        "Alpenrose Hotel & Spa AG",
        "Alpenrose Hotel & Spa AG",
        U,
    ),
    C("folio", "departure_date", "2027-06-18", "Departure: 18.06.2027", V),
    C("folio", "departure_date", "2027-06-18", "18.06.2027", V),
    C("folio", "departure_date", "2027-06-15", "Arrival: 15.06.2027", U),
    C("folio", "departure_date", "2027-06-15", "15.06.2027", U),
    C("folio", "trip_price", 1002.0, "Total price CHF 1'002.00", V),
    C("folio", "trip_price", 1002.0, "1'002.00", V),
    C("folio", "trip_price", 300.0, "Deposit paid CHF 300.00", U),
    C("folio", "trip_price", 930.0, "930.00", U),
    C("folio", "trip_price", 72.0, "Breakfast 3 x 24.00   72.00", U),
    # ---- airline irregularity report
    C(
        "pir",
        "report_reference",
        "ZRH-NW-27-045511",
        "File reference: ZRH-NW-27-045511",
        V,
    ),
    C("pir", "report_reference", "ZRH-NW-27-045511", "ZRH-NW-27-045511", V),
    C("pir", "report_reference", "724 2209918345", "Ticket number: 724 2209918345", U),
    C("pir", "report_reference", "NW 884420", "Bag tag: NW 884420", U),
    C("pir", "report_reference", "NW 412", "Flight: NW 412 on 23.07.2027", U),
    C("pir", "report_reference", "+41 44 555 01 90", "Phone: +41 44 555 01 90", U),
    C("pir", "incident_date", "2027-07-23", "Date of incident: 23.07.2027", V),
    C("pir", "incident_date", "2027-07-23", "23.07.2027", V),
    C("pir", "incident_date", "2027-07-24", "Date reported: 24.07.2027", U),
    C("pir", "incident_date", "1979-02-02", "Date of birth: 02.02.1979", U),
    C("pir", "incident_date", "2027-07-23", "Flight: NW 412 on 23.07.2027", U),
    C("pir", "traveller_name", "Elena Marchetti", "Passenger: Elena Marchetti", V),
    C(
        "pir",
        "traveller_name",
        "Nordwind Airlines Ltd.",
        "Airline: Nordwind Airlines Ltd.",
        U,
    ),
    C("pir", "airline", "Nordwind Airlines Ltd.", "Airline: Nordwind Airlines Ltd.", V),
    C(
        "pir",
        "airline",
        "Nordwind Airlines Ltd.",
        "Nordwind Airlines Ltd. - Baggage Service Zurich",
        V,
    ),
    # ---- quote
    C("quote_en", "quote_number", "HQ-55102", "Quote no.: HQ-55102", V),
    C("quote_en", "quote_number", "88-1204", "Order no.: 88-1204", U),
    C("quote_en", "quote_number", "CHE-555.666.777", "VAT no.: CHE-555.666.777", U),
    C("quote_en", "quote_date", "2027-03-03", "Quote date: 03.03.2027", V),
    C("quote_en", "quote_date", "2027-04-02", "Valid until: 02.04.2027", U),
    C("quote_en", "quote_date", "2027-04-02", "02.04.2027", U),
    C("quote_en", "total", 1221.53, "Total CHF 1'221.53", V),
    C("quote_en", "total", 91.53, "VAT 8.1%   91.53", U),
    C("quote_en", "total", 720.0, "Labour 6 h   720.00", U),
    C("quote_en", "total", 410.0, "Paint materials   410.00", U),
    C(
        "quote_en",
        "customer_name",
        "Pia Hartmann-Roth",
        "Customer: Pia Hartmann-Roth",
        V,
    ),
    C(
        "quote_en",
        "customer_name",
        "Hartmann Auto Body Ltd",
        "Hartmann Auto Body Ltd",
        U,
    ),
    # ---- shape of an identifier (the label is right in every case)
    C(
        "booking_overview",
        "booking_reference",
        "XKQZPL",
        "Booking reference: XKQZPL",
        V,
    ),
    C(
        "booking_overview",
        "booking_reference",
        "ABCDEF-GH",
        "Booking reference: ABCDEF-GH",
        V,
    ),
    C(
        "booking_overview",
        "booking_reference",
        "BK 2027 00417",
        "Booking reference: BK 2027 00417",
        V,
    ),
    C(
        "booking_overview",
        "booking_reference",
        "a1b2c3",
        "Booking reference: a1b2c3",
        V,
    ),
    C(
        "booking_overview",
        "booking_reference",
        "pending",
        "Booking reference: pending",
        U,
    ),
    C(
        "booking_overview",
        "booking_reference",
        "unavailable",
        "Booking reference: unavailable",
        U,
    ),
    C(
        "booking_overview",
        "booking_reference",
        "to be confirmed by e-mail",
        "Booking reference: to be confirmed by e-mail",
        U,
    ),
    C(
        "booking_overview",
        "booking_reference",
        "the number is on your ticket",
        "Booking reference: the number is on your ticket",
        U,
    ),
]


def stored(out: Any) -> dict[str, Any]:
    return {f.name: f for f in out.fields}


@pytest.mark.parametrize("case", [c.param() for c in CASES])
def test_reader_keeps_or_downgrades_one_field(case: Case, tmp_path: Path) -> None:
    kind, pages = LAYOUTS[case.layout]
    answer = page_answer(kind, {case.name: (case.value, case.snippet)})
    out = read(tmp_path, kind, pages, [answer])
    fields = stored(out)
    if case.expect is None:
        assert fields == {}, "a placeholder must not be stored"
        return
    assert set(fields) == {case.name}, f"the field is not stored: {case.snippet!r}"
    got = fields[case.name]
    assert got.confidence == case.expect
    assert got.value == case.value
    assert got.source.document_id == "D-hold"
    assert got.source.page == case.page
    assert got.source.snippet == case.snippet.strip()


# --------------------------------------------------------------------------------
# Several pages
# --------------------------------------------------------------------------------

INVOICE = "invoice_two_pages"
FIRST = {
    "invoice_number": ("B-27-3392", "Invoice no. B-27-3392"),
    "invoice_date": ("2027-07-30", "Date: 30.07.2027"),
    "customer_name": ("Marco Fontana", "Customer: Marco Fontana"),
}
TOTAL = {"total": (2734.93, "Total CHF 2'734.93")}


def two_pages(
    tmp_path: Path, first: dict[str, Any], second: dict[str, Any]
) -> dict[str, Any]:
    kind, pages = LAYOUTS[INVOICE]
    out = read(
        tmp_path, kind, pages, [page_answer(kind, first), page_answer(kind, second)]
    )
    return stored(out)


def test_total_on_page_two_is_verified_with_its_page(tmp_path: Path) -> None:
    fields = two_pages(tmp_path, FIRST, TOTAL)
    assert {n: f.confidence for n, f in fields.items()} == {
        "invoice_number": V,
        "invoice_date": V,
        "customer_name": V,
        "total": V,
    }
    assert fields["total"].source.page == 2
    assert fields["total"].source.snippet == "Total CHF 2'734.93"
    assert fields["invoice_number"].source.page == 1


def test_total_on_page_two_with_a_bare_snippet(tmp_path: Path) -> None:
    fields = two_pages(tmp_path, FIRST, {"total": (2734.93, "2'734.93")})
    assert fields["total"].confidence == V and fields["total"].source.page == 2


def test_subtotal_on_page_two_is_downgraded(tmp_path: Path) -> None:
    fields = two_pages(tmp_path, FIRST, {"total": (2530.0, "Subtotal   2'530.00")})
    assert fields["total"].confidence == U and fields["total"].value == 2530.0
    assert fields["total"].source.page == 2


def test_subtotal_on_page_one_and_total_on_page_two_stays_unverified(
    tmp_path: Path,
) -> None:
    first = {**FIRST, "total": (2140.0, "Subtotal   2'140.00")}
    fields = two_pages(tmp_path, first, TOTAL)
    assert fields["total"].confidence == U
    assert fields["total"].value == 2140.0 and fields["total"].source.page == 1
    assert fields["invoice_number"].confidence == V


@pytest.mark.parametrize("placeholder", ["Not answerable", "n/a", "—"])
def test_placeholder_on_page_one_does_not_hide_the_real_value(
    placeholder: str, tmp_path: Path
) -> None:
    kind, pages = LAYOUTS[INVOICE]
    pages = [[*pages[0], f"Customer: {placeholder}"], pages[1]]
    first = {"customer_name": (placeholder, f"Customer: {placeholder}")}
    second = {"customer_name": ("Marco Fontana", "Customer: Marco Fontana")}
    out = read(
        tmp_path, kind, pages, [page_answer(kind, first), page_answer(kind, second)]
    )
    got = stored(out)["customer_name"]
    assert got.confidence == V and got.source.page == 2
    assert got.value == "Marco Fontana"


@pytest.mark.parametrize("placeholder", ["n/a", "N.A.", "nil", "-"])
def test_placeholder_on_page_two_makes_no_disagreement(
    placeholder: str, tmp_path: Path
) -> None:
    kind, pages = LAYOUTS[INVOICE]
    pages = [pages[0], [*pages[1], f"Customer: {placeholder}"]]
    first = {"customer_name": ("Marco Fontana", "Customer: Marco Fontana")}
    second = {"customer_name": (placeholder, f"Customer: {placeholder}")}
    out = read(
        tmp_path, kind, pages, [page_answer(kind, first), page_answer(kind, second)]
    )
    got = stored(out)["customer_name"]
    assert got.confidence == V and got.source.page == 1


def test_two_placeholders_store_nothing(tmp_path: Path) -> None:
    kind, pages = LAYOUTS[INVOICE]
    pages = [[*pages[0], "Customer: n/a"], [*pages[1], "Customer: not available"]]
    first = {"customer_name": ("n/a", "Customer: n/a")}
    second = {"customer_name": ("not available", "Customer: not available")}
    out = read(
        tmp_path, kind, pages, [page_answer(kind, first), page_answer(kind, second)]
    )
    assert "customer_name" not in stored(out)


def test_same_invoice_number_on_both_pages_stays_verified(tmp_path: Path) -> None:
    second = {"invoice_number": FIRST["invoice_number"]}
    fields = two_pages(tmp_path, FIRST, second)
    assert fields["invoice_number"].confidence == V
    assert fields["invoice_number"].source.page == 1


def test_photo_has_no_text_layer_and_stays_unverified(tmp_path: Path) -> None:
    """No text layer: the label check is not made, the path is unchanged (0.5)."""
    from PIL import Image

    image = Image.new("RGB", (400, 300), "white")
    png = BytesIO()
    image.save(png, "PNG")
    kind = "repair_invoice"
    answer = page_answer(kind, {"invoice_number": ("4471", "Invoice no.: 4471")})
    stub = Stub({"document_type": kind}, answer)
    out = stub.reader().extract(
        Document(id="D-hold", file="x.png", pages=1), png.getvalue(), "travel"
    )
    [field] = out.fields
    assert field.confidence == U and field.value == "4471"
    assert field.source.box is not None


# --------------------------------------------------------------------------------
# labels.verified, direct
# --------------------------------------------------------------------------------


def D(y: int, m: int, d: int) -> date:
    return date(y, m, d)


@dataclass(frozen=True)
class Direct:
    kind: str
    name: str
    value: Any
    snippet: str
    expect: bool
    text: str | None = None  # the page; the snippet alone when None

    def param(self) -> Any:
        shown = (self.text or self.snippet).replace("\n", "/")
        return pytest.param(
            self, id=f"{self.kind}|{self.name}|{shown[:48]}|{self.expect}"
        )


def Y(
    kind: str, name: str, value: Any, snippet: str, text: str | None = None
) -> Direct:
    return Direct(kind, name, value, snippet, True, text)


def N(
    kind: str, name: str, value: Any, snippet: str, text: str | None = None
) -> Direct:
    return Direct(kind, name, value, snippet, False, text)


RI, RQ, MI = "repair_invoice", "repair_quote", "medical_invoice"
BC, CC, PR, LR = (
    "booking_confirmation",
    "cancellation_confirmation",
    "police_report",
    "luggage_report",
)

IDS = [
    # the label names the field, in any case
    Y(RI, "invoice_number", "4471", "Invoice no.: 4471"),
    Y(RI, "invoice_number", "4471", "INVOICE NO.: 4471"),
    Y(RI, "invoice_number", "4471", "Invoice number\n4471"),
    Y(MI, "invoice_number", "ZB-2027-1187", "Invoice no.: ZB-2027-1187"),
    # the label of the snippet occurrence: above, on the same line, after a separator
    Y(RI, "invoice_number", "4471", "4471", "Invoice no.\n4471\n"),
    Y(RI, "invoice_number", "4471", "4471", "Invoice no.\n\n4471\n"),
    # a label of another thing: a veto, even though the value is plausible
    N(RI, "invoice_number", "4471", "Customer no.: 4471"),
    N(MI, "invoice_number", "4471", "Patient no. 4471"),
    N(RI, "invoice_number", "4471", "Order number: 4471"),
    N(RI, "invoice_number", "4471", "Job no.: 4471"),
    N(RI, "invoice_number", "4471", "Tax number: 4471"),
    N(RI, "invoice_number", "4471", "VAT no. 4471"),
    N(RI, "invoice_number", "4471", "Insured no.: 4471"),
    N(RI, "invoice_number", "CHE-123.456.789", "UID: CHE-123.456.789"),
    N(
        RI,
        "invoice_number",
        "CH93 0076 2011 6238 5295 7",
        "IBAN: CH93 0076 2011 6238 5295 7",
    ),
    N(RI, "invoice_number", "0791234567", "Phone number: 0791234567"),
    N(RI, "invoice_number", "Q-1", "Quote no.: Q-1"),
    # the value repeated, or a label elsewhere on the page
    N(RI, "invoice_number", "4471", "4471", "Invoice no.: 5512\nOrder no.: 4471"),
    Y(RI, "invoice_number", "5512", "5512", "Invoice no.: 5512\nOrder no.: 4471"),
    N(RI, "invoice_number", "4471", "4471", "Invoice no.: 4471\nCustomer no.: 4471"),
    N(RI, "invoice_number", "4471", "4471", "Customer no.: 4471\nInvoice no.: 4471"),
    Y(RI, "invoice_number", "4471", "4471", "Invoice no.: 4471\nPage 1 of 1"),
    # two labels in the snippet: the last one counts
    Y(
        BC,
        "booking_reference",
        "XJ-7790",
        "Customer no.: K-5521\nBooking reference: XJ-7790",
    ),
    N(
        BC,
        "booking_reference",
        "K-5521",
        "Customer no.: K-5521",
        "Customer no.: K-5521\nBooking reference: XJ-7790",
    ),
    # other id fields
    Y(BC, "booking_reference", "AB-12", "Booking reference: AB-12"),
    Y(BC, "booking_reference", "AB-12", "Booking no.: AB-12"),
    N(BC, "booking_reference", "AB-12", "Customer no.: AB-12"),
    N(BC, "booking_reference", "AB-12", "Phone: AB-12"),
    Y(RQ, "quote_number", "Q-1", "Quote no.: Q-1"),
    N(RQ, "quote_number", "Q-1", "Order no.: Q-1"),
    N(RQ, "quote_number", "Q-1", "Customer no.: Q-1"),
    Y(PR, "report_number", "27/881", "Report no.: 27/881"),
    N(PR, "report_number", "27/881", "Customer no.: 27/881"),
    Y(LR, "report_reference", "ZRH-1", "File reference: ZRH-1"),
    Y(LR, "report_reference", "ZRH-1", "Report reference: ZRH-1"),
    N(LR, "report_reference", "ZRH-1", "Ticket number: ZRH-1"),
    N(LR, "report_reference", "ZRH-1", "Phone: ZRH-1"),
    # shape: the label is right, the value is prose or a word of the dictionary
    Y(RI, "invoice_number", "ABCDEF-GH", "Invoice no.: ABCDEF-GH"),
    Y(RI, "invoice_number", "XKQZPL", "Invoice no.: XKQZPL"),
    Y(RI, "invoice_number", "2027/0318", "Invoice no.: 2027/0318"),
    Y(RI, "invoice_number", "K-30418", "Invoice no.: K-30418"),
    N(RI, "invoice_number", "pending", "Invoice no.: pending"),
    N(RI, "invoice_number", "unavailable", "Invoice no.: unavailable"),
    N(
        RI,
        "invoice_number",
        "see attached document",
        "Invoice no.: see attached document",
    ),
    N(RI, "invoice_number", "to be confirmed", "Invoice no.: to be confirmed"),
    N(
        RI,
        "invoice_number",
        "will be sent by post",
        "Invoice no.: will be sent by post",
    ),
]

DATES = [
    # the own date of the document: the generic label is enough
    Y(RI, "invoice_date", D(2027, 1, 5), "Date: 05.01.2027"),
    Y(RI, "invoice_date", D(2027, 1, 5), "Invoice date: 05.01.2027"),
    Y(RI, "invoice_date", D(2027, 1, 5), "Date: 2027-01-05"),
    Y(RQ, "quote_date", D(2027, 1, 5), "Date: 05.01.2027"),
    Y(RQ, "quote_date", D(2027, 1, 5), "Quote date: 05.01.2027"),
    Y("receipt", "purchase_date", D(2027, 1, 5), "Date: 05.01.2027"),
    Y(CC, "cancellation_date", D(2027, 1, 5), "Cancellation date: 05.01.2027"),
    # a date of another meaning
    N(RI, "invoice_date", D(2027, 2, 5), "Due date: 05.02.2027"),
    N(RI, "invoice_date", D(2027, 2, 5), "Valid until 05.02.2027"),
    N(RI, "invoice_date", D(2027, 2, 5), "Expiry date: 05.02.2027"),
    N(RI, "invoice_date", D(1980, 2, 5), "Date of birth: 05.02.1980"),
    N(RI, "invoice_date", D(2027, 2, 5), "Printed: 05.02.2027"),
    N(RQ, "quote_date", D(2027, 2, 5), "Valid until: 05.02.2027"),
    N("receipt", "purchase_date", D(2029, 2, 5), "Warranty until 05.02.2029"),
    N(CC, "cancellation_date", D(2027, 2, 5), "Valid until: 05.02.2027"),
    N(CC, "cancellation_date", D(1980, 2, 5), "Date of birth: 05.02.1980"),
    # no label at all
    N(RI, "invoice_date", D(2027, 1, 5), "05.01.2027"),
    N("receipt", "purchase_date", D(2027, 1, 5), "05.01.2027 14:32"),
    N(RQ, "quote_date", D(2027, 1, 5), "Zürich, 05.01.2027"),
    # event dates need an event word
    Y(BC, "departure_date", D(2027, 7, 5), "Departure date: 05.07.2027"),
    N(BC, "departure_date", D(2027, 7, 5), "Date: 05.07.2027"),
    N(BC, "departure_date", D(2027, 7, 12), "Return: 12.07.2027"),
    N(BC, "departure_date", D(2027, 7, 5), "05.07.2027"),
    Y(PR, "incident_date", D(2027, 6, 4), "Incident date: 04.06.2027"),
    N(PR, "incident_date", D(2027, 6, 4), "Date: 04.06.2027"),
    N(PR, "incident_date", D(1990, 6, 5), "Date of birth: 05.06.1990"),
    Y(LR, "incident_date", D(2027, 6, 4), "Date of incident: 04.06.2027"),
    Y(LR, "incident_date", D(2027, 6, 4), "Date of event: 04.06.2027"),
    N(LR, "incident_date", D(2027, 6, 5), "Date reported: 05.06.2027"),
    N(LR, "incident_date", D(2027, 6, 4), "Flight date: 04.06.2027"),
    Y(
        "medical_report",
        "treatment_date",
        D(2027, 3, 1),
        "Date of treatment: 01.03.2027",
    ),
    N("medical_report", "treatment_date", D(2027, 3, 1), "Date : 01.03.2027"),
    N("medical_report", "treatment_date", D(2027, 3, 4), "Report date: 04.03.2027"),
    Y(
        "medical_certificate",
        "unfit_from",
        D(2027, 3, 1),
        "Unfit for work from: 01.03.2027",
    ),
    N("medical_certificate", "unfit_from", D(2027, 3, 1), "Date: 01.03.2027"),
    N("medical_certificate", "unfit_from", D(2027, 3, 15), "Valid until: 15.03.2027"),
    N("medical_certificate", "unfit_from", D(1990, 3, 15), "Date of birth: 15.03.1990"),
]

AMOUNTS = [
    Y(RI, "total", 99.0, "Total CHF 99.00"),
    Y(RI, "total", 99.0, "Total EUR 99.00"),
    Y(RI, "total", 99.0, "Total: 99.00 CHF"),
    Y(RI, "total", 99.0, "TOTAL 99.00"),
    Y(RI, "total", 1099.0, "TOTAL CHF 1'099.00"),
    Y(RI, "total", 99.0, "Total amount: 99.00"),
    Y(MI, "total", 99.0, "Total CHF 99.00"),
    Y(RQ, "total", 99.0, "Total CHF 99.00"),
    N(RI, "total", 99.0, "Subtotal: CHF 99.00"),
    N(RI, "total", 99.0, "VAT 8.1%: 99.00"),
    N(RI, "total", 99.0, "Deposit CHF 99.00"),
    N(RI, "total", -99.0, "Discount -99.00"),
    N(RI, "total", 99.0, "Service fee 99.00"),
    N(RI, "total", 99.0, "Handling charge CHF 99.00"),
    N(RI, "total", 99.0, "99.00"),
    N(RI, "total", 99.0, "Paint 99.00"),
    N(RI, "total", 99.0, "99.00", "Subtotal 99.00\nTotal 99.00"),
    Y(RI, "total", 99.0, "99.00", "Total CHF 99.00\nCard CHF 99.00"),
    N(RI, "total", 99.0, "99.00", "Total CHF 99.00\nDeposit CHF 99.00"),
    Y(RI, "total", 99.0, "99.00", "Total\n99.00"),
    N(RI, "total", 99.0, "99.00", "Position 1\n99.00"),
    Y(BC, "trip_price", 99.0, "Trip price: CHF 99.00"),
    Y(BC, "trip_price", 99.0, "Total price CHF 99.00"),
    N(BC, "trip_price", 99.0, "Deposit CHF 99.00"),
    N(BC, "trip_price", 99.0, "Insurance premium CHF 99.00"),
    Y("receipt", "amount_paid", 20.0, "Amount paid CHF 20.00"),
    Y("receipt", "amount_paid", 20.0, "Total CHF 20.00"),
    N("receipt", "amount_paid", 5.0, "Change CHF 5.00"),
    N("receipt", "amount_paid", 20.0, "Subtotal CHF 20.00"),
    N("receipt", "amount_paid", 4.5, "Coffee   4.50"),
    Y(CC, "refund_received", 400.0, "Refund: CHF 400.00"),
    N(CC, "refund_received", 100.0, "Cancellation fee CHF 100.00"),
    N(CC, "refund_received", 100.0, "Fee CHF 100.00"),
    N(CC, "refund_received", 400.0, "400.00"),
]

PERSON_LABELS = "Doctor: ", "Signed: "

PROVIDERS = [
    # an organisation
    Y(MI, "provider", "Praxis Dr. med. A. Muster", "Praxis Dr. med. A. Muster"),
    Y(MI, "provider", "Dr. Muster AG", "Dr. Muster AG"),
    Y(MI, "provider", "Gruppenpraxis Wil", "Gruppenpraxis Wil"),
    Y(
        MI,
        "provider",
        "Zentrum für Zahnmedizin Dr. Weber",
        "Zentrum für Zahnmedizin Dr. Weber",
    ),
    Y(MI, "provider", "Medical Center Bern", "Medical Center Bern"),
    Y(MI, "provider", "Dental Care Ltd", "Dental Care Ltd"),
    Y(MI, "provider", "Seeblick Physio", "Seeblick Physio"),
    Y("medical_report", "provider", "Praxis am Bahnhof", "Praxis am Bahnhof"),
    # one person with a title
    N(MI, "provider", "Dr. med. Hanspeter Gerber", "Dr. med. Hanspeter Gerber"),
    N(MI, "provider", "Dr. Muster", "Dr. Muster"),
    N(
        MI,
        "provider",
        "Prof. Dr. med. Beat Rothenfluh",
        "Prof. Dr. med. Beat Rothenfluh",
    ),
    N(MI, "provider", "PD Dr. med. Lukas Imhof", "PD Dr. med. Lukas Imhof"),
    N(MI, "provider", "med. pract. Sabine Roth", "med. pract. Sabine Roth"),
    N(MI, "provider", "Dr. med. J. P. Hess", "Dr. med. J. P. Hess"),
    N(
        "medical_report",
        "provider",
        "Dr. med. Hanspeter Gerber",
        "Dr. med. Hanspeter Gerber",
    ),
    # a name with no title: only a person label makes it a person
    Y(MI, "provider", "Sabine Roth", "Sabine Roth"),
    Y(MI, "provider", "Sabine Roth", "Sabine Roth", "Invoice\nSabine Roth\nZürich"),
    *[
        N(MI, "provider", "Sabine Roth", "Sabine Roth", f"Invoice\n{label}Sabine Roth")
        for label in PERSON_LABELS
    ],
    N(
        MI,
        "provider",
        "Mary Ann Keller",
        "Mary Ann Keller",
        "Physician: Mary Ann Keller",
    ),
    N(MI, "provider", "Tobias Frei", "Tobias Frei", "Dentist: Tobias Frei"),
]

PERSONS = [
    Y(MI, "patient_name", "Lukas Meierhans", "Lukas Meierhans"),
    Y(MI, "patient_name", "Anna-Maria von Arx", "Patient: Anna-Maria von Arx"),
    Y(MI, "patient_name", "Hans Müller-Frey", "Hans Müller-Frey"),
    Y(RI, "customer_name", "Seamus O'Brien", "Customer: Seamus O'Brien"),
    Y(BC, "traveller_name", "Mr. Ruedi Haldimann", "Guest: Mr. Ruedi Haldimann"),
    N(MI, "patient_name", "Praxis am Rebberg", "Praxis am Rebberg"),
    N(RI, "customer_name", "Muster Treuhand AG", "Customer: Muster Treuhand AG"),
    N(RI, "customer_name", "Dr. Muster GmbH", "Dr. Muster GmbH"),
    N(RI, "customer_name", "Garage Rossi Sagl", "Garage Rossi Sagl"),
    N(RI, "customer_name", "Hotel Bellevue SA", "Hotel Bellevue SA"),
    N(BC, "traveller_name", "Alpenrose Hotel & Spa AG", "Alpenrose Hotel & Spa AG"),
    N(BC, "traveller_name", "Reisebüro Nova GmbH", "Reisebüro Nova GmbH"),
    N(LR, "traveller_name", "Nordwind Airlines Ltd.", "Nordwind Airlines Ltd."),
    N(PR, "customer_name", "Bernasconi Sàrl", "Bernasconi Sàrl"),
    N(PR, "customer_name", "Medizinisches Zentrum Wil", "Medizinisches Zentrum Wil"),
    N(MI, "patient_name", "Studio Legale Rossi", "Studio Legale Rossi"),
]

OUT_OF_SCOPE = [
    Y("receipt", "merchant", "Hans Muster", "Hans Muster"),
    Y("receipt", "merchant", "Dorfladen AG", "Total CHF 99.00"),
    Y(RI, "repair_company", "Dr. med. Hans Muster", "Dr. med. Hans Muster"),
    Y(RQ, "repair_company", "Autorimessa Ceresio SA", "Autorimessa Ceresio SA"),
    Y(
        LR,
        "airline",
        "Nordwind Airlines Ltd.",
        "Date: 05.01.2027 Nordwind Airlines Ltd.",
    ),
    Y("receipt", "item_description", "Ruchbrot", "Ruchbrot"),
]

DIRECT = [*IDS, *DATES, *AMOUNTS, *PROVIDERS, *PERSONS, *OUT_OF_SCOPE]


@pytest.mark.parametrize("case", [c.param() for c in DIRECT])
def test_verified_direct(case: Direct) -> None:
    text = case.text if case.text is not None else case.snippet
    assert (
        labels.verified(case.kind, case.name, case.value, case.snippet, text)
        is case.expect
    )


def test_the_safeguarded_fields_are_the_ones_of_the_specification() -> None:
    expected = {
        **dict.fromkeys(
            [
                "invoice_number",
                "quote_number",
                "booking_reference",
                "report_number",
                "report_reference",
            ],
            "id",
        ),
        **dict.fromkeys(
            [
                "invoice_date",
                "quote_date",
                "purchase_date",
                "cancellation_date",
                "departure_date",
                "incident_date",
                "treatment_date",
                "unfit_from",
            ],
            "date",
        ),
        **dict.fromkeys(
            ["total", "trip_price", "amount_paid", "refund_received"], "amount"
        ),
        "provider": "organisation",
        **dict.fromkeys(["patient_name", "customer_name", "traveller_name"], "person"),
    }
    assert dict(labels.SAFEGUARDED) == expected


@pytest.mark.parametrize(
    "name",
    [
        "cause",
        "diagnosis",
        "offence",
        "item_description",
        "damage_description",
        "line_items",
        "repair_company",
        "merchant",
        "airline",
    ],
)
def test_out_of_scope_fields_are_not_safeguarded(name: str) -> None:
    assert name not in labels.SAFEGUARDED


# --------------------------------------------------------------------------------
# labels.is_placeholder, direct
# --------------------------------------------------------------------------------

PLACEHOLDER_TRUE = [
    "Not answerable",
    "not answerable",
    "NOT ANSWERABLE",
    "Unanswerable",
    "null",
    "NULL",
    "None",
    "N/A",
    "n/a",
    "N.A.",
    "n.a.",
    "-",
    "--",
    "—",
    "Unknown",
    "UNKNOWN",
    "Not found",
    "Not available",
    "Not applicable",
    "Not specified",
    "Not provided",
    "Not given",
    "Not stated",
    "Nil",
    "No value",
    "Empty",
    "  Not   Applicable  ",
    "\tUnknown\n",
    '"n/a"',
    "'Unknown'",
    "[n/a]",
    "(unknown)",
    "(n/a)",
    "N/A.",
    "Unknown.",
    "(not available)",
]

PLACEHOLDER_FALSE: list[object] = [
    "N/A 4471",
    "ID: n/a",
    "Unknown Pass AG",
    "none of the above",
    "Not available in stock",
    "Unknown, Peter",
    "Nilufar Sharipova",
    "Nullmatt AG",
    "NIL-27/4471",
    "n.a.-77",
    "A",
    "X",
    "K",
    "-4471",
    "4471",
    "XKQZPL",
    "Nicolas Null",
    "Anna Keller",
    None,
    0,
    0.0,
    1,
    12.5,
    -4.0,
    False,
    True,
    date(2027, 1, 5),
    [],
    ["n/a"],
    {"value": "n/a"},
]


@pytest.mark.parametrize(
    "value", [pytest.param(v, id=repr(v)) for v in PLACEHOLDER_TRUE]
)
def test_is_placeholder_true(value: str) -> None:
    assert labels.is_placeholder(value) is True


@pytest.mark.parametrize(
    "value", [pytest.param(v, id=repr(v)) for v in PLACEHOLDER_FALSE]
)
def test_is_placeholder_false(value: object) -> None:
    assert labels.is_placeholder(value) is False


def test_placeholders_are_normalised_and_have_no_single_letter() -> None:
    assert labels.PLACEHOLDERS
    for text in labels.PLACEHOLDERS:
        assert text == text.casefold() and text == " ".join(text.split())
        assert len(text) > 1 or not text.isalnum()
