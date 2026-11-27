"""Adversarial provenance and safety tests of the document scope (issue #126).

Specification: `backend/.dev-data/issue-126/READER-INTERFACE.md`. Every test is
offline: an HTTP stub, no key, no network. The documents are built here (reportlab
for text pages, Pillow for image-only pages) with invented content; nothing is read
from `data/`.

In the document scope the index of a block is a claim of the MODEL. The tests look
for a way to get a wrong value, a wrong page or a value without evidence stored at
0.8 or more, or an incomplete result that looks complete.

Naming. A test that ends in `_known_limit` pins a weakness that exists today in
the page scope too (the same answers give the same `Extraction` in both scopes: the
tests run both and compare). A test that ends in `_undocumented` pins what the code
does where the note is silent. No test here found a defect of the code.

The contract limit of the split line item (cases 17 and 20). `Extraction` is a flat
list of dicts: a line has no page, no confidence and no "incomplete" flag, and a
failed call is a document with no field. So the contract cannot represent a line
that starts on page 1 and ends on page 2, it cannot verify one, and it cannot say
"this document was not read" or "this list of lines is incomplete" when the document
has no `total` to carry the mark.
"""

import json
import random
import re
from functools import cache
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader
from reportlab.lib.utils import ImageReader  # type: ignore[import-untyped]
from reportlab.pdfgen import canvas  # type: ignore[import-untyped]

from app.checks.tampering import check_tampering
from app.docintel import labels, omni
from app.docintel.omni import UNVERIFIED, VERIFIED
from app.fraud.routing import LOW_CONFIDENCE, route
from app.models import Document, DocumentType, Extraction, Outcome
from app.workflows import WORKFLOWS
from tests.builders import travel_claim, travel_documents
from tests.test_omni_document import Model, blocks, by_document, by_page, finished
from tests.test_omni_reader import URL, doc, nothing_on

KIND = "repair_invoice"
TYPE = DocumentType.repair_invoice
DOCUMENT_ID = "D-omni"
A4_POINTS = (595.27, 841.89)
Given = tuple[Any, str]


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVIDIA_KEY", "test-key-value")
    monkeypatch.setenv("VLM_BASE_URL", URL)
    monkeypatch.setenv("VLM_MODEL", "omni-test")


@pytest.fixture(autouse=True)
def rendered_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Render each document once for the whole run: the pages are immutable tuples,
    and the tests change nothing in them. Only a speed-up."""
    original = omni.pages
    seen: dict[tuple[bytes, int | None, int], list[omni.Page]] = {}

    def pages(data: bytes, first: int | None = None, dpi: int = omni.DPI) -> Any:
        key = (data, first, dpi)
        if key not in seen:
            seen[key] = original(data, first, dpi)
        return list(seen[key])

    monkeypatch.setattr(omni, "pages", pages)


# --------------------------------------------------------------------- the documents
# A 3-page repair invoice. The rows are 1 to 3 (page 1), 4 to 6 (page 2); the total
# is on page 3. Page 1 and page 3 print amounts and dates that look like the total
# and the invoice date but are not (a subtotal, a deposit, a due date, a cost
# estimate), and numbers that look like the invoice number (customer, order).

PAGE_1 = [
    "Brennholt Sanitaer AG",
    "Seestrasse 12, 8700 Kusnacht",
    "Invoice no.: BS-26-0417",
    "Invoice date: 2026-10-09",
    "Customer: Ortrud Vanselow",
    "Site contact: Gregor Imhof",
    "Customer no.: K-77310",
    "Cost estimate of 2026-09-20: CHF 1,284.50",
    "Pipe replacement  2  45.00  90.00",
    "Boiler flush  1  120.00  120.00",
    "Radiator valves  3  110.00  330.00",
    "Subtotal page 1, carried forward 540.00",
    "Page 1 of 3",
]
PAGE_2 = [
    "Invoice BS-26-0417",
    "Work completed on 2026-10-02",
    "Brought forward from page 1 540.00",
    "Wall tiles  10  18.50  185.00",
    "Tiler labour  8  64.00  512.00",
    "Sealing and small material  1  47.50  47.50",
    "Carried forward to page 3 1,284.50",
    "Page 2 of 3",
]
PAGE_3 = [
    "Invoice BS-26-0417",
    "Brought forward from page 2 1,284.50",
    "Subtotal before VAT 1,284.50",
    "Total CHF 1,284.50",
    "of which VAT 8.1% 96.20",
    "Deposit received 2026-09-25: CHF 300.00",
    "Invoice date: 2026-10-09",
    "Due date: 2026-11-08",
    "Order no.: BS-26-0471",
    "Customer: Ortrud Vanselow",
    "Signed: Gregor Imhof",
    "Subcontractor: Rohrmeister Frei AG",
    "Payable within 30 days",
    "Page 3 of 3",
]
INVOICE_PAGES = [PAGE_1, PAGE_2, PAGE_3]


def build_pdf(
    pages: list[list[str]],
    scans: frozenset[int] = frozenset(),
    stamped: frozenset[int] = frozenset(),
) -> bytes:
    """A PDF with these lines on each page. A page number in `scans` (from 1) is an
    image: the page has no text layer. A page in `stamped` is an image with a thin
    text layer: only its last line (the page footer) is text."""
    out = BytesIO()
    pdf = canvas.Canvas(out, pagesize=A4_POINTS, invariant=1)
    for number, lines in enumerate(pages, 1):
        if number in scans or number in stamped:
            image = Image.new("L", (1240, 1754), 255)
            draw = ImageDraw.Draw(image)
            font = ImageFont.load_default(size=26)
            for i, line in enumerate(lines):
                draw.text((70, 70 + 40 * i), line, fill=0, font=font)
            pdf.drawImage(ImageReader(image), 0, 0, *A4_POINTS)
            if number in stamped:
                pdf.setFont("Helvetica", 10)
                pdf.drawString(50, 30, lines[-1])
        else:
            pdf.setFont("Helvetica", 10)
            for i, line in enumerate(lines):
                pdf.drawString(50, 800 - 16 * i, line)
        pdf.showPage()
    pdf.save()
    return out.getvalue()


@cache
def invoice(scans: frozenset[int] = frozenset()) -> bytes:
    return build_pdf(INVOICE_PAGES, scans)


def texts(data: bytes) -> list[str]:
    return [p.text for p in omni.pages(data)]


# The values and the text that a model would copy, from the pages above.
INV: Given = ("BS-26-0417", "Invoice no.: BS-26-0417")
HEAD: Given = ("BS-26-0417", "Invoice BS-26-0417")
DATE: Given = ("2026-10-09", "Invoice date: 2026-10-09")
COMPANY: Given = ("Brennholt Sanitaer AG", "Brennholt Sanitaer AG")
CUSTOMER: Given = ("Ortrud Vanselow", "Customer: Ortrud Vanselow")
TOTAL: Given = (1284.5, "Total CHF 1,284.50")
# right value or wrong value, true snippet of its page
CUSTOMER_NO: Given = ("K-77310", "Customer no.: K-77310")
ORDER_NO: Given = ("BS-26-0471", "Order no.: BS-26-0471")
ESTIMATE_DATE: Given = ("2026-09-20", "Cost estimate of 2026-09-20: CHF 1,284.50")
DUE_DATE: Given = ("2026-11-08", "Due date: 2026-11-08")
DEPOSIT_DATE: Given = ("2026-09-25", "Deposit received 2026-09-25: CHF 300.00")
DONE_DATE: Given = ("2026-10-02", "Work completed on 2026-10-02")
SUBTOTAL: Given = (540.0, "Subtotal page 1, carried forward 540.00")
BROUGHT_1: Given = (540.0, "Brought forward from page 1 540.00")
BROUGHT_2: Given = (1284.5, "Brought forward from page 2 1,284.50")
CARRIED: Given = (1284.5, "Carried forward to page 3 1,284.50")
SUBTOTAL_3: Given = (1284.5, "Subtotal before VAT 1,284.50")
VAT: Given = (96.2, "of which VAT 8.1% 96.20")
DEPOSIT: Given = (300.0, "Deposit received 2026-09-25: CHF 300.00")
SIGNER: Given = ("Gregor Imhof", "Signed: Gregor Imhof")
SITE_CONTACT: Given = ("Gregor Imhof", "Site contact: Gregor Imhof")
SUBCONTRACTOR: Given = ("Rohrmeister Frei AG", "Subcontractor: Rohrmeister Frei AG")


def row(
    description: str | None, quantity: Any, unit_price: Any, amount: Any
) -> dict[str, Any]:
    return {
        "description": description,
        "quantity": quantity,
        "unit_price": unit_price,
        "amount": amount,
    }


L1 = row("Pipe replacement", 2.0, 45.0, 90.0)
L2 = row("Boiler flush", 1.0, 120.0, 120.0)
L3 = row("Radiator valves", 3.0, 110.0, 330.0)
L4 = row("Wall tiles", 10.0, 18.5, 185.0)
L5 = row("Tiler labour", 8.0, 64.0, 512.0)
L6 = row("Sealing and small material", 1.0, 47.5, 47.5)
SIX = [L1, L2, L3, L4, L5, L6]  # their amounts add up to the total, 1,284.50


def block_of(
    kind: DocumentType, lines: list[dict[str, Any]] | None, given: dict[str, Given]
) -> dict[str, Any]:
    answer = nothing_on(kind)
    for name, (value, snippet) in given.items():
        answer["values"][name] = value
        answer["snippets"][name] = snippet
    answer["values"]["line_items"] = list(lines or [])
    return answer


def block(lines: list[dict[str, Any]] | None = None, **given: Given) -> dict[str, Any]:
    """The answer for one page of the repair invoice."""
    return block_of(TYPE, lines, given)


def only(name: str, given: Given) -> dict[str, Any]:
    """The answer for a page where the model gives one field (and no line)."""
    return block_of(TYPE, None, {name: given})


EMPTY = block()
PERFECT = [
    block(
        [L1, L2, L3],
        invoice_number=INV,
        invoice_date=DATE,
        repair_company=COMPANY,
        customer_name=CUSTOMER,
    ),  # fmt: skip
    block([L4, L5, L6], invoice_number=HEAD),
    block(total=TOTAL),
]


# ------------------------------------------------------------------------ the helpers


def assert_provenance(out: Extraction, data: bytes) -> None:
    """Rule 3 and the note (section 4), for every field of an `Extraction`: the
    document, a page of the document, a snippet printed on that page or a box of a
    page with no text layer; nothing at 0.8 or more without a snippet on a text
    layer; one field per name."""
    found = omni.pages(data)
    assert len({f.name for f in out.fields}) == len(out.fields)
    for f in out.fields:
        s = f.source
        assert s.document_id == DOCUMENT_ID
        assert 1 <= s.page <= len(found)
        assert s.snippet or s.box is not None
        page = found[s.page - 1]
        if page.text.strip():
            assert s.snippet and s.box is None
            assert 0 < len(s.snippet) <= omni.MAX_SNIPPET
            assert omni._shown(s.snippet, page.text), (f.name, s.page)
        else:
            width, height = page.size
            assert s.snippet is None and s.box == (0.0, 0.0, width, height)
            assert f.confidence < LOW_CONFIDENCE, "a scan field needs a person"
        if f.confidence >= LOW_CONFIDENCE:
            assert s.snippet and page.text.strip()


def read_document(
    data: bytes, *answers: Any, kind: str = KIND
) -> tuple[Extraction, Model]:
    out, stub = by_document(data, kind, blocks(*answers))
    assert_provenance(out, data)
    return out, stub


def read_pages(data: bytes, *answers: Any, kind: str = KIND) -> Extraction:
    out, _ = by_page(data, kind, *answers)
    assert_provenance(out, data)
    return out


def both(data: bytes, *answers: Any, kind: str = KIND) -> Extraction:
    """The same page answers through the document scope (one answer with a block
    per page) and the page scope (one answer per page): the same `Extraction`. Gives
    that `Extraction`."""
    document, stub = read_document(data, *answers, kind=kind)
    page = read_pages(data, *answers, kind=kind)
    assert document.model_dump() == page.model_dump()
    assert len(stub.requests) == 2  # classify, then one call for the document
    return document


def stored(out: Extraction) -> dict[str, tuple[Any, int, float]]:
    """name -> (value, page, confidence)."""
    return {f.name: (f.value, f.source.page, f.confidence) for f in out.fields}


def field(out: Extraction, name: str) -> Any:
    return next(f for f in out.fields if f.name == name)


def assert_a_person_checks(out: Extraction) -> None:
    assert all(f.confidence < LOW_CONFIDENCE for f in out.fields)
    assert out.line_items == []


def with_total_marked(out: Extraction) -> bool:
    return any(f.name == "total" and f.confidence < LOW_CONFIDENCE for f in out.fields)


def t003(out: Extraction, tmp_path: Path) -> list[str]:
    """The codes of the tampering check for this document (no stored file, so only
    T003 can fire)."""
    document = Document(
        id=DOCUMENT_ID,
        file="absent.pdf",
        document_type=out.document_type.value,
        fields=out.fields,
        line_items=out.line_items,
    )
    claim = travel_claim(documents=[document])
    return [s.code for s in check_tampering(claim, tmp_path)]


def route_with(out: Extraction) -> tuple[Outcome, list[str]]:
    document = Document(
        id=DOCUMENT_ID,
        file="absent.pdf",
        document_type=out.document_type.value,
        fields=out.fields,
        line_items=out.line_items,
    )
    claim = travel_claim(documents=[*travel_documents(), document])
    return route(claim, [], WORKFLOWS["travel"])


# -------------------------------------------------------------------- the fixtures


def test_the_fixture_documents_hold_what_the_attacks_rely_on() -> None:
    """Checked once: the text layer of each page, the image-only pages, and where
    each snippet used below is printed."""
    text = texts(invoice())
    assert len(text) == 3
    for page, lines in zip(text, INVOICE_PAGES, strict=True):
        for line in lines:
            assert line in page, line
    snippets = {
        INV: {1},
        HEAD: {2, 3},
        DATE: {1, 3},
        COMPANY: {1},
        CUSTOMER: {1, 3},
        TOTAL: {3},
        CUSTOMER_NO: {1},
        ORDER_NO: {3},
        ESTIMATE_DATE: {1},
        DUE_DATE: {3},
        DEPOSIT_DATE: {3},
        DONE_DATE: {2},
        SUBTOTAL: {1},
        BROUGHT_1: {2},
        BROUGHT_2: {3},
        CARRIED: {2},
        SUBTOTAL_3: {3},
        VAT: {3},
        SIGNER: {3},
        SITE_CONTACT: {1},
        SUBCONTRACTOR: {3},
    }
    for (_, snippet), where in snippets.items():
        shown = {n for n, page in enumerate(text, 1) if omni._shown(snippet, page)}
        assert shown == where, snippet
    for number, rows in enumerate([[L1, L2, L3], [L4, L5, L6], []], 1):
        assert all(omni._on_page(line, text[number - 1]) for line in rows)
    assert round(sum(line["amount"] for line in SIX), 2) == TOTAL[0]
    assert round(sum(line["amount"] for line in SIX[:3]), 2) == SUBTOTAL[0]


def test_the_scan_and_the_mixed_fixtures_have_the_right_text_layers() -> None:
    assert [bool(t.strip()) for t in texts(invoice(frozenset({1, 2, 3})))] == [
        False
    ] * 3
    assert [bool(t.strip()) for t in texts(invoice(frozenset({2})))] == [
        True,
        False,
        True,
    ]
    assert len(PdfReader(BytesIO(invoice(frozenset({2})))).pages) == 3


def test_a_perfect_answer_is_stored_whole_in_both_scopes() -> None:
    """The control of every test below: what a faithful model gives."""
    out = both(invoice(), *PERFECT)
    assert stored(out) == {
        "invoice_number": ("BS-26-0417", 1, VERIFIED),
        "invoice_date": ("2026-10-09", 1, VERIFIED),
        "repair_company": ("Brennholt Sanitaer AG", 1, VERIFIED),
        "customer_name": ("Ortrud Vanselow", 1, VERIFIED),
        "total": (1284.5, 3, VERIFIED),
    }
    assert out.line_items == SIX


# -------------------------------------------------- 1. a value in the wrong block


@pytest.mark.parametrize(
    ("name", "given", "wrong"),
    [
        ("repair_company", COMPANY, 1),
        ("repair_company", COMPANY, 2),
        ("total", TOTAL, 0),
        ("total", TOTAL, 1),
        ("invoice_number", ORDER_NO, 0),
        ("invoice_number", ORDER_NO, 1),
        ("invoice_number", INV, 1),
        ("invoice_number", INV, 2),
        ("invoice_date", DEPOSIT_DATE, 0),
        ("invoice_date", DUE_DATE, 1),
    ],
)
def test_a_correct_value_in_the_block_of_the_wrong_page_is_not_stored(
    name: str, given: Given, wrong: int
) -> None:
    """The snippet is printed on one page only. In the block of another page it is
    not shown there: the field is not stored, and it is never moved to the page that
    prints it."""
    answers = [EMPTY, EMPTY, EMPTY]
    answers[wrong] = only(name, given)
    out = both(invoice(), *answers)
    assert out.fields == [] and out.line_items == []


def test_the_right_block_gives_the_page_even_when_a_wrong_block_comes_first() -> None:
    """The misplaced copy is refused; the same value in its own block is stored with
    its own page, at 0.9, and the refused copy makes no conflict (same value)."""
    answers = [block(total=TOTAL), EMPTY, block(total=TOTAL)]
    out = both(invoice(), *answers)
    assert stored(out) == {"total": (1284.5, 3, VERIFIED)}


def test_a_whole_answer_shifted_by_one_page_stores_no_wrong_page() -> None:
    """The model puts the content of page k in the block of page k+1 (and the last
    block is empty), or one block early."""
    later = both(invoice(), EMPTY, PERFECT[0], PERFECT[1])
    assert {f.name for f in later.fields} == {"invoice_number"}
    assert stored(later)["invoice_number"][1:] == (3, VERIFIED)  # the header, page 3
    assert later.line_items == []  # rows 1 to 3 are not on page 2
    earlier = both(invoice(), PERFECT[1], PERFECT[2], EMPTY)
    assert earlier.fields == []  # no snippet is printed on the page of its block
    assert earlier.line_items == []


def test_everything_in_the_first_block_stores_only_what_page_1_prints() -> None:
    """A model that writes the whole document into block 1 and leaves the other
    blocks empty: the values of page 3 and the rows of page 2 are not on page 1. No
    wrong page; the total is simply missing (like a total that is not read)."""
    everything = block(
        SIX,
        invoice_number=INV,
        invoice_date=DATE,
        repair_company=COMPANY,
        customer_name=CUSTOMER,
        total=TOTAL,
    )
    out = both(invoice(), everything, EMPTY, EMPTY)
    assert set(stored(out)) == {
        "invoice_number",
        "invoice_date",
        "repair_company",
        "customer_name",
    }
    assert {page for _, page, _ in stored(out).values()} == {1}
    assert out.line_items == []  # rows 4 to 6 are not on page 1


# --------------------------------------------- 2. a snippet copied from another page


@pytest.mark.parametrize(
    ("name", "given", "block_number", "page"),
    [
        ("total", TOTAL, 0, None),  # printed on page 3, given in the block of page 1
        ("total", TOTAL, 1, None),
        ("invoice_number", HEAD, 0, None),  # printed on pages 2 and 3, not on page 1
        ("invoice_number", INV, 1, None),  # printed on page 1 only
        ("invoice_number", INV, 2, None),
        ("customer_name", CUSTOMER, 1, None),  # printed on pages 1 and 3, not on 2
        ("customer_name", CUSTOMER, 2, 3),  # page 3 prints it too: stored there
        ("repair_company", SUBCONTRACTOR, 0, None),
    ],
)
def test_a_correct_snippet_copied_from_another_page_is_refused(
    name: str, given: Given, block_number: int, page: int | None
) -> None:
    """The value is right and the snippet is printed, but not on the page of the
    block that gives it: refused. A page that prints the same text is a page where
    the snippet is real, and the field is stored with the page of the block."""
    answers = [EMPTY, EMPTY, EMPTY]
    answers[block_number] = only(name, given)
    out = both(invoice(), *answers)
    if page is None:
        assert out.fields == []
    else:
        assert stored(out)[name][1] == page


# ------------------------------------------- 3. an identifier or a date or an amount
#                                              given to the wrong field


@pytest.mark.parametrize(
    ("name", "given", "block_number"),
    [
        ("invoice_number", CUSTOMER_NO, 0),
        ("invoice_number", ORDER_NO, 2),
        ("invoice_date", DUE_DATE, 2),
        ("invoice_date", ESTIMATE_DATE, 0),
        ("invoice_date", DEPOSIT_DATE, 2),
        ("invoice_date", DONE_DATE, 1),
        ("total", SUBTOTAL, 0),
        ("total", BROUGHT_1, 1),
        ("total", BROUGHT_2, 2),
        ("total", CARRIED, 1),
        ("total", SUBTOTAL_3, 2),
        ("total", VAT, 2),
        ("total", DEPOSIT, 2),
    ],
    ids=lambda v: v[1][:18] if isinstance(v, tuple) else str(v),
)
def test_a_true_snippet_with_the_wrong_label_is_stored_at_half(
    name: str, given: Given, block_number: int
) -> None:
    """The value and the snippet are true and on the page; the words in front of the
    value name another thing. The field is stored with its page and the snippet of
    the model at 0.5, so a person checks it. Never 0.9."""
    answers = [EMPTY, EMPTY, EMPTY]
    answers[block_number] = only(name, given)
    out = both(invoice(), *answers)
    value, page, confidence = stored(out)[name]
    assert (value, page, confidence) == (given[0], block_number + 1, UNVERIFIED)
    assert field(out, name).source.snippet == given[1]


def test_a_bare_snippet_under_two_labels_is_not_verified() -> None:
    """The amount 1,284.50 is on page 3 under "Brought forward" (no meaning),
    "Subtotal before VAT" (another thing) and "Total": a bare snippet cannot say
    which one the model read, so the veto of the subtotal wins."""
    out = both(invoice(), EMPTY, EMPTY, block(total=(1284.5, "1,284.50")))
    assert stored(out)["total"] == (1284.5, 3, UNVERIFIED)


def test_a_person_with_the_wrong_role_keeps_09_known_limit() -> None:
    """Person fields have no label check (#125, `labels.py`): the site contact read
    as the customer is stored at 0.9. Also in the page scope: yes."""
    out = both(invoice(), block(customer_name=SITE_CONTACT), EMPTY, EMPTY)
    assert stored(out)["customer_name"] == ("Gregor Imhof", 1, VERIFIED)


def test_a_company_with_the_wrong_role_keeps_09_known_limit() -> None:
    """Free text and company fields are out of scope of the label check: the
    subcontractor read as the repair company is stored at 0.9. Also in the page
    scope: yes."""
    out = both(invoice(), EMPTY, EMPTY, block(repair_company=SUBCONTRACTOR))
    assert stored(out)["repair_company"] == ("Rohrmeister Frei AG", 3, VERIFIED)


# ---------------------------------------------------- 4. a value printed on many pages


def test_a_running_header_given_in_every_block_is_stored_once_without_a_conflict() -> (
    None
):
    out = both(
        invoice(),
        block(invoice_number=INV),
        block(invoice_number=HEAD),
        block(invoice_number=HEAD),
    )
    assert stored(out) == {"invoice_number": ("BS-26-0417", 1, VERIFIED)}
    assert field(out, "invoice_number").source.snippet == INV[1]


def test_a_header_given_only_in_the_block_of_page_2_is_stored_with_page_2() -> None:
    out = both(invoice(), EMPTY, block(invoice_number=HEAD), EMPTY)
    assert stored(out) == {"invoice_number": ("BS-26-0417", 2, VERIFIED)}


def test_the_first_block_that_gives_the_header_wins() -> None:
    out = both(invoice(), EMPTY, block(invoice_number=HEAD), block(invoice_number=HEAD))
    assert stored(out)["invoice_number"][1] == 2


def test_a_header_with_another_case_is_no_conflict() -> None:
    """Case and spaces are ignored by the conflict rule; the stored value is the one
    of the first block, as the model wrote it."""
    out = both(
        invoice(),
        block(invoice_number=INV),
        block(invoice_number=("bs-26-0417", HEAD[1])),
        EMPTY,
    )
    assert stored(out) == {"invoice_number": ("BS-26-0417", 1, VERIFIED)}


def test_the_header_in_the_block_of_page_1_is_refused_and_the_next_block_wins() -> None:
    """The snippet "Invoice BS-26-0417" is not on page 1 (the label there is "Invoice
    no.:"), so block 1 gives no field and block 2 does."""
    out = both(invoice(), block(invoice_number=HEAD), block(invoice_number=HEAD), EMPTY)
    assert stored(out) == {"invoice_number": ("BS-26-0417", 2, VERIFIED)}


# ----------------------------------------------------- 5. the same number, two meanings


def test_the_total_amount_printed_on_page_1_as_an_estimate_is_stored_at_half() -> None:
    """Page 1 prints 1,284.50 as a cost estimate; page 3 prints it as the total. A
    block 1 that gives it as `total` is stored with page 1 and 0.5. The block of
    page 3 verifies it on its own page, but the first block that gives a stored field
    wins: the field stays at page 1, 0.5. A person checks it (safe, but the right
    page and the 0.9 are lost)."""
    first = block(total=(1284.5, "CHF 1,284.50"))
    out = both(invoice(), first, EMPTY, block(total=TOTAL))
    assert stored(out) == {"total": (1284.5, 1, UNVERIFIED)}


def test_a_label_of_another_page_never_verifies_a_value() -> None:
    """The estimate of page 1 alone, with a label on page 3 that would fit: the
    label check reads the text of the page of the block."""
    for snippet in (
        "CHF 1,284.50",
        "1,284.50",
        "Cost estimate of 2026-09-20: CHF 1,284.50",
    ):
        out = both(invoice(), block(total=(1284.5, snippet)), EMPTY, EMPTY)
        assert stored(out) == {"total": (1284.5, 1, UNVERIFIED)}, snippet


def test_the_amount_given_in_the_block_of_page_2_with_the_carried_row_is_half() -> None:
    out = both(invoice(), EMPTY, block(total=CARRIED), block(total=TOTAL))
    assert stored(out) == {"total": (1284.5, 2, UNVERIFIED)}


# --------------------------------------------------------------------- 6. conflicts

CONFLICTS = [
    # name, (the first block, its value), (the later block, its value)
    ("invoice_number", (0, INV), (2, ORDER_NO)),  # right first
    ("invoice_number", (0, CUSTOMER_NO), (1, HEAD)),  # wrong first
    ("invoice_date", (0, DATE), (2, DUE_DATE)),
    ("invoice_date", (0, ESTIMATE_DATE), (2, DATE)),
    ("customer_name", (0, CUSTOMER), (2, SIGNER)),
    ("customer_name", (0, SITE_CONTACT), (2, CUSTOMER)),
    ("total", (0, SUBTOTAL), (2, TOTAL)),
    ("total", (1, CARRIED), (2, VAT)),
]


@pytest.mark.parametrize(
    ("name", "first", "later"),
    CONFLICTS,
    ids=[f"{c[0]}-{i}" for i, c in enumerate(CONFLICTS)],
)
def test_two_values_in_two_blocks_keep_the_first_at_half(
    name: str, first: tuple[int, Given], later: tuple[int, Given]
) -> None:
    answers = [EMPTY, EMPTY, EMPTY]
    for index, given in (first, later):
        answers[index] = only(name, given)
    out = both(invoice(), *answers)
    value, page, confidence = stored(out)[name]
    assert (value, page, confidence) == (first[1][0], first[0] + 1, UNVERIFIED)


def test_a_third_block_that_repeats_the_first_value_does_not_clear_the_conflict() -> (
    None
):
    out = both(
        invoice(),
        block(invoice_date=DATE),
        block(invoice_date=DONE_DATE),
        block(invoice_date=DATE),
    )
    assert stored(out) == {"invoice_date": ("2026-10-09", 1, UNVERIFIED)}


def test_the_first_value_refused_at_its_source_still_counts_for_the_conflict() -> None:
    """Block 1 gives another date with an invented snippet: not stored. Block 3 gives
    the right date: stored, but at 0.5, because two values were given."""
    invented = ("2026-09-20", "Date: 2026-09-20")  # printed nowhere
    out = both(invoice(), block(invoice_date=invented), EMPTY, block(invoice_date=DATE))
    assert stored(out) == {"invoice_date": ("2026-10-09", 3, UNVERIFIED)}


@pytest.mark.parametrize("first_wrong_block", [0, 1])
def test_a_refused_misplaced_value_with_another_value_makes_a_conflict(
    first_wrong_block: int,
) -> None:
    """The right date is given in the wrong block (refused: not on that page), and
    again in the right one with another date in a later block."""
    answers = [EMPTY, EMPTY, EMPTY]
    answers[first_wrong_block] = block(invoice_date=DUE_DATE)  # page 3 text
    answers[2] = block(invoice_date=DATE)
    out = both(invoice(), *answers)
    assert stored(out) == {"invoice_date": ("2026-10-09", 3, UNVERIFIED)}


# ------------------------------------------------------------------ 7. placeholders


@pytest.mark.parametrize("placeholder", ["Not answerable", "n/a", "null", "unknown"])
def test_a_placeholder_is_not_stored_and_makes_no_conflict(placeholder: str) -> None:
    holder: Given = (placeholder, placeholder)
    first = both(
        invoice(), block(invoice_number=holder), block(invoice_number=HEAD), EMPTY
    )
    assert stored(first) == {"invoice_number": ("BS-26-0417", 2, VERIFIED)}
    last = both(
        invoice(), block(invoice_number=INV), EMPTY, block(invoice_number=holder)
    )
    assert stored(last) == {"invoice_number": ("BS-26-0417", 1, VERIFIED)}
    alone = both(invoice(), block(invoice_number=holder), EMPTY, EMPTY)
    assert alone.fields == []


def test_a_placeholder_in_an_amount_or_a_date_key_leaves_the_other_keys() -> None:
    """A text where a number or a date is expected fails the schema for that key
    only; the rest of the block and the other blocks are read."""
    bad = block(invoice_number=INV, repair_company=COMPANY)
    bad["values"]["total"] = "n/a"
    bad["values"]["invoice_date"] = "unknown"
    out = both(invoice(), bad, block([L4, L5, L6]), block(total=TOTAL))
    assert set(stored(out)) == {"invoice_number", "repair_company", "total"}
    assert stored(out)["total"] == (1284.5, 3, VERIFIED)


# ----------------------------------------------- 8. unsupported or fabricated snippets


@pytest.mark.parametrize(
    ("name", "value", "snippet"),
    [
        ("invoice_number", "BS-26-0417", "Invoice nr. BS-26-0417"),  # printed nowhere
        ("invoice_number", "BS-26-0471", "Invoice no.: BS-26-0417"),  # not the value
        ("total", 1284.5, "Total CHF 1,284.00"),
        ("total", 1284.5, "Total EUR 1,284.50"),
        ("total", 1284.5, "Total CHF 2,284.50"),
        ("total", 284.5, "284.50"),  # cuts the number "1,284.50"
        ("total", 84.5, "Total CHF 1,284.50"),  # the snippet holds 84.50? no: 1,284.50
        ("invoice_date", "2026-10-10", "Invoice date: 2026-10-09"),
        ("repair_company", "Brennholt Sanitaer", "Rohrmeister Frei AG"),
    ],
)
def test_a_snippet_that_is_not_printed_or_does_not_hold_the_value_is_refused(
    name: str, value: Any, snippet: str
) -> None:
    out = both(invoice(), EMPTY, EMPTY, only(name, (value, snippet)))
    assert name not in stored(out)
    out = both(invoice(), only(name, (value, snippet)), EMPTY, EMPTY)
    assert name not in stored(out)


def test_a_snippet_that_cuts_a_number_is_refused_on_the_page_that_prints_it() -> None:
    """ "284.50" is the end of "1,284.50" on page 3; "0.0417" would be a cut too."""
    out = both(invoice(), EMPTY, EMPTY, block(total=(284.5, "284.50")))
    assert out.fields == []
    out = both(invoice(), EMPTY, EMPTY, block(total=(1284.5, "Total CHF 1,284.5")))
    assert out.fields == [], "the page prints 1,284.50: the snippet cuts the cents"


def test_a_cut_identifier_is_stored_at_half() -> None:
    """The value "BS-26" is the start of "BS-26-0417": the snippet is printed and
    holds it, no number is cut, but text sticks to the value. Stored at 0.5."""
    cut: Given = ("BS-26", "Invoice no.: BS-26")
    out = both(invoice(), block(invoice_number=cut), EMPTY, EMPTY)
    assert stored(out) == {"invoice_number": ("BS-26", 1, UNVERIFIED)}


def test_a_snippet_of_120_characters_is_accepted_and_121_is_refused() -> None:
    page = texts(invoice())[0]
    # a window of the page text that is printed (no number is cut) at both lengths,
    # so the length is the only difference between the two snippets
    start = next(
        s
        for s in range(page.index("Invoice no.:"))
        if all(
            omni._shown(page[s : s + n], page) and "BS-26-0417" in page[s : s + n]
            for n in (120, 121)
        )
    )
    for length, kept in ((120, True), (121, False)):
        snippet = page[start : start + length]
        assert len(snippet) == length
        out = both(
            invoice(), block(invoice_number=("BS-26-0417", snippet)), EMPTY, EMPTY
        )
        assert ("invoice_number" in stored(out)) == kept, length


def test_a_paragraph_as_snippet_is_refused() -> None:
    page = texts(invoice())[0]
    assert len(page) > 3 * omni.MAX_SNIPPET
    out = both(invoice(), block(invoice_number=("BS-26-0417", page)), EMPTY, EMPTY)
    assert out.fields == []


def test_a_snippet_made_of_spaces_or_with_no_text_is_refused() -> None:
    for snippet in ("   ", "", "\n"):
        out = both(
            invoice(), block(invoice_number=("BS-26-0417", snippet)), EMPTY, EMPTY
        )
        assert out.fields == []


# ----------------------------------------------------------------- 9. scan attribution

SCAN = frozenset({1, 2, 3})
FIELDS = [
    ("invoice_number", INV),
    ("invoice_date", DATE),
    ("repair_company", COMPANY),
    ("customer_name", CUSTOMER),
    ("total", TOTAL),
]


@pytest.mark.parametrize("block_number", [0, 1, 2])
@pytest.mark.parametrize(("name", "given"), FIELDS, ids=[n for n, _ in FIELDS])
def test_on_a_scan_a_value_gets_the_page_of_its_block_at_half(
    name: str, given: Given, block_number: int
) -> None:
    """Nothing can check the page of a value on a scan: the page of the block is the
    claim of the model, stored as the whole page with no snippet, at 0.5, whichever
    block gives it (even a block of a page where the value is not). Document scope
    only as a risk: in the page scope the page is the page of the call (the same
    answers give the same `Extraction`)."""
    answers = [EMPTY, EMPTY, EMPTY]
    answers[block_number] = only(name, given)
    out = both(invoice(SCAN), *answers)
    assert stored(out) == {name: (given[0], block_number + 1, UNVERIFIED)}
    f = field(out, name)
    assert f.source.snippet is None and f.source.box is not None


def test_a_value_in_two_scan_blocks_keeps_the_first_page() -> None:
    out = both(invoice(SCAN), EMPTY, block(total=TOTAL), block(total=TOTAL))
    assert stored(out) == {"total": (1284.5, 2, UNVERIFIED)}


@pytest.mark.parametrize("shift", range(8))
def test_no_arrangement_of_blocks_gives_a_scan_field_08_or_more(shift: int) -> None:
    """Every field goes to every subset of the 3 blocks over the 8 runs (the shifts),
    and the six rows are spread over the blocks. On a document with no text layer:
    first block wins, 0.5 everywhere, no line, no field above 0.8."""
    answers = [block(), block(), block()]
    for i, (name, (value, snippet)) in enumerate(FIELDS):
        mask = (i + shift) % 8
        for b in range(3):
            if mask >> b & 1:
                answers[b]["values"][name] = value
                answers[b]["snippets"][name] = snippet
    for i, line in enumerate(SIX):
        if (i + shift) % 4 != 3:
            answers[(i + shift) % 3]["values"]["line_items"].append(line)
    out = both(invoice(SCAN), *answers)
    assert out.line_items == []
    for i, (name, (value, _)) in enumerate(FIELDS):
        mask = (i + shift) % 8
        if mask == 0:
            assert name not in stored(out)
        else:
            first = min(b for b in range(3) if mask >> b & 1)
            assert stored(out)[name] == (value, first + 1, UNVERIFIED)
    assert all(f.confidence < LOW_CONFIDENCE for f in out.fields)


def test_a_scan_with_an_invented_snippet_is_still_refused_when_it_does_not_hold() -> (
    None
):
    """On a scan the snippet is not checked against the page, but it must hold the
    value (the answer must not contradict itself)."""
    out = both(invoice(SCAN), block(total=(1284.5, "Total CHF 999.00")), EMPTY, EMPTY)
    assert out.fields == []


# --------------------------------------------------------------- 10. text and scan

MIXED = frozenset({2})


def test_in_a_mixed_document_the_text_blocks_verify_and_the_scan_block_gives_half() -> (
    None
):
    out = both(
        invoice(MIXED),
        PERFECT[0],
        block(invoice_number=HEAD),
        PERFECT[2],
    )
    got = stored(out)
    assert got["invoice_number"] == ("BS-26-0417", 1, VERIFIED)  # block 1 wins
    assert got["total"] == (1284.5, 3, VERIFIED)
    assert out.line_items == [L1, L2, L3]  # see the known limit below


def test_a_scan_block_gives_half_with_the_box_of_its_page() -> None:
    out = both(invoice(MIXED), EMPTY, block(invoice_number=HEAD), EMPTY)
    assert stored(out) == {"invoice_number": ("BS-26-0417", 2, UNVERIFIED)}
    assert field(out, "invoice_number").source.box is not None


def test_a_text_page_value_given_in_the_scan_block_is_half_known_limit() -> None:
    """The reader cannot know where a value is on a scan: the customer name is printed
    on page 1 (a text page) and the model gives it in the block of the scan page 2.
    Stored with page 2, the box of page 2, 0.5. Also in the page scope: yes (the same
    code path; there a hallucinated value on the image of page 2), but the document
    call, where the model sees every page, makes the misplacement likelier."""
    out = both(invoice(MIXED), EMPTY, block(customer_name=CUSTOMER), EMPTY)
    assert stored(out) == {"customer_name": ("Ortrud Vanselow", 2, UNVERIFIED)}


def test_a_conflict_between_a_text_block_and_a_scan_block_puts_the_field_at_half() -> (
    None
):
    other: Given = ("BS-26-0471", "BS-26-0471")
    out = both(
        invoice(MIXED), block(invoice_number=INV), block(invoice_number=other), EMPTY
    )
    assert stored(out) == {"invoice_number": ("BS-26-0417", 1, UNVERIFIED)}
    same = both(
        invoice(MIXED), block(invoice_number=INV), block(invoice_number=HEAD), EMPTY
    )
    assert stored(same) == {"invoice_number": ("BS-26-0417", 1, VERIFIED)}


def test_a_scan_block_that_gives_a_line_empties_the_list_and_marks_the_total() -> None:
    out = both(invoice(MIXED), PERFECT[0], PERFECT[1], PERFECT[2])
    assert out.line_items == []
    assert stored(out)["total"] == (1284.5, 3, UNVERIFIED)
    assert stored(out)["customer_name"][2] == VERIFIED  # the other fields stay


def test_a_scan_block_that_gives_no_line_leaves_the_lines_of_the_text_pages_known_limit(
    tmp_path: Path,
) -> None:
    """Page 2 is an image and holds rows 4 to 6; the model gives none (or the reader
    is not told). The reader cannot know that a scan page holds lines: the three lines
    of page 1 are stored as if they were the table, and `total` keeps 0.9. Only T003
    sees it. Also in the page scope: yes."""
    out = both(invoice(MIXED), PERFECT[0], block(invoice_number=HEAD), PERFECT[2])
    assert out.line_items == [L1, L2, L3]
    assert stored(out)["total"][2] == VERIFIED
    assert t003(out, tmp_path) == ["T003"]


# --------------------------------------------------------------- 11. invented lines


@pytest.mark.parametrize(
    "line",
    [
        row("Pipe replacement", 2.0, 45.0, 91.0),  # an amount that is not printed
        row("Pipe replacement", 2.0, 46.0, 90.0),
        row("Pipe replacement and cleaning", 2.0, 45.0, 90.0),
        row("Wall tiles", 10.0, 18.5, 185.0),  # a line of page 2 in the block of page 1
        row(None, None, None, 1285.5),  # an amount that no page prints
    ],
)
def test_a_line_that_is_not_on_its_page_empties_the_list_and_marks_the_total(
    line: dict[str, Any],
) -> None:
    answers = [
        block([L1, L2, L3, line], invoice_number=INV, customer_name=CUSTOMER),
        block([L4, L5, L6]),
        block(total=TOTAL),
    ]
    out = both(invoice(), *answers)
    assert out.line_items == []
    assert stored(out)["total"] == (1284.5, 3, UNVERIFIED)
    assert stored(out)["invoice_number"] == ("BS-26-0417", 1, VERIFIED)
    assert stored(out)["customer_name"][2] == VERIFIED


def test_a_line_whose_description_is_on_another_page_is_refused() -> None:
    out = both(invoice(), block([L1, L2, L3, L4]), block([L5, L6]), block(total=TOTAL))
    assert out.line_items == [] and stored(out)["total"][2] == UNVERIFIED


def test_the_rows_in_the_blocks_of_the_wrong_pages_are_refused() -> None:
    """The right rows, shifted by one block: each row is on the page before."""
    out = both(invoice(), EMPTY, block([L1, L2, L3]), block([L4, L5, L6], total=TOTAL))
    assert out.line_items == []
    assert stored(out)["total"][2] == UNVERIFIED


def test_a_line_with_only_an_amount_printed_elsewhere_is_stored_known_limit(
    tmp_path: Path,
) -> None:
    """Page 1 prints 1,284.50 as a cost estimate. A "line" made of that amount alone
    has no description to check: its one value is on the page, so it is confirmed and
    stored as a row of the table. Also in the page scope: yes. T003 sees the sum."""
    stray = row(None, None, None, 1284.5)
    out = both(invoice(), block([L1, L2, L3, stray]), PERFECT[1], PERFECT[2])
    assert out.line_items == [L1, L2, L3, stray, L4, L5, L6]
    assert stored(out)["total"][2] == VERIFIED
    assert t003(out, tmp_path) == ["T003"]


def test_a_line_built_from_numbers_of_different_rows_is_stored_known_limit(
    tmp_path: Path,
) -> None:
    """Each value of the line is printed on page 1 (the description of row 1, the
    quantity of row 3, the unit price of row 2, the amount of the subtotal), but no
    row prints them together. The check is value by value, so the line passes. Also
    in the page scope: yes. T003 sees the wrong sum when the total exists."""
    made_up = row("Pipe replacement", 3.0, 120.0, 540.0)
    out = both(
        invoice(), block([made_up, L2, L3]), block([L4, L5, L6]), block(total=TOTAL)
    )
    assert out.line_items == [made_up, L2, L3, L4, L5, L6]
    assert stored(out)["total"][2] == VERIFIED
    assert t003(out, tmp_path) == ["T003"]


# ------------------------------------------------- 12. duplicate and repeated lines

COPY = [
    "Invoice no.: CP-26-0220",
    "Parking permit  1  25.00  25.00",
    "Window cleaning  2  12.50  25.00",
    "Total CHF 50.00",
]
P1 = row("Parking permit", 1.0, 25.0, 25.0)
P2 = row("Window cleaning", 2.0, 12.5, 25.0)
COPY_TOTAL: Given = (50.0, "Total CHF 50.00")


def test_a_table_printed_on_two_pages_gives_double_lines_that_t003_sees(
    tmp_path: Path,
) -> None:
    """Page 2 is a copy of page 1 (a duplicate sheet): the model reads both tables.
    Each block's lines are on its page, so both are kept: the reader never removes a
    line. The sum is twice the total: T003 fires. Also in the page scope: yes."""
    data = build_pdf([COPY, COPY])
    first = block([P1, P2], total=COPY_TOTAL)
    out = both(data, first, block([P1, P2], total=COPY_TOTAL))
    assert out.line_items == [P1, P2, P1, P2]
    assert stored(out) == {"total": (50.0, 1, VERIFIED)}  # the same value: no conflict
    assert t003(out, tmp_path) == ["T003"]
    single = both(data, first, EMPTY)
    assert single.line_items == [P1, P2]
    assert t003(single, tmp_path) == []  # the control: one table, the sum holds


def test_a_brought_forward_row_read_as_a_line_is_kept_and_t003_sees_it(
    tmp_path: Path,
) -> None:
    """ "Brought forward from page 1 540.00" is printed on page 2, so a line made of
    it is confirmed (description and amount are on the page). Kept, and the sum is
    540.00 too high: T003 fires. Also in the page scope: yes."""
    carried = row("Brought forward from page 1", None, None, 540.0)
    out = both(invoice(), PERFECT[0], block([carried, L4, L5, L6]), PERFECT[2])
    assert out.line_items == [L1, L2, L3, carried, L4, L5, L6]
    assert stored(out)["total"] == (1284.5, 3, VERIFIED)
    assert t003(out, tmp_path) == ["T003"]
    assert t003(both(invoice(), *PERFECT), tmp_path) == []


def test_two_equal_real_lines_on_one_page_are_both_kept(tmp_path: Path) -> None:
    twin = [
        "Invoice no.: TW-26-0007",
        "Parking permit  1  25.00  25.00",
        "Parking permit  1  25.00  25.00",
        "Total CHF 50.00",
    ]
    data = build_pdf([twin, ["General terms"]])
    out = both(data, block([P1, P1], total=COPY_TOTAL), EMPTY)
    assert out.line_items == [P1, P1]
    assert t003(out, tmp_path) == []  # 25 + 25 = 50: the real sum holds


# ------------------------------------------------ 13. incomplete or invalid lines


@pytest.mark.parametrize(
    "line",
    [
        row("Pipe replacement", 2.0, 45.0, None),  # a null amount
        row("Pipe replacement", None, None, None),  # only a description
    ],
    ids=["null amount", "only a description"],
)
def test_a_line_with_missing_values_is_stored_and_t003_stays_silent_known_limit(
    line: dict[str, Any], tmp_path: Path
) -> None:
    """The line is on the page, so it is confirmed. But T003 reads the amounts and
    skips a list with a missing one: the lines then add up to nothing that T003
    checks, whatever the total. Also in the page scope: yes."""
    out = both(
        invoice(), block([line, L2, L3]), block([L4, L5, L6]), block(total=TOTAL)
    )
    assert out.line_items == [line, L2, L3, L4, L5, L6]
    assert stored(out)["total"][2] == VERIFIED
    assert t003(out, tmp_path) == []  # the sum is not checked: an amount is missing


def test_a_text_as_amount_refuses_the_list_of_that_block() -> None:
    bad = row("Pipe replacement", 2.0, 45.0, "90.00")
    out = both(
        invoice(),
        block([bad, L2, L3], invoice_number=INV),
        block([L4, L5, L6]),
        block(total=TOTAL),
    )
    assert out.line_items == []
    assert stored(out)["total"] == (1284.5, 3, UNVERIFIED)
    assert stored(out)["invoice_number"] == ("BS-26-0417", 1, VERIFIED)


def test_an_all_null_line_is_dropped() -> None:
    empty = row(None, None, None, None)
    out = both(invoice(), block([L1, empty, L2, L3]), block([L4, L5, L6]), PERFECT[2])
    assert out.line_items == SIX
    assert stored(out)["total"][2] == VERIFIED


def test_a_block_with_refused_lines_empties_the_list_of_the_other_blocks() -> None:
    bad = {"description": "Pipe replacement", "amount": 90.0}  # keys are missing
    out = both(invoice(), block([L1, L2, L3]), PERFECT[1], PERFECT[2])
    assert out.line_items == SIX
    broken = PERFECT[1]["values"] | {"line_items": [bad]}
    out = both(
        invoice(),
        PERFECT[0],
        {"values": broken, "snippets": PERFECT[1]["snippets"]},
        PERFECT[2],
    )
    assert out.line_items == []
    assert stored(out)["total"][2] == UNVERIFIED
    assert stored(out)["customer_name"][2] == VERIFIED


# ------------------------------------------------------ 14. a silently omitted row


@pytest.mark.parametrize("omitted", range(6))
def test_a_silently_omitted_row_is_stored_as_a_complete_list_known_limit(
    omitted: int, tmp_path: Path
) -> None:
    """The model gives 5 of the 6 printed rows, each one correct. The validation
    proves each line it receives, not the completeness of the table: the five lines
    are stored as if complete, and `total` keeps 0.9. Only the sum check T003 can
    notice it, when a total exists. Also in the page scope: yes."""
    rows = [line for i, line in enumerate(SIX) if i != omitted]
    answers = [
        block(
            [line for line in rows if line in (L1, L2, L3)],
            invoice_number=INV,
            invoice_date=DATE,
            repair_company=COMPANY,
            customer_name=CUSTOMER,
        ),
        block([line for line in rows if line in (L4, L5, L6)]),
        block(total=TOTAL),
    ]
    out = both(invoice(), *answers)
    assert out.line_items == rows and len(out.line_items) == 5
    assert stored(out)["total"] == (1284.5, 3, VERIFIED)
    assert t003(out, tmp_path) == ["T003"]


def test_without_a_total_an_omitted_row_is_not_seen_by_anything_known_limit(
    tmp_path: Path,
) -> None:
    """The control of the previous test: the same 5 lines and no total. T003 needs
    a total to compare with; nothing in the `Extraction` says that a row is missing.
    Also in the page scope: yes."""
    out = both(
        invoice(),
        block([L1, L2, L3], invoice_number=INV),
        block([L4, L5]),
        EMPTY,
    )
    assert len(out.line_items) == 5 and "total" not in stored(out)
    assert t003(out, tmp_path) == []


def test_a_whole_table_left_out_of_a_block_is_not_seen_known_limit(
    tmp_path: Path,
) -> None:
    """Page 1 prints three rows and the model gives [] for them: no mark ("[]" is
    "no line read", D77). The lines of page 2 are stored, `total` keeps 0.9, T003
    fires only because the sum is short. Also in the page scope: yes."""
    out = both(invoice(), EMPTY, PERFECT[1], PERFECT[2])
    assert out.line_items == [L4, L5, L6]
    assert stored(out)["total"][2] == VERIFIED
    assert t003(out, tmp_path) == ["T003"]


# --------------------------------------------- 15. missing blocks and failed answers


@pytest.mark.parametrize("missing", [0, 1, 2])
def test_a_missing_page_block_leaves_every_stored_field_below_08_and_no_line(
    missing: int,
) -> None:
    answers: list[Any] = list(PERFECT)
    answers[missing] = None
    out = both(invoice(), *answers)
    assert_a_person_checks(out)
    every = {
        "invoice_number",
        "invoice_date",
        "repair_company",
        "customer_name",
        "total",
    }
    expected = {
        0: {"invoice_number", "total"},  # the header of page 2 and the total
        1: every,
        2: every - {"total"},
    }
    assert set(stored(out)) == expected[missing]


@pytest.mark.parametrize("missing", [(0, 1), (0, 2), (1, 2)])
def test_two_missing_page_blocks_leave_every_stored_field_below_08(
    missing: tuple[int, int],
) -> None:
    answers: list[Any] = list(PERFECT)
    for index in missing:
        answers[index] = None
    out = both(invoice(), *answers)
    assert out.fields  # the one block that came back gives its fields
    assert_a_person_checks(out)


def test_a_block_that_is_absent_from_the_answer_is_a_page_that_was_not_read() -> None:
    answer = blocks(*PERFECT)
    del answer["page_2"]
    document, _ = by_document(invoice(), KIND, answer)
    assert_provenance(document, invoice())
    nulled = both(invoice(), PERFECT[0], None, PERFECT[2])
    assert document.model_dump() == nulled.model_dump()
    assert_a_person_checks(document)


def test_a_document_with_a_missing_block_cannot_go_straight_through() -> None:
    """The routing reads the confidences: one missing block gives a field below 0.8,
    so the claim goes to a person. The same claim with the perfect answer has no
    low-confidence reason (the control). No outcome is a rejection: only the three
    outcomes exist."""
    control = both(invoice(), *PERFECT)
    _, control_reasons = route_with(control)
    assert not any("low confidence" in r for r in control_reasons)
    for missing in range(3):
        answers: list[Any] = list(PERFECT)
        answers[missing] = None
        outcome, reasons = route_with(both(invoice(), *answers))
        assert outcome == Outcome.handler_review
        assert any("low confidence" in r for r in reasons)
        assert outcome.value in {"straight_through", "handler_review", "investigation"}
    assert {o.value for o in Outcome} == {
        "straight_through",
        "handler_review",
        "investigation",
    }


def twice() -> str:
    """The JSON of a good answer with the key "page_1" a second time at the end."""
    one, two, three, again = (json.dumps(b) for b in (*PERFECT, EMPTY))
    return f'{{"page_1": {one}, "page_2": {two}, "page_3": {three}, "page_1": {again}}}'


def cut_answer() -> str:
    return json.dumps(blocks(*PERFECT))[:-60]


FAILED = [
    ("not json", "this is not json"),
    ("empty", ""),
    ("cut", cut_answer()),
    ("cut, and marked length", finished(cut_answer(), "length")),
    ("a list", json.dumps([PERFECT[0]])),
    ("page_1 twice", twice()),
]


def test_the_cut_answer_does_not_parse() -> None:
    with pytest.raises(ValueError):
        json.loads(cut_answer())


@pytest.mark.parametrize(("name", "reply"), FAILED, ids=[n for n, _ in FAILED])
def test_a_failed_document_answer_gives_no_field_no_line_and_no_exception(
    name: str, reply: Any
) -> None:
    out, stub = by_document(invoice(), KIND, reply)
    assert_provenance(out, invoice())
    assert out.document_type == TYPE and out.fields == [] and out.line_items == []
    assert len(stub.requests) == 2, "no second call, no call per page"


def test_a_whole_answer_that_the_endpoint_marks_as_cut_goes_to_a_person() -> None:
    """Finish reason `length` and JSON that closes: the values are read, with their
    pages, and none is final. Not dropped: a document with no field has no mark."""
    out, stub = by_document(invoice(), KIND, finished(blocks(*PERFECT), "length"))
    assert_provenance(out, invoice())
    assert out.fields and out.line_items == []
    assert all(f.confidence < LOW_CONFIDENCE for f in out.fields)
    whole, _ = by_document(invoice(), KIND, blocks(*PERFECT))
    assert [(f.name, f.value, f.source.page) for f in out.fields] == [
        (f.name, f.value, f.source.page) for f in whole.fields
    ]
    assert len(stub.requests) == 2


@pytest.mark.parametrize("status", [500, 503, 429])
def test_a_document_call_that_gets_no_answer_after_the_retries_gives_no_field(
    status: int,
) -> None:
    stub = Model({"document_type": KIND}, status, status, status)
    out = stub.scoped("document").extract(doc(3), invoice(), "travel")
    assert out.fields == [] and out.line_items == []
    assert len(stub.requests) == 4  # classify, then the call and its two retries
    assert all(len(r["messages"][0]["content"]) == 7 for r in stub.requests[1:])


@pytest.mark.parametrize("status", [400, 401, 404])
def test_a_client_error_ends_the_document_call_at_once(status: int) -> None:
    stub = Model({"document_type": KIND}, status)
    out = stub.scoped("document").extract(doc(3), invoice(), "travel")
    assert out.fields == [] and len(stub.requests) == 2


def test_no_failed_answer_goes_through_the_routing_as_a_rejection() -> None:
    for _, reply in FAILED:
        out, _ = by_document(invoice(), KIND, reply)
        outcome, _ = route_with(out)
        assert outcome in set(Outcome)


# ------------------------------------- 16. the right total must be downgraded when...


def right_total(out: Extraction, snippet: str = TOTAL[1]) -> None:
    value, page, confidence = stored(out)["total"]
    assert (value, page) == (1284.5, 3)
    assert field(out, "total").source.snippet == snippet
    assert confidence < LOW_CONFIDENCE


def test_the_right_total_is_downgraded_when_a_page_was_not_read() -> None:
    right_total(both(invoice(), PERFECT[0], None, PERFECT[2]))


def test_the_right_total_is_downgraded_by_an_unknown_key_in_the_answer() -> None:
    """Document scope only: the key is not a page of the document."""
    for key in ("page_4", "page_0", "pages", "Page_3"):
        answer = blocks(*PERFECT)
        answer[key] = EMPTY
        out, _ = by_document(invoice(), KIND, answer)
        assert_provenance(out, invoice())
        right_total(out)
        assert out.line_items == []


def test_the_right_total_is_downgraded_when_a_line_is_not_confirmed() -> None:
    answers = [block([L1, row("Pipe replacement", 2.0, 45.0, 91.0), L3]), *PERFECT[1:]]
    answers[0]["values"].update(
        PERFECT[0]["values"] | {"line_items": answers[0]["values"]["line_items"]}
    )
    right_total(both(invoice(), *answers))


def test_the_right_total_is_downgraded_when_another_block_gives_another_total() -> None:
    """The right total comes first in a variant: page 1 prints the total, page 2
    prints a revised one. The first value stays, at 0.5, with its source."""
    data = build_pdf([["Total CHF 1,284.50"], ["Revised total CHF 1,300.00"]])
    out = both(
        data,
        block(total=TOTAL),
        block(total=(1300.0, "Revised total CHF 1,300.00")),
    )
    assert stored(out) == {"total": (1284.5, 1, UNVERIFIED)}
    assert field(out, "total").source.snippet == TOTAL[1]


def test_the_right_total_is_downgraded_when_the_label_does_not_fit() -> None:
    """The value is the total and page 3 prints it, but the model read the line
    "Subtotal before VAT"."""
    out = both(invoice(), PERFECT[0], PERFECT[1], block(total=SUBTOTAL_3))
    right_total(out, SUBTOTAL_3[1])


def test_the_right_total_is_downgraded_when_a_scan_page_gives_lines() -> None:
    out = both(invoice(MIXED), *PERFECT)
    right_total(out)
    assert out.line_items == []


# ----------------------------------------------------------- 17. the split line item

SPLIT = [
    [
        "Invoice no.: SP-26-0101",
        "Customer: Ortrud Vanselow",
        "Carpentry work  4  60.00  240.00",
        "Installation of stainless steel kitchen  1  980.00  980.00",
    ],
    ["sink unit with mixer tap", "Total CHF 1,220.00"],
]
CARPENTRY = row("Carpentry work", 4.0, 60.0, 240.0)
FIRST_PART = row("Installation of stainless steel kitchen", 1.0, 980.0, 980.0)
JOINED = row(
    "Installation of stainless steel kitchen sink unit with mixer tap",
    1.0,
    980.0,
    980.0,
)
REST = row("sink unit with mixer tap", None, None, None)
SPLIT_FIELDS: dict[str, Given] = {
    "invoice_number": ("SP-26-0101", "Invoice no.: SP-26-0101"),
    "customer_name": CUSTOMER,
}
SPLIT_TOTAL: Given = (1220.0, "Total CHF 1,220.00")


def split_pdf() -> bytes:
    return build_pdf(SPLIT)


def test_a_joined_line_over_a_page_break_is_not_confirmed_known_limit() -> None:
    """(a) The model joins the description over the break and gives one line in the
    block of page 1. The joined text is not printed as one text on page 1, so the
    line is not confirmed: no line is stored and `total` is at 0.5. The right answer
    is refused, which is the opposite of what a reader should do. Also in the page
    scope: yes. The contract cannot represent a line that spans two pages."""
    out = both(
        split_pdf(),
        block_of(TYPE, [CARPENTRY, JOINED], SPLIT_FIELDS),
        block(total=SPLIT_TOTAL),
    )
    assert out.line_items == []
    assert stored(out)["total"] == (1220.0, 2, UNVERIFIED)
    assert stored(out)["invoice_number"][2] == VERIFIED


def test_the_cut_description_is_stored_as_a_complete_list_known_limit(
    tmp_path: Path,
) -> None:
    """(b) The model gives the cut description in block 1 and nothing in block 2: the
    list is stored with a description that stops at the page break, and looks
    complete (`total` keeps 0.9; the sum 1,220.00 holds, so T003 is silent). Also in
    the page scope: yes."""
    out = both(
        split_pdf(),
        block_of(TYPE, [CARPENTRY, FIRST_PART], SPLIT_FIELDS),
        block(total=SPLIT_TOTAL),
    )
    assert out.line_items == [CARPENTRY, FIRST_PART]
    assert stored(out)["total"] == (1220.0, 2, VERIFIED)
    assert t003(out, tmp_path) == []


def test_the_rest_of_a_split_line_as_a_second_line_is_one_line_too_many_known_limit(
    tmp_path: Path,
) -> None:
    """(c) The model gives the first part in block 1 and the rest as a second line in
    block 2: both are confirmed on their own page and stored. The document has two
    rows and the list has three lines. Also in the page scope: yes. T003 skips the
    list (the amount of the extra line is missing), so it stays silent."""
    out = both(
        split_pdf(),
        block_of(TYPE, [CARPENTRY, FIRST_PART], SPLIT_FIELDS),
        block([REST], total=SPLIT_TOTAL),
    )
    assert out.line_items == [CARPENTRY, FIRST_PART, REST]
    assert stored(out)["total"] == (1220.0, 2, VERIFIED)
    assert t003(out, tmp_path) == []


# --------------------------- 18. nothing but the text layer and the label gives 0.9

HOME = {"invoice_number": 0, "invoice_date": 0, "repair_company": 0,
        "customer_name": 0, "total": 2}  # fmt: skip
WRONG_BLOCK = {"invoice_number": 1, "invoice_date": 1, "repair_company": 1,
               "customer_name": 1, "total": 0}  # fmt: skip
LATER = {  # a different value for the field, from a block that comes after
    "invoice_number": (2, ORDER_NO),
    "invoice_date": (2, DUE_DATE),
    "repair_company": (2, SUBCONTRACTOR),
    "customer_name": (2, SIGNER),
    "total": (0, SUBTOTAL),  # the total is on the last page: this one comes before
}
WAYS = [
    "right",
    "wrong_block",
    "scan",
    "conflict",
    "missing_block",
    "unknown_key",
    "unconfirmed_line",  # only `total` is marked by a line that is not confirmed
]
CASES = [
    (name, way)
    for name, _ in FIELDS
    for way in WAYS
    if way != "unconfirmed_line" or name == "total"
]


def way_answers(name: str, way: str) -> tuple[list[Any], bytes]:
    given = dict(FIELDS)[name]
    answers: list[Any] = [block(), block(), block()]
    data = invoice()
    if way == "wrong_block":
        answers[WRONG_BLOCK[name]] = only(name, given)
        return answers, data
    answers[HOME[name]] = only(name, given)
    if way == "scan":
        data = invoice(frozenset({HOME[name] + 1}))
    elif way == "conflict":
        index, other = LATER[name]
        answers[index] = only(name, other)
    elif way == "missing_block":
        answers[(HOME[name] + 1) % 3] = None
    elif way == "unconfirmed_line":
        answers[0]["values"]["line_items"] = [row("Pipe replacement", 2.0, 45.0, 91.0)]
    return answers, data


@pytest.mark.parametrize(("name", "way"), CASES, ids=[f"{n}-{w}" for n, w in CASES])
def test_only_the_snippet_on_the_text_layer_with_a_fitting_label_gives_09(
    name: str, way: str
) -> None:
    """For each field and each way a value can arrive, the confidence is 0.9 only in
    the one case: the snippet is printed on the text layer of the page of its block,
    the label fits, and nothing raises a doubt (every block present, no other value,
    no unknown key, no unconfirmed line). Every other way: not stored, or below 0.8."""
    answers, data = way_answers(name, way)
    if way == "unknown_key":
        answer = blocks(*answers)
        answer["page_9"] = EMPTY
        out, _ = by_document(data, KIND, answer)
        assert_provenance(out, data)
    else:
        out = both(data, *answers)
    got = stored(out)
    if way == "wrong_block":
        assert name not in got
    elif way == "right":
        assert got[name][2] == VERIFIED
    else:
        assert got[name][2] < LOW_CONFIDENCE
    assert [n for n, (_, _, c) in got.items() if c == VERIFIED] == (
        [name] if way == "right" else []
    )


class Spy:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[tuple[str, str, Any, str, str]] = []
        original = labels.verified

        def verified(
            document_type: str, name: str, value: object, snippet: str, text: str
        ) -> bool:
            self.calls.append((document_type, name, value, snippet, text))
            return original(document_type, name, value, snippet, text)

        monkeypatch.setattr(labels, "verified", verified)


def test_the_label_check_gets_the_text_of_one_page_never_a_joined_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = Spy(monkeypatch)
    out = both(invoice(), *PERFECT)
    pages = texts(invoice())
    assert len(spy.calls) == 2 * len(out.fields)  # the document call and the page calls
    assert len(set(pages)) == 3
    for _, name, _, snippet, text in spy.calls:
        assert sum(text == page for page in pages) == 1, "exactly one page"
        assert (
            text != "\n".join(pages) and "Page 1 of 3" not in text or text == pages[0]
        )
        assert omni._shown(snippet, text)
        assert snippet != ""
        page = stored(out)[name][1]
        assert text == pages[page - 1], (name, page)


def test_the_label_check_is_not_called_on_a_scan_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = Spy(monkeypatch)
    both(invoice(SCAN), *PERFECT)
    assert spy.calls == []


def test_a_label_on_another_page_is_never_given_to_the_label_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The estimate on page 1 and the total label on page 3: the text given to the
    label check for the block of page 1 holds no "Total"."""
    spy = Spy(monkeypatch)
    both(invoice(), block(total=(1284.5, "CHF 1,284.50")), EMPTY, EMPTY)
    assert spy.calls and all("Total CHF" not in text for *_, text in spy.calls)


# --------------------------------------------- 20. what the contract cannot express


def test_a_failed_call_is_the_same_as_a_document_that_prints_nothing_known_limit() -> (
    None
):
    """`Extraction` has no "not read": a document call that fails gives a document
    with no field and no line, exactly the extraction of a document whose every page
    holds nothing. Also in the page scope: only when every page fails."""
    stub = Model({"document_type": KIND}, 500, 500, 500)
    failed = stub.scoped("document").extract(doc(3), invoice(), "travel")
    empty = both(invoice(), EMPTY, EMPTY, EMPTY)
    assert failed.model_dump() == empty.model_dump()
    assert failed.fields == [] and failed.line_items == []


def test_a_failed_document_call_adds_no_reason_to_the_routing_known_limit() -> None:
    """The routing reads only the stored fields: with no field, nothing says "low
    confidence" for this document. The claim is the same as one without the document
    (other reasons, like a missing document, may still send it to a person)."""
    failed, _ = by_document(invoice(), KIND, "not json")
    _, with_document = route_with(failed)
    claim = travel_claim(documents=travel_documents())
    _, without = route(claim, [], WORKFLOWS["travel"])
    assert with_document == without
    assert not any("low confidence" in r for r in with_document)


CANCELLATION = [
    [
        "Meridian Journeys SA",
        "Booking reference: MQ-48201",
        "Cancelled on: 2026-11-02",
        "Cancellation charge  -570.00",
    ],
    ["Refund: CHF 1,330.00"],
]
CANCEL_FIELDS: dict[str, Given] = {
    "booking_reference": ("MQ-48201", "Booking reference: MQ-48201"),
    "cancellation_date": ("2026-11-02", "Cancelled on: 2026-11-02"),
}
REFUND: Given = (1330.0, "Refund: CHF 1,330.00")


def test_with_no_total_field_nothing_carries_the_incomplete_mark_known_limit() -> None:
    """A cancellation confirmation has lines and no `total`. A line that is not on
    its page empties the list, but the mark ("the lines are incomplete") goes to a
    `total` that does not exist: every stored field keeps 0.9 and the list is simply
    empty, like a document with no table. Also in the page scope: yes."""
    data = build_pdf(CANCELLATION)
    wrong_line = {"description": "Cancellation charge", "amount": -507.0}
    first = block_of(
        DocumentType.cancellation_confirmation, [wrong_line], CANCEL_FIELDS
    )
    second = block_of(
        DocumentType.cancellation_confirmation, [], {"refund_received": REFUND}
    )
    out = both(data, first, second, kind="cancellation_confirmation")
    assert out.line_items == []
    assert {f.name for f in out.fields} == {
        "booking_reference",
        "cancellation_date",
        "refund_received",
    }
    assert {f.confidence for f in out.fields} == {VERIFIED}
    good = {"description": "Cancellation charge", "amount": -570.0}
    kept = both(
        data,
        block_of(DocumentType.cancellation_confirmation, [good], CANCEL_FIELDS),
        second,
        kind="cancellation_confirmation",
    )
    assert kept.line_items == [good]


# ----------------------------------------- other checks of the request and of the note


def test_a_scan_and_a_text_document_send_the_same_form_of_request() -> None:
    """No test of the content of the images (the stub never looks at them): both ask
    for one block per page and send one image per page."""
    for data in (invoice(), invoice(SCAN), invoice(MIXED)):
        _, stub = by_document(data, KIND, blocks(*PERFECT))
        call = stub.requests[1]
        images = [p for p in call["messages"][0]["content"] if p["type"] == "image_url"]
        assert len(images) == 3
        required = call["response_format"]["json_schema"]["schema"]["required"]
        assert required == ["page_1", "page_2", "page_3"]


# ------------------------------------------- more attacks on what a value must show


@pytest.mark.parametrize(
    ("name", "given"),
    [
        ("customer_name", ("Ortrud", "Customer: Ortrud Vanselow")),  # half a name
        (
            "customer_name",
            ("A", "Customer: Ortrud Vanselow"),
        ),  # the letter "a" is in it
        ("repair_company", ("AG", "Brennholt Sanitaer AG")),
        ("repair_company", ("Sanitaer", "Brennholt Sanitaer AG")),
    ],
)
def test_a_partial_text_value_is_stored_at_09_known_limit(
    name: str, given: Given
) -> None:
    """For a text field the value only has to be inside the snippet, and the snippet
    on the page. A person field has no label check and a company is out of scope of
    it: half of a name, or one letter of it, is stored at 0.9 with a true snippet.
    Low severity (the page and the snippet are real), but a value without a check of
    its own. Also in the page scope: yes."""
    out = both(invoice(), only(name, given), EMPTY, EMPTY)
    assert stored(out)[name] == (given[0], 1, VERIFIED)


def test_a_junk_line_with_only_a_quantity_is_stored_and_silences_t003_known_limit(
    tmp_path: Path,
) -> None:
    """A line `{"quantity": 1.0}` is confirmed: the number 1 is printed on every
    page. It is stored as a row of the table, and T003 skips a list that holds a line
    with no amount: with it, the 5-of-6 list of the omitted row passes unseen. A
    downstream limit (T003), also in the page scope: yes."""
    junk = row(None, 1.0, None, None)
    five = [L1, L2, L3, L4, L6]
    control = both(invoice(), block([L1, L2, L3]), block([L4, L6]), block(total=TOTAL))
    assert control.line_items == five and t003(control, tmp_path) == ["T003"]
    out = both(
        invoice(), block([L1, L2, L3]), block([L4, L6, junk]), block(total=TOTAL)
    )
    assert out.line_items == [*five, junk]
    assert stored(out)["total"] == (1284.5, 3, VERIFIED)
    assert t003(out, tmp_path) == []


STAMPED = frozenset({2})


def test_a_page_with_a_thin_text_layer_loses_its_values_in_silence_undocumented() -> (
    None
):
    """Page 2 is an image with a text footer ("Page 2 of 3"): it has a text layer, so
    it is a text page. What the model reads from the image is not on that layer: the
    header is refused (not stored, not marked at 0.5, as a full scan would be) and the
    rows of page 2 are not confirmed, so the list is empty and `total` is at 0.5. The
    rest of the document looks complete. Also in the page scope: yes."""
    data = build_pdf(INVOICE_PAGES, stamped=STAMPED)
    assert texts(data)[1].strip() == "Page 2 of 3"
    out = both(data, PERFECT[0], PERFECT[1], PERFECT[2])
    assert "invoice_number" in stored(out)  # from block 1
    assert stored(out)["invoice_number"][1] == 1
    assert out.line_items == []
    assert stored(out)["total"][2] == UNVERIFIED
    alone = both(data, EMPTY, block(invoice_number=HEAD), EMPTY)
    assert alone.fields == []  # lost with no mark: nothing says "page 2 has a value"


# -------------------------------------------------- a seeded random attack, by oracle

OPTIONS: dict[str, list[Given]] = {
    "invoice_number": [INV, HEAD, CUSTOMER_NO, ORDER_NO, ("n/a", "n/a"),
                       ("BS-26-0417", "Invoice nr BS-26-0417")],
    "invoice_date": [DATE, DUE_DATE, ESTIMATE_DATE, DEPOSIT_DATE, DONE_DATE],
    "repair_company": [COMPANY, SUBCONTRACTOR],
    "customer_name": [CUSTOMER, SIGNER, SITE_CONTACT, ("Not answerable", "x")],
    "total": [TOTAL, SUBTOTAL, BROUGHT_1, BROUGHT_2, CARRIED, SUBTOTAL_3, VAT, DEPOSIT,
              (1284.5, "1,284.5"), (284.5, "284.50")],
}  # fmt: skip
# the snippets whose label names another thing: never 0.9
BAD_LABEL = {
    s
    for g in (CUSTOMER_NO, ORDER_NO, DUE_DATE, ESTIMATE_DATE, DEPOSIT_DATE, DONE_DATE,
              SUBTOTAL, BROUGHT_1, BROUGHT_2, CARRIED, SUBTOTAL_3, VAT, DEPOSIT)
    for s in (g[1],)
}  # fmt: skip
JUNK_LINES = [
    row("Pipe replacement", 2.0, 45.0, 91.0),
    row(None, 1.0, None, None),
    row("Wall tiles", 10.0, 18.5, 185.0),
    row(None, None, None, None),
]


@cache
def stamped_invoice() -> bytes:
    return build_pdf(INVOICE_PAGES, stamped=STAMPED)


def numbers_printed(text: str) -> set[float]:
    found = re.findall(r"\d[\d,']*(?:\.\d+)?", text)
    return {float(n.replace(",", "").replace("'", "")) for n in found}


def printed(line: dict[str, Any], text: str) -> bool:
    """An independent check of `_on_page`: each value of the line is printed."""
    if not text.strip():
        return False
    for value in line.values():
        if value is None:
            continue
        if isinstance(value, str):
            if " ".join(value.lower().split()) not in " ".join(text.lower().split()):
                return False
        elif not any(abs(n - value) < 0.005 for n in numbers_printed(text)):
            return False
    return True


@pytest.mark.parametrize("seed", range(6))
def test_random_attacks_never_break_the_invariants(seed: int) -> None:
    """A seeded random mix of right, misplaced, mislabelled, invented and conflicting
    answers, lines that are right, wrong or junk, missing blocks and unknown keys, on
    a text, a scan, a mixed and a thin-text document. After each run: provenance
    holds; both scopes agree; a field at 0.8 or more has every block present, one
    value given for it, a text layer under its snippet and a label that fits; the
    lines are all or nothing, each printed on the page of its block."""
    rng = random.Random(20261126 + seed)
    documents = [invoice(), invoice(MIXED), invoice(SCAN), stamped_invoice()]
    for _ in range(40):
        data = rng.choice(documents)
        text = texts(data)
        answers: list[Any] = []
        given: dict[str, list[Given]] = {n: [] for n in OPTIONS}
        for _page in range(3):
            if rng.random() < 0.08:
                answers.append(None)
                continue
            lines = [
                rng.choice(SIX + JUNK_LINES) for _ in range(rng.choice([0, 0, 1, 3]))
            ]
            picked = {
                n: rng.choice(options)
                for n, options in OPTIONS.items()
                if rng.random() < 0.35
            }
            for n, g in picked.items():
                given[n].append(g)
            answers.append(block_of(TYPE, lines, picked))
        extra = rng.random() < 0.1
        if extra:
            answer = blocks(*answers)
            answer["page_9"] = EMPTY
            out, _ = by_document(data, KIND, answer)
            assert_provenance(out, data)
        else:
            out = both(data, *answers)
        complete = not extra and all(a is not None for a in answers)
        for f in out.fields:
            if f.confidence < LOW_CONFIDENCE:
                continue
            assert complete
            assert text[f.source.page - 1].strip()
            assert f.source.snippet not in BAD_LABEL
            values = {
                " ".join(str(v).lower().split())
                for v, _ in given[f.name]
                if not labels.is_placeholder(v)
            }
            assert len(values) == 1, (f.name, values)
        pairs = [
            (text[i], line)
            for i, a in enumerate(answers)
            if a is not None
            for line in a["values"]["line_items"]
            if any(v is not None for v in line.values())
        ]
        every = all(printed(line, page) for page, line in pairs)
        if out.line_items:
            assert complete and every
            assert out.line_items == [line for _, line in pairs]
        elif pairs and "total" in stored(out):
            assert stored(out)["total"][2] < LOW_CONFIDENCE


def test_a_snippet_that_spans_the_page_break_is_refused_in_both_blocks() -> None:
    """The end of page 1 and the top of page 2 are not one text: a snippet made of
    both is printed on no page. The label check never sees a joined text either."""
    spanning = ("BS-26-0417", "Page 1 of 3\nInvoice BS-26-0417")
    assert omni._shown(spanning[1], "\n".join(texts(invoice())))  # joined: it is there
    for index in (0, 1):
        answers = [EMPTY, EMPTY, EMPTY]
        answers[index] = only("invoice_number", spanning)
        assert both(invoice(), *answers).fields == []


def test_a_conflict_the_model_leaves_out_is_not_seen_document_only_known_limit() -> (
    None
):
    """Page 1 prints a subtotal of 540.00 that a model reading page 1 alone would
    give as the total, and the reader would then see the conflict with page 3 (the
    case of `test_two_values_in_two_blocks_keep_the_first_at_half`). In the document
    call the model sees the real total on page 3 and leaves the subtotal out: the
    reader sees one value and stores it at 0.9. The two answers below give the same
    `Extraction` in both scopes; only the document call lets the model make that
    choice, so this is a loss of the document scope. The note says so (section 5)."""
    seen = both(invoice(), only("total", SUBTOTAL), EMPTY, only("total", TOTAL))
    assert stored(seen)["total"] == (540.0, 1, UNVERIFIED)
    left_out = both(invoice(), EMPTY, EMPTY, only("total", TOTAL))
    assert stored(left_out)["total"] == (1284.5, 3, VERIFIED)
