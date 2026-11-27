"""OmniReader (issue #18) with a stubbed HTTP client: no key, no network.

The stub answers the requests in order (classify first, then one answer per page).
The mock documents are rendered for real by pypdfium2.
"""

import base64
import json
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pypdfium2 as pdfium  # type: ignore[import-untyped]
import pytest
from PIL import Image
from pypdf import PdfReader, PdfWriter

from app.docintel import omni
from app.docintel.omni import UNVERIFIED, VERIFIED, OmniReader, _shown, pages
from app.docintel.reader import get_reader
from app.docintel.schemas import SCHEMAS
from app.fraud.routing import LOW_CONFIDENCE
from app.models import Document, DocumentType, Extraction
from tests.reader_contract import MOCK, check_reader_contract

URL = "https://vlm.test/v1"
BOOKING = MOCK / "claim-1-travel" / "booking-confirmation.pdf"
CANCELLATION = MOCK / "claim-1-travel" / "cancellation-confirmation.pdf"


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVIDIA_KEY", "test-key-value")
    monkeypatch.setenv("VLM_BASE_URL", URL)
    monkeypatch.setenv("VLM_MODEL", "omni-test")


class Stub:
    """Answers each request with the next reply: a dict (a 200 chat answer with
    this JSON as content), a str (raw content), an int (that status), or a
    callable that raises (a timeout)."""

    def __init__(self, *replies: Any) -> None:
        self.replies: Iterator[Any] = iter(replies)
        self.requests: list[dict[str, Any]] = []
        self.headers: list[httpx.Headers] = []
        self.waits: list[float] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        self.headers.append(request.headers)
        assert str(request.url) == f"{URL}/chat/completions"
        reply = next(self.replies)
        if callable(reply):
            reply(request)
        if isinstance(reply, tuple):
            status, headers = reply
            return httpx.Response(status, headers=headers, text="{}")
        if isinstance(reply, int):
            return httpx.Response(reply, text='{"message": "error"}')
        content = reply if isinstance(reply, str) else json.dumps(reply)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}}]}
        )

    def reader(self) -> OmniReader:
        client = httpx.Client(transport=httpx.MockTransport(self.handle))
        return OmniReader(client=client, sleep=self.waits.append)


def doc(pages: int = 1) -> Document:
    return Document(id="D-omni", file="x", pages=pages)


def test_claims_reader_omni_picks_the_omni_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLAIMS_READER", "omni")
    reader = get_reader()
    assert isinstance(reader, OmniReader) and reader.name == "omni"


@pytest.mark.parametrize("name", ["NVIDIA_KEY", "VLM_BASE_URL", "VLM_MODEL"])
def test_a_missing_setting_stops_the_startup(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.delenv(name)
    with pytest.raises(RuntimeError, match=name) as error:
        OmniReader()
    assert "test-key-value" not in str(error.value)


@pytest.mark.parametrize(
    "junk", [b"", b"not a document", b"%PDF-1.4 broken", bytes(range(256))]
)
def test_junk_gives_unknown_with_no_request(junk: bytes) -> None:
    stub = Stub()
    reader = stub.reader()
    assert reader.classify(doc(), junk) == "unknown"
    out = reader.extract(doc(), junk, "travel")
    assert out.document_type == "unknown" and out.fields == [] and out.line_items == []
    assert stub.requests == []


def image_of(request: dict[str, Any]) -> tuple[str, bytes]:
    """The mime type and the bytes of the one image of a request."""
    [message] = request["messages"]
    [text, image] = message["content"]
    assert text["type"] == "text" and image["type"] == "image_url"
    head, encoded = image["image_url"]["url"].split(",", 1)
    return head.removeprefix("data:").removesuffix(";base64"), base64.b64decode(encoded)


def test_classify_sends_the_full_page_to_the_configured_model() -> None:
    stub = Stub({"document_type": "booking_confirmation"})
    data = BOOKING.read_bytes()

    assert stub.reader().classify(doc(), data) == "booking_confirmation"

    [request] = stub.requests
    assert request["model"] == "omni-test"
    assert request["chat_template_kwargs"] == {"enable_thinking": False}
    mime, png = image_of(request)
    assert mime == "image/png"
    with Image.open(BytesIO(png)) as sent:  # the whole A4 page at 150 DPI, not a crop
        assert sent.size == pages(data)[0].size
        assert abs(sent.size[0] - 1240) <= 1 and abs(sent.size[1] - 1754) <= 1
    assert stub.headers[0]["authorization"] == "Bearer test-key-value"


def test_a_photo_is_sent_as_it_is() -> None:
    jpg = MOCK / "hard" / "kit-anna-luggage-receipt-jacket-shoes-photo.jpg"
    stub = Stub({"document_type": "receipt"})
    assert stub.reader().classify(doc(), jpg.read_bytes()) == "receipt"
    assert image_of(stub.requests[0]) == ("image/jpeg", jpg.read_bytes())


@pytest.mark.parametrize(
    "reply",
    [{"document_type": "invoice"}, {"type": "receipt"}, "not json", [1], 404, 422],
)
def test_classify_gives_unknown_on_a_bad_answer(reply: Any) -> None:
    assert Stub(reply).reader().classify(doc(), BOOKING.read_bytes()) == "unknown"


def test_classify_asks_once_per_document_and_not_after_a_failure() -> None:
    stub = Stub(400, {"document_type": "booking_confirmation"})
    reader, data = stub.reader(), BOOKING.read_bytes()
    assert reader.classify(doc(), data) == "unknown"  # a failure is not kept
    assert reader.classify(doc(), data) == "booking_confirmation"
    assert reader.classify(doc(), data) == "booking_confirmation"
    assert len(stub.requests) == 2


def timeout(request: httpx.Request) -> None:
    raise httpx.ReadTimeout("slow", request=request)


ANSWER = {"document_type": "booking_confirmation"}


@pytest.mark.parametrize(
    ("replies", "waits"),
    [
        (((429, {"Retry-After": "3"}), ANSWER), [3.0]),
        ((429, ANSWER), [2.0]),
        ((503, 502, ANSWER), [2.0, 8.0]),
        ((timeout, ANSWER), [2.0]),
        (((429, {"Retry-After": "999"}), ANSWER), [30.0]),  # never an endless wait
    ],
)
def test_a_429_a_5xx_or_a_timeout_is_retried_with_a_bounded_wait(
    replies: tuple[Any, ...], waits: list[float]
) -> None:
    stub = Stub(*replies)
    assert stub.reader().classify(doc(), BOOKING.read_bytes()) == "booking_confirmation"
    assert stub.waits == waits and len(stub.requests) == len(replies)


@pytest.mark.parametrize("replies", [(429, 429, 429), (timeout,) * 3, (500,) * 3])
def test_the_retries_end_and_the_reader_does_not_raise(
    replies: tuple[Any, ...], caplog: pytest.LogCaptureFixture
) -> None:
    stub = Stub(*replies)
    assert stub.reader().classify(doc(), BOOKING.read_bytes()) == "unknown"
    assert len(stub.requests) == 3 and len(stub.waits) == 2
    assert "omni call failed" in caplog.text and "test-key-value" not in caplog.text


@pytest.mark.parametrize("status", [400, 401, 404, 422])
def test_a_client_error_is_not_retried(status: int) -> None:
    stub = Stub(status)
    assert stub.reader().classify(doc(), BOOKING.read_bytes()) == "unknown"
    assert len(stub.requests) == 1 and stub.waits == []


def answer_from(path: Path, **values: Any) -> dict[str, Any]:
    """The perfect answer for a mock document (its expected.json), with changes."""
    expected = json.loads(path.with_suffix(".expected.json").read_text())
    answer: dict[str, Any] = {
        "values": {f["name"]: f["value"] for f in expected["fields"]},
        "snippets": {f["name"]: f["snippet"] for f in expected["fields"]},
    }
    if "line_items" in SCHEMAS[DocumentType(expected["document_type"])].model_fields:
        answer["values"]["line_items"] = expected.get("line_items", [])
    for name, value in values.items():
        answer["values"][name] = value
    return answer


def read(path: Path, kind: str, *answers: Any) -> tuple[Extraction, Stub]:
    stub = Stub({"document_type": kind}, *answers)
    return stub.reader().extract(doc(), path.read_bytes(), "travel"), stub


def test_a_native_pdf_gives_its_fields_with_page_and_verified_snippet() -> None:
    out, stub = read(BOOKING, "booking_confirmation", answer_from(BOOKING))

    assert out.document_type == "booking_confirmation"
    by_name = {f.name: f for f in out.fields}
    assert set(by_name) == {
        "booking_reference",
        "traveller_name",
        "departure_date",
        "trip_price",
    }
    assert by_name["departure_date"].value == "2026-08-10"  # JSON form, as the fixture
    assert by_name["trip_price"].value == 1840.0
    for f in out.fields:
        assert f.source.document_id == "D-omni" and f.source.page == 1
        assert f.source.snippet and f.source.box is None
        assert f.confidence == VERIFIED
    assert by_name["trip_price"].source.snippet == "CHF 1,840.00"
    assert [i["amount"] for i in out.line_items] == [840.0, 900.0, 100.0]
    assert len(stub.requests) == 2  # classify, then the one page
    prompt = stub.requests[1]["messages"][0]["content"][0]["text"]
    assert "trip_price" in prompt and "snippets" in prompt


def test_a_null_value_is_not_stored() -> None:
    out, _ = read(
        BOOKING, "booking_confirmation", answer_from(BOOKING, trip_price=None)
    )
    assert "trip_price" not in {f.name for f in out.fields}
    assert len(out.fields) == 3


def test_the_type_of_classify_chooses_the_schema() -> None:
    cert = MOCK / "claim-1-travel" / "medical-certificate.pdf"
    out, stub = read(cert, "medical_certificate", answer_from(cert))
    assert {f.name for f in out.fields} == {"patient_name", "cause", "unfit_from"}
    assert out.line_items == []
    prompt = stub.requests[1]["messages"][0]["content"][0]["text"]
    assert "unfit_from" in prompt and "trip_price" not in prompt


@pytest.mark.parametrize("kind", ["unknown", "damage_photo"])
def test_a_type_with_nothing_to_read_gives_no_field_and_no_second_call(
    kind: str,
) -> None:
    out, stub = read(BOOKING, kind)
    assert out.document_type == kind and out.fields == [] and out.line_items == []
    assert len(stub.requests) == 1


def names(out: Extraction) -> set[str]:
    return {f.name for f in out.fields}


ALL_FOUR = {"booking_reference", "traveller_name", "departure_date", "trip_price"}


@pytest.mark.parametrize(
    ("field", "value", "snippet"),
    [
        ("booking_reference", "WPR-999999", "WPR-999999"),  # not on the page
        ("trip_price", 1840.0, "Anna Keller"),  # on the page, but not the value
        ("trip_price", 1840.0, None),  # no snippet
        ("trip_price", 1840.0, 1840),  # not a text
        ("trip_price", 1840.0, ""),
        ("departure_date", "2026-08-11", "2026-08-10"),
    ],
)
def test_a_value_with_no_true_source_is_not_stored(
    field: str, value: Any, snippet: Any
) -> None:
    answer = answer_from(BOOKING, **{field: value})
    answer["snippets"][field] = snippet
    out, _ = read(BOOKING, "booking_confirmation", answer)
    assert names(out) == ALL_FOUR - {field}


def test_a_whole_page_as_snippet_proves_nothing() -> None:
    answer = answer_from(BOOKING)
    answer["snippets"]["trip_price"] = pages(BOOKING.read_bytes())[0].text
    out, _ = read(BOOKING, "booking_confirmation", answer)
    assert names(out) == ALL_FOUR - {"trip_price"}


def test_a_mixed_up_field_with_a_true_snippet_is_stored_unverified() -> None:
    """840.00 is on the page (a line item), so this wrong trip price passes the
    checks of the snippet. The snippet proves that the text is there, not that it is
    the right field (D80): no label says "trip price" in front of it (#125), so the
    value and its source stay and a person checks it."""
    answer = answer_from(BOOKING, trip_price=840.0)
    answer["snippets"]["trip_price"] = "840.00"
    out, _ = read(BOOKING, "booking_confirmation", answer)
    price = next(f for f in out.fields if f.name == "trip_price")
    assert price.value == 840.0 and price.confidence == UNVERIFIED
    assert price.source.snippet == "840.00" and price.source.page == 1
    assert price.confidence < LOW_CONFIDENCE
    others = {f.confidence for f in out.fields if f.name != "trip_price"}
    assert others == {VERIFIED}


def test_a_field_with_the_label_of_another_field_keeps_value_and_source() -> None:
    """The customer number K-30418 is on the page, but it is not a booking reference."""
    answer = answer_from(BOOKING, booking_reference="K-30418")
    answer["snippets"]["booking_reference"] = "K-30418"
    out, _ = read(BOOKING, "booking_confirmation", answer)
    ref = next(f for f in out.fields if f.name == "booking_reference")
    assert ref.value == "K-30418" and ref.confidence == UNVERIFIED
    assert (ref.source.document_id, ref.source.page) == ("D-omni", 1)
    assert ref.source.snippet == "K-30418" and ref.source.box is None
    assert names(out) == ALL_FOUR


def test_a_field_whose_label_fits_stays_verified() -> None:
    answer = answer_from(BOOKING)
    answer["snippets"]["booking_reference"] = "Booking reference:\nWPR-482913"
    answer["snippets"]["departure_date"] = "Departure:\n2026-08-10"
    out, _ = read(BOOKING, "booking_confirmation", answer)
    assert names(out) == ALL_FOUR
    assert {f.confidence for f in out.fields} == {VERIFIED}


@pytest.mark.parametrize(
    "placeholder", ["Not answerable", "null", "N/A", "-", "unknown"]
)
def test_a_placeholder_is_no_value_and_no_disagreement(placeholder: str) -> None:
    """Page 1 says "Not answerable", page 2 gives the name: the field is the name,
    verified, with no conflict between the two pages."""
    first = nothing_on(DocumentType.booking_confirmation)
    first["values"]["traveller_name"] = placeholder
    first["snippets"]["traveller_name"] = placeholder
    second = nothing_on(DocumentType.booking_confirmation)
    second["values"]["traveller_name"] = "Anna Keller"
    second["snippets"]["traveller_name"] = "Anna Keller"
    stub = Stub(ANSWER, first, second)
    out = stub.reader().extract(doc(2), two_pages(), "travel")
    [name] = out.fields
    assert (name.name, name.value, name.source.page) == (
        "traveller_name",
        "Anna Keller",
        2,
    )
    assert name.confidence == VERIFIED


@pytest.mark.parametrize(
    ("snippet", "text", "shown"),
    [
        ("250.00", "Total CHF 1,250.00", False),  # a cut number
        ("40.00", "Total CHF 1,840.00", False),
        ("250.00", "Total CHF 1'250.00", False),
        ("1,500.00", "Fees -1,500.00", False),  # the sign belongs to the number
        ("1,500.00", "Fees −1,500.00", False),
        ("250", "Total 250.00", False),  # a cut decimal
        ("250.0", "Total 250.00", False),
        ("1,250.00", "Total CHF 1,250.00", True),
        ("CHF 1,250.00", "Total CHF 1,250.00.", True),  # the full stop of a sentence
        ("1'250.00", "Total CHF 1'250.00", True),
        ("-1,500.00", "Fees -1,500.00", True),
        ("250.00", "Fee 250.00, total CHF 1,250.00", True),  # also there whole
        ("2026-08-04", "Date: 2026-08-04", True),
        ("WPR-482913", "Booking reference: WPR-482913", True),  # not a sign
        ("anna  KELLER", "Traveller: Anna Keller", True),
    ],
)
def test_a_snippet_is_on_the_page_only_with_whole_numbers(
    snippet: str, text: str, shown: bool
) -> None:
    assert _shown(snippet, text) is shown


@pytest.mark.parametrize(
    ("path", "kind", "field", "value", "snippet"),
    [
        (BOOKING, "booking_confirmation", "trip_price", 40.0, "40.00"),  # 1,840.00
        (
            CANCELLATION,
            "cancellation_confirmation",
            "refund_received",
            1500.0,
            "1,500.00",
        ),
    ],
)
def test_a_value_read_from_part_of_a_number_is_not_stored(
    path: Path, kind: str, field: str, value: float, snippet: str
) -> None:
    answer = answer_from(path, **{field: value})
    answer["snippets"][field] = snippet
    out, _ = read(path, kind, answer)
    assert field not in names(out)
    assert len(out.fields) == len(answer["snippets"]) - 1  # the others stay


@pytest.mark.parametrize(
    ("path", "kind", "field", "value"),
    [
        (BOOKING, "booking_confirmation", "trip_price", 1840.0),
        (CANCELLATION, "cancellation_confirmation", "refund_received", 340.0),
    ],
)
def test_a_whole_amount_is_still_stored(
    path: Path, kind: str, field: str, value: float
) -> None:
    out, _ = read(path, kind, answer_from(path))
    stored = next(f for f in out.fields if f.name == field)
    assert stored.value == value and stored.confidence == VERIFIED


@pytest.mark.parametrize(
    ("change", "lost"),
    [
        ({"trip_price": "1840"}, {"trip_price"}),  # never converted to a number
        ({"trip_price": "CHF 1,840.00"}, {"trip_price"}),
        ({"departure_date": "10.08.2026"}, {"departure_date"}),
        ({"departure_date": 20260810}, {"departure_date"}),
        ({"traveller_name": "   "}, {"traveller_name"}),
        ({"price": 1840.0}, set()),  # an unknown key is dropped, not mapped
        (
            {"booking_reference": None, "booking_ref": "WPR-482913"},
            {"booking_reference"},
        ),
    ],
)
def test_an_invalid_value_is_dropped_and_the_others_kept(
    change: dict[str, Any], lost: set[str]
) -> None:
    answer = answer_from(BOOKING, **change)
    answer["snippets"].update({k: "x" for k in change if k not in ALL_FOUR})
    out, _ = read(BOOKING, "booking_confirmation", answer)
    assert names(out) == ALL_FOUR - lost
    assert {f.name for f in out.fields} <= ALL_FOUR


def test_an_absent_key_counts_as_not_found() -> None:
    answer = answer_from(BOOKING)
    del answer["values"]["traveller_name"]
    out, _ = read(BOOKING, "booking_confirmation", answer)
    assert names(out) == ALL_FOUR - {"traveller_name"}


def test_one_json_fence_around_the_answer_is_accepted() -> None:
    fenced = "```json\n" + json.dumps(answer_from(BOOKING)) + "\n```"
    out, _ = read(BOOKING, "booking_confirmation", fenced)
    assert names(out) == ALL_FOUR


@pytest.mark.parametrize(
    "reply",
    [
        "not json",
        json.dumps(answer_from(BOOKING))[:-20],  # cut off
        "Here is the JSON: " + json.dumps(answer_from(BOOKING)),  # text around it
        {"values": answer_from(BOOKING)["values"]},  # no snippets
        {"values": [1, 2], "snippets": {}},
        {"values": {"trip_price": 1840.0}, "snippets": []},
        [answer_from(BOOKING)],
        "",
        404,
    ],
)
def test_an_answer_that_cannot_be_trusted_gives_no_field(reply: Any) -> None:
    out, _ = read(BOOKING, "booking_confirmation", reply)
    assert out.document_type == "booking_confirmation"
    assert out.fields == [] and out.line_items == []


INVOICE = MOCK / "claim-2-property" / "repair-invoice.pdf"
INVOICE_FIELDS = {
    "invoice_number",
    "invoice_date",
    "repair_company",
    "total",
    "customer_name",
}


def invoice(lines: Any) -> Extraction:
    answer = answer_from(INVOICE)
    answer["values"]["line_items"] = lines
    return read(INVOICE, "repair_invoice", answer)[0]


def confidence(out: Extraction, name: str) -> float:
    return next(f.confidence for f in out.fields if f.name == name)


def expected_lines() -> list[dict[str, Any]]:
    path = INVOICE.with_suffix(".expected.json")
    lines: list[dict[str, Any]] = json.loads(path.read_text())["line_items"]
    return lines


def test_line_items_that_are_all_on_the_page_are_stored() -> None:
    out = invoice(expected_lines())
    assert out.line_items == expected_lines()
    assert names(out) == INVOICE_FIELDS and confidence(out, "total") == VERIFIED


def test_lines_on_the_page_are_stored_even_when_they_do_not_add_up() -> None:
    """The reader does not judge the sum: T003 does (a real discrepancy must reach
    it). 720.00 is on the page, so this line is confirmed."""
    lines = expected_lines()
    lines[0] = {**lines[0], "unit_price": 720.0, "amount": 720.0}
    out = invoice(lines)
    assert out.line_items == lines
    assert sum(i["amount"] for i in out.line_items) != 3200.0
    assert confidence(out, "total") == VERIFIED


@pytest.mark.parametrize(
    "change",
    [
        {"amount": 400.0},  # not on the page: one line of four is unconfirmed
        {"description": "Gold-plated taps"},
        {"amount": "380"},  # invalid in the schema
        {"colour": "red"},  # unknown key
    ],
)
def test_one_unconfirmed_line_empties_the_list_and_marks_the_total(
    change: dict[str, Any],
) -> None:
    """An incomplete list would look complete to T003 and the settlement: the
    reader stores none and puts the total below the routing threshold (D80)."""
    lines = expected_lines()
    lines[1] = {**lines[1], **change}
    out = invoice(lines)
    assert out.line_items == []
    assert names(out) == INVOICE_FIELDS
    assert confidence(out, "total") == UNVERIFIED
    assert confidence(out, "invoice_number") == VERIFIED


@pytest.mark.parametrize("lines", ["four lines", None, {"amount": 380.0}])
def test_line_items_in_a_wrong_form_empty_the_list_and_mark_the_total(
    lines: Any,
) -> None:
    out = invoice(lines)
    assert out.line_items == [] and confidence(out, "total") == UNVERIFIED


def test_a_line_with_only_nulls_is_dropped() -> None:
    empty = {"description": None, "quantity": None, "unit_price": None, "amount": None}
    out = invoice([*expected_lines(), empty])
    assert out.line_items == expected_lines()
    assert confidence(out, "total") == VERIFIED


def test_no_line_read_is_an_empty_list_with_no_mark() -> None:
    """[] says "no line read" (D77). A line that the model skips is not seen: the
    reader cannot know it (D80, known limit)."""
    out = invoice([])
    assert out.line_items == [] and confidence(out, "total") == VERIFIED


def test_with_no_total_an_incomplete_list_leaves_no_mark_known_limit() -> None:
    """Nothing in the Extraction can say "lines incomplete" when there is no
    total to mark (D80): the list is empty and the fields keep their confidence."""
    lines = expected_lines()
    lines[1] = {**lines[1], "amount": 400.0}
    answer = answer_from(INVOICE, total=None)
    answer["values"]["line_items"] = lines
    out, _ = read(INVOICE, "repair_invoice", answer)
    assert out.line_items == []
    assert "total" not in names(out)
    assert {f.confidence for f in out.fields} == {VERIFIED}


def two_pages() -> bytes:
    """Page 1: the booking. Page 2: the cancellation (it also names Anna Keller)."""
    writer = PdfWriter()
    for path in (BOOKING, CANCELLATION):
        writer.append(PdfReader(path))
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def test_each_page_is_one_request_and_a_field_keeps_its_own_page() -> None:
    first = answer_from(BOOKING, traveller_name=None)
    second = {
        "values": {
            "booking_reference": "WPR-482913",  # page 1 already gave it
            "traveller_name": "Anna Keller",
            "departure_date": None,
            "trip_price": None,
            "line_items": [],
        },
        "snippets": {
            "booking_reference": "WPR-482913",
            "traveller_name": "Anna Keller",
        },
    }
    stub = Stub({"document_type": "booking_confirmation"}, first, second)
    out = stub.reader().extract(doc(2), two_pages(), "travel")

    pages_of = {f.name: f.source.page for f in out.fields}
    assert pages_of == {
        "booking_reference": 1,  # the first page that gives a value wins
        "departure_date": 1,
        "trip_price": 1,
        "traveller_name": 2,
    }
    assert len(out.line_items) == 3
    assert len(stub.requests) == 3
    assert image_of(stub.requests[1]) != image_of(stub.requests[2])
    assert "page 2 of 2" in stub.requests[2]["messages"][0]["content"][0]["text"]


def test_a_page_that_fails_keeps_the_fields_of_the_others_for_a_person() -> None:
    """Page 2 was not read: it may hold a value that changes the claim (a refund).
    The fields of page 1 stay, with their source, but a person checks them."""
    stub = Stub(ANSWER, answer_from(BOOKING), 500, 500, 500)
    out = stub.reader().extract(doc(2), two_pages(), "travel")
    assert names(out) == ALL_FOUR
    assert {f.source.page for f in out.fields} == {1}
    assert all(f.confidence < LOW_CONFIDENCE for f in out.fields)
    assert out.line_items == []  # page 2 may hold lines that nobody read


def nothing_on(kind: DocumentType) -> dict[str, Any]:
    """The answer for a page that shows none of the values of the type."""
    values: dict[str, Any] = {
        n: [] if n == "line_items" else None for n in SCHEMAS[kind].model_fields
    }
    return {"values": values, "snippets": {}}


def test_two_pages_that_give_two_values_send_the_field_to_a_person() -> None:
    """Page 1 gives the trip price 1,840.00; page 2 gives 340.00 (both on their
    page). The first value stays with its source, but it is not final."""
    second = nothing_on(DocumentType.booking_confirmation)
    second["values"]["trip_price"] = 340.0
    second["snippets"]["trip_price"] = "340.00"
    stub = Stub(ANSWER, answer_from(BOOKING), second)
    out = stub.reader().extract(doc(2), two_pages(), "travel")
    by_name = {f.name: f for f in out.fields}
    price = by_name.pop("trip_price")
    assert price.value == 1840.0 and price.source.page == 1
    assert price.confidence < LOW_CONFIDENCE
    assert {f.confidence for f in by_name.values()} == {VERIFIED}


def test_the_same_value_on_two_pages_is_no_conflict() -> None:
    second = nothing_on(DocumentType.booking_confirmation)
    second["values"]["traveller_name"] = "ANNA KELLER"  # case and spaces differ
    second["snippets"]["traveller_name"] = "Anna Keller"
    stub = Stub(ANSWER, answer_from(BOOKING), second)
    out = stub.reader().extract(doc(2), two_pages(), "travel")
    assert {f.confidence for f in out.fields} == {VERIFIED}


def test_a_document_with_no_page_read_gives_no_field_known_limit() -> None:
    """Every page failed: no field is stored, so no field can carry the mark. The
    Reader contract cannot say "not read" (D80): the base sees a document with
    no fields, and a check that needs one of them skips."""
    stub = Stub(ANSWER, 500, 500, 500)
    out = stub.reader().extract(doc(), BOOKING.read_bytes(), "travel")
    assert out.document_type == "booking_confirmation" and out.fields == []


SCAN = MOCK / "hard" / "claim-1-travel-booking-confirmation-scan.pdf"
PHOTO = MOCK / "hard" / "claim-2-property-repair-invoice-photo.jpg"


def test_a_scan_gives_the_whole_page_as_source_and_a_person_checks_it() -> None:
    out, _ = read(SCAN, "booking_confirmation", answer_from(SCAN))
    size = pages(SCAN.read_bytes())[0].size
    assert names(out) == ALL_FOUR
    for f in out.fields:
        assert f.source.snippet is None  # the model's text is not shown as evidence
        assert f.source.box == (0.0, 0.0, float(size[0]), float(size[1]))
        assert f.confidence == UNVERIFIED
    assert out.line_items == []  # nothing on the page can confirm a line


def test_a_scan_still_refuses_an_answer_that_contradicts_itself() -> None:
    answer = answer_from(SCAN, trip_price=1480.0)  # the snippet says 1,840.00
    out, _ = read(SCAN, "booking_confirmation", answer)
    assert names(out) == ALL_FOUR - {"trip_price"}


def test_a_photo_gives_the_whole_photo_as_source() -> None:
    out, stub = read(PHOTO, "repair_invoice", answer_from(PHOTO))
    with Image.open(PHOTO) as photo:
        size = photo.size
    assert image_of(stub.requests[1])[0] == "image/jpeg"
    assert names(out) == INVOICE_FIELDS
    assert {f.source.box for f in out.fields} == {(0.0, 0.0, *map(float, size))}
    assert {f.confidence for f in out.fields} == {UNVERIFIED}
    assert out.line_items == []


def test_a_damage_photo_gives_its_type_and_no_description_yet() -> None:
    photo = MOCK / "claim-2-property" / "damage-photo-1.jpg"
    out, stub = read(photo, "damage_photo")
    assert out.document_type == "damage_photo" and out.fields == []
    assert len(stub.requests) == 1  # issue #25 reads the damage


def copies(n: int) -> bytes:
    """A PDF of n pages: the booking n times."""
    writer = PdfWriter()
    for _ in range(n):
        writer.append(PdfReader(BOOKING))
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


@pytest.fixture
def rendered(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Counts the pages that pdfium renders."""
    count: list[int] = []
    render = pdfium.PdfPage.render

    def counted(self: Any, **kwargs: Any) -> Any:
        count.append(1)
        return render(self, **kwargs)

    monkeypatch.setattr(pdfium.PdfPage, "render", counted)
    return count


def test_classify_renders_only_page_1(rendered: list[int]) -> None:
    assert Stub(ANSWER).reader().classify(doc(3), copies(3)) == "booking_confirmation"
    assert len(rendered) == 1


def test_a_pdf_longer_than_the_limit_is_read_in_part_and_given_to_a_person(
    rendered: list[int],
) -> None:
    """Only the first MAX_PAGES pages are rendered and sent. What the others hold
    is unknown, so no field of the document is final and no line list is given."""
    limit = omni.MAX_PAGES
    empty = nothing_on(DocumentType.booking_confirmation)
    stub = Stub(ANSWER, answer_from(BOOKING), *[empty] * (limit - 1))
    out = stub.reader().extract(doc(limit + 2), copies(limit + 2), "travel")

    assert len(stub.requests) == 1 + limit  # bounded, whatever the length
    assert len(rendered) <= 1 + limit
    prompt = stub.requests[2]["messages"][0]["content"][0]["text"]
    assert f"page 2 of {limit + 2}" in prompt
    assert names(out) == ALL_FOUR
    assert all(f.confidence < LOW_CONFIDENCE for f in out.fields)
    assert out.line_items == []


def test_a_huge_page_is_sent_whole_and_scaled_down_to_the_pixel_limit() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=5000, height=2500)  # 54 MP at 150 DPI
    data = BytesIO()
    writer.write(data)
    [page] = pages(data.getvalue())
    width, height = page.size
    assert width * height <= omni.MAX_PIXELS
    assert abs(width / height - 2) < 0.01  # the whole page, not a crop


def test_a_huge_photo_is_sent_whole_and_scaled_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(omni, "MAX_PIXELS", 100_000)  # the photo has 1.92 MP
    [page] = pages(PHOTO.read_bytes())
    width, height = page.size
    assert width * height <= 100_000 and abs(width / height - 0.75) < 0.01
    with Image.open(BytesIO(page.image)) as sent:
        assert sent.size == page.size and sent.format == "JPEG"
    assert page.mime == "image/jpeg"


def answer_everything(request: httpx.Request) -> httpx.Response:
    """Any type, and values with a snippet that is on no page: no field passes."""
    prompt = json.loads(request.content)["messages"][0]["content"][0]["text"]
    if prompt.startswith("What kind"):
        content: Any = {"document_type": "receipt"}
    else:
        content = {
            "values": {"merchant": "Nowhere AG", "line_items": []},
            "snippets": {"merchant": "Nowhere AG"},
        }
    body = {"choices": [{"message": {"content": json.dumps(content)}}]}
    return httpx.Response(200, json=body)


def test_the_reader_contract_with_a_stub() -> None:
    client = httpx.Client(transport=httpx.MockTransport(answer_everything))
    check_reader_contract(OmniReader(client=client, sleep=lambda _: None))


def test_the_log_holds_no_value_no_snippet_and_no_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("DEBUG")
    read(BOOKING, "booking_confirmation", answer_from(BOOKING))
    read(BOOKING, "booking_confirmation", 500, 500, 500)
    assert "omni call failed" in caplog.text
    for secret in ("test-key-value", "Anna Keller", "WPR-482913", "1,840"):
        assert secret not in caplog.text


def refs(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        if "$ref" in node:
            yield node["$ref"]
        for v in node.values():
            yield from refs(v)
    elif isinstance(node, list):
        for v in node:
            yield from refs(v)


def test_each_request_asks_for_json_that_follows_a_schema() -> None:
    """The hosted endpoint accepts response_format json_schema (spike, D80): the
    form of the answer is forced, not only asked."""
    _, stub = read(INVOICE, "repair_invoice", answer_from(INVOICE))
    classify, page = (r["response_format"] for r in stub.requests)
    assert classify["type"] == page["type"] == "json_schema"
    kinds = classify["json_schema"]["schema"]["properties"]["document_type"]["enum"]
    assert set(kinds) == {t.value for t in DocumentType}
    schema = page["json_schema"]["schema"]
    assert schema["required"] == ["values", "snippets"]
    assert "$defs" not in schema["properties"]["values"]  # moved to the root
    for ref in refs(schema):  # every reference resolves from the root
        assert ref.removeprefix("#/$defs/") in schema["$defs"]
    assert list(refs(schema))  # the line items are a reference


def test_a_snippet_for_the_line_items_is_not_used() -> None:
    """The model may give a snippet for `line_items` (spike): lines are confirmed
    one by one on the page text, never by a snippet."""
    answer = answer_from(BOOKING)
    answer["snippets"]["line_items"] = "a text that is on no page"
    out, _ = read(BOOKING, "booking_confirmation", answer)
    assert names(out) == ALL_FOUR and len(out.line_items) == 3
