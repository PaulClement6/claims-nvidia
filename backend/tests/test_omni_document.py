"""OmniReader, one call per document (issue #126), with a stubbed HTTP client: no key,
no network.

`scope="document"` sends every page image of a document in one request. The answer
holds one block per page (`page_1`, `page_2`, ...), each in the form of a page
answer, and the merge of the page mode reads the blocks. The page mode stays the
default: with it, and for a document of one page, the requests are those of #18.

More tests of the request and of the parser: `test_omni_document_request.py`. The
adversarial cases: `test_omni_document_safety.py`.
"""

import base64
import json
from io import BytesIO
from typing import Any

import httpx2 as httpx
import pytest
from pypdf import PdfReader, PdfWriter

from app.docintel import evaluate as ev
from app.docintel import omni
from app.docintel.omni import UNVERIFIED, VERIFIED, OmniReader, OmniSettings
from app.docintel.reader import get_reader
from app.docintel.schemas import SCHEMAS
from app.fraud.routing import LOW_CONFIDENCE
from app.models import DocumentType, Extraction
from tests.test_omni_reader import (
    ALL_FOUR,
    ANSWER,
    BOOKING,
    SCAN,
    URL,
    Stub,
    answer_from,
    copies,
    doc,
    image_of,
    names,
    nothing_on,
    two_pages,
)
from tests.test_omni_settings import CLASSIFY_SHA, PAGE_SHA, sha, without_image

BOOKING_TYPE = DocumentType.booking_confirmation
ORDER = [
    "model",
    "messages",
    "temperature",
    "max_tokens",
    "chat_template_kwargs",
    "response_format",
]


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVIDIA_KEY", "test-key-value")
    monkeypatch.setenv("VLM_BASE_URL", URL)
    monkeypatch.setenv("VLM_MODEL", "omni-test")


class Model(Stub):
    """The stub of `test_omni_reader`, which also takes a ready `httpx.Response`
    (an answer with a finish reason)."""

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        self.headers.append(request.headers)
        assert str(request.url) == f"{URL}/chat/completions"
        reply = next(self.replies)
        if isinstance(reply, httpx.Response):
            return reply
        if isinstance(reply, int):
            return httpx.Response(reply, text='{"message": "error"}')
        content = reply if isinstance(reply, str) else json.dumps(reply)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}}]}
        )

    def scoped(self, scope: str) -> OmniReader:
        client = httpx.Client(transport=httpx.MockTransport(self.handle))
        return OmniReader(
            client=client, sleep=self.waits.append, settings=OmniSettings(scope=scope)
        )


def finished(content: Any, reason: str) -> httpx.Response:
    """A 200 answer with this content and this finish reason."""
    text = content if isinstance(content, str) else json.dumps(content)
    choice = {"message": {"content": text}, "finish_reason": reason}
    return httpx.Response(200, json={"choices": [choice]})


def blocks(*answers: Any) -> dict[str, Any]:
    """The answer of a document call from one answer per page."""
    return {f"page_{n}": answer for n, answer in enumerate(answers, 1)}


def by_page(data: bytes, kind: str, *answers: Any) -> tuple[Extraction, Model]:
    stub = Model({"document_type": kind}, *answers)
    return stub.scoped("page").extract(doc(len(answers)), data, "travel"), stub


def by_document(data: bytes, kind: str, answer: Any) -> tuple[Extraction, Model]:
    stub = Model({"document_type": kind}, answer)
    pages = len(PdfReader(BytesIO(data)).pages)
    return stub.scoped("document").extract(doc(pages), data, "travel"), stub


def joined(*paths: Any) -> bytes:
    """One PDF with the pages of these PDFs, in order."""
    writer = PdfWriter()
    for path in paths:
        writer.append(PdfReader(path))
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def parts(request: dict[str, Any]) -> list[dict[str, Any]]:
    [message] = request["messages"]
    assert message["role"] == "user"
    content: list[dict[str, Any]] = message["content"]
    return content


def images_of(request: dict[str, Any]) -> list[tuple[str, bytes]]:
    """The mime type and the bytes of each image of a request, in order."""
    found = []
    for part in parts(request):
        if part["type"] == "image_url":
            head, encoded = part["image_url"]["url"].split(",", 1)
            mime = head.removeprefix("data:").removesuffix(";base64")
            found.append((mime, base64.b64decode(encoded)))
    return found


def second_page(**values: Any) -> dict[str, Any]:
    """An answer for page 2 of `two_pages()` (the cancellation) with these values;
    the snippet of a value is its text on that page."""
    shown = {"traveller_name": "Anna Keller", "booking_reference": "WPR-482913"}
    answer = nothing_on(BOOKING_TYPE)
    for name, value in values.items():
        answer["values"][name] = value
        answer["snippets"][name] = shown.get(name, str(value))
    return answer


# ------------------------------------------------------------------- the setting


def test_the_default_scope_is_the_page_and_the_ids_of_issue_20_stay() -> None:
    assert OmniSettings().scope == "page" and omni.ISSUE_18.scope == "page"
    ids = {p: omni.config_id(OmniSettings(prompt=p)) for p in omni.PROMPTS}
    assert ids == {  # the ids of the saved reports of #19, #20 and #125
        "v1": "b0f2e2ccab2b",
        "schema-only": "3cc3e9264e13",
        "described": "2df4dfdab3b8",
        "example": "f83b155b3d22",
    }
    assert omni.config_id(OmniSettings(dpi=100)) == "43d2587b68c2"
    assert omni.prompt_sha256("v1") == (
        "e8096cae4ff9aa0d4a95bc1c00070f88d6b9a38753c384d1ed10abd6814e0d9a"
    )


def test_the_reader_of_the_application_keeps_the_page_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`CLAIMS_READER=omni` builds the reader of #18: the document scope is an
    experiment and is on only when a caller asks for it."""
    monkeypatch.setenv("CLAIMS_READER", "omni")
    reader = get_reader()
    assert isinstance(reader, OmniReader)
    assert reader.settings == omni.ISSUE_18 == OmniSettings(scope="page")
    assert omni.SCOPES == ("page", "document")


def test_the_document_scope_is_another_configuration() -> None:
    page, document = OmniSettings(), OmniSettings(scope="document")
    assert omni.config_id(document) != omni.config_id(page)
    assert omni.prompt_sha256("v1", "document") != omni.prompt_sha256("v1")
    assert omni.config_id(document) == omni.config_id(OmniSettings(scope="document"))
    # the texts of a document call are the variable of the comparison of #126: a
    # changed word of the prompt or of the page mark gives another id
    assert omni.config_id(document) == "54b875de9dfa"
    assert omni.prompt_sha256("v1", "document") == (
        "0c6895e9c62f50c0156e8b717afc0bebe78d95d512c1ce1587c4af5b1331ebb2"
    )


@pytest.mark.parametrize(
    "bad",
    [
        {"scope": "pages"},
        {"scope": ""},
        {"scope": "Document"},
        {"scope": "document", "prompt": "described"},
        {"scope": "document", "prompt": "schema-only"},
        {"scope": "document", "prompt": "example"},
    ],
)
def test_a_scope_that_makes_no_sense_is_refused(bad: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        OmniSettings(**bad)


@pytest.mark.parametrize("bad", [None, 1, True, ["document"]])
def test_a_scope_of_the_wrong_type_is_refused(bad: Any) -> None:
    with pytest.raises(TypeError):
        OmniSettings(scope=bad)


def test_the_harness_takes_the_scope_and_records_it_only_when_it_is_not_the_page() -> (
    None
):
    assert ev.omni_options(["scope=document"]) == OmniSettings(scope="document")
    with pytest.raises(ValueError):
        ev.omni_options(["scope=both"])
    _, page, page_meter = ev._omni(options=OmniSettings())
    _, document, document_meter = ev._omni(options=OmniSettings(scope="document"))
    assert page_meter is not None and document_meter is not None
    page_meter.close()
    document_meter.close()
    assert "scope" not in page  # a report of the page mode stays as #20 wrote it
    assert document["scope"] == "document"
    assert document["config_id"] != page["config_id"]
    assert page["prompt_sha256"] == omni.prompt_sha256("v1")
    assert document["prompt_sha256"] == omni.prompt_sha256("v1", "document")
    assert document["prompt_sha256"] != page["prompt_sha256"]
    assert document["max_document_bytes"] == omni.MAX_DOCUMENT_BYTES
    assert "max_document_bytes" not in page


# ------------------------------------------------------------------- the requests


def test_a_document_of_one_page_sends_the_requests_of_issue_18() -> None:
    stub = Model(ANSWER, answer_from(BOOKING))
    out = stub.scoped("document").extract(doc(), BOOKING.read_bytes(), "travel")
    assert [sha(without_image(b)) for b in stub.requests] == [CLASSIFY_SHA, PAGE_SHA]
    assert names(out) == ALL_FOUR


# SHA-256 of request bodies of the page scope with no image, taken with omni.py of
# commit 8a2dd09 (before issue #126). The code of #126 gives the same bodies.
BODIES_BEFORE_126 = {
    "schema-only": "e29dd74309d42033504f9639bf482f1f35e43067238477f6c34aed4f5559b2bb",
    "described": "d6f35bf9ffc0593c30345aa42f9a669a68146fb270e327ee3b46aa1ef58ca1e6",
    "example": "887c49287867fc1675a9af9493c527a6f6c34cfa448bcfb9e04dc614a6f01ed3",
    "two pages": [
        "4a6f45af263d53a2d7f4d47ccc7192772dc5c03e9103a29597dbe69cf903775d",
        "76499cbbeec9920114d8031b542aa75a4bae275008be92f19606147e6f9ec150",
    ],
    "thinking": [
        "6ead4bf78a7a5684a00d640a1f1f4ec070c067da38d5caa3da5ddb9998cf5ba3",
        "4fcaa1c062db166a42eb771cdb1413585c35c412d9f1bb89c88b266548bd1047",
    ],
}


def bodies(data: bytes, pages: int, **settings: Any) -> list[str]:
    """The SHA-256 of each request body (no image) of one read with these settings."""
    stub = Model(ANSWER, *[answer_from(BOOKING)] * pages)
    client = httpx.Client(transport=httpx.MockTransport(stub.handle))
    reader = OmniReader(client=client, settings=OmniSettings(**settings))
    reader.extract(doc(pages), data, "travel")
    return [sha(without_image(b)) for b in stub.requests]


def test_the_page_scope_sends_the_bodies_of_before_issue_126() -> None:
    """More than the two pins of #18: every prompt, a document of two pages, and
    thinking with a budget (the key `reasoning_budget` last)."""
    booking = BOOKING.read_bytes()
    assert bodies(booking, 1) == [CLASSIFY_SHA, PAGE_SHA]
    for prompt in ("schema-only", "described", "example"):
        assert bodies(booking, 1, prompt=prompt) == [
            CLASSIFY_SHA,
            BODIES_BEFORE_126[prompt],
        ]
    assert bodies(two_pages(), 2) == [CLASSIFY_SHA, *BODIES_BEFORE_126["two pages"]]
    thinking = {"thinking": True, "reasoning_budget": 512, "max_tokens": 3584}
    assert bodies(booking, 1, **thinking) == BODIES_BEFORE_126["thinking"]


def test_the_page_scope_still_sends_one_request_per_page() -> None:
    _, stub = by_page(two_pages(), "booking_confirmation", answer_from(BOOKING), {})
    assert len(stub.requests) == 3
    assert [len(images_of(r)) for r in stub.requests] == [1, 1, 1]


def test_a_document_call_holds_every_page_image_in_order_with_its_page_mark() -> None:
    data = two_pages()
    _, per_page = by_page(data, "booking_confirmation", answer_from(BOOKING), {})
    _, stub = by_document(data, "booking_confirmation", blocks(answer_from(BOOKING)))

    classify, call = stub.requests  # one classify call and one call for the document
    assert classify == per_page.requests[0]  # page 1 only, as in the page mode
    content = parts(call)
    kinds = [p["type"] for p in content]
    assert kinds == ["text", "text", "image_url", "text", "image_url"]
    assert [content[1]["text"], content[3]["text"]] == ["Page 1 of 2:", "Page 2 of 2:"]
    sent = [image_of(r) for r in per_page.requests[1:]]
    assert images_of(call) == sent and sent[0] != sent[1]
    assert {mime for mime, _ in images_of(call)} == {"image/png"}


def test_a_document_call_keeps_the_settings_and_the_key_order_of_issue_18() -> None:
    _, stub = by_document(two_pages(), "booking_confirmation", blocks({}, {}))
    call = stub.requests[1]
    assert list(call) == ORDER
    assert call["model"] == "omni-test"
    assert call["temperature"] == 0.2 and call["max_tokens"] == 2048
    assert call["chat_template_kwargs"] == {"enable_thinking": False}


def test_the_schema_of_a_document_answer_is_one_page_answer_per_page() -> None:
    _, stub = by_document(two_pages(), "booking_confirmation", blocks({}, {}))
    form = stub.requests[1]["response_format"]
    assert form["type"] == "json_schema"
    assert form["json_schema"]["name"] == "answer"
    assert form["json_schema"]["strict"] is True
    schema = form["json_schema"]["schema"]
    page = omni.answer_schema(SCHEMAS[BOOKING_TYPE])
    defs = page.pop("$defs")
    assert schema == {
        "type": "object",
        "properties": {
            "page_1": {"$ref": "#/$defs/page"},
            "page_2": {"$ref": "#/$defs/page"},
        },
        "required": ["page_1", "page_2"],
        "additionalProperties": False,
        "$defs": {"page": page, **defs},
    }
    assert schema == omni.document_schema(SCHEMAS[BOOKING_TYPE], 2)


def test_the_document_prompt_keeps_the_rules_of_v1_word_for_word() -> None:
    _, stub = by_document(two_pages(), "booking_confirmation", blocks({}, {}))
    prompt: str = parts(stub.requests[1])[0]["text"]
    values = omni.values_schema(SCHEMAS[BOOKING_TYPE])
    assert prompt == omni.document_prompt("booking_confirmation", 2, 2, values)
    assert "the 2 pages of one booking_confirmation" in prompt
    assert '"page_1"' in prompt and json.dumps(values) in prompt
    page = omni.page_prompt("v1", "booking_confirmation", 1, 2, values)
    rules = [
        "Answer with JSON only, in this form:",
        "Every key is required.",
        ": never guess.",
        "Dates are YYYY-MM-DD. Amounts are plain numbers: no currency, no thousands "
        "separator.",
        "copied character by character, as short as possible.",
        "JSON schema of `values`:",
    ]
    for rule in rules:
        assert rule in page and rule in prompt, rule


# ------------------------------------------------------------- the same answers


PAGE_1 = answer_from(BOOKING, traveller_name=None)
BAD_LINE = answer_from(BOOKING)
BAD_LINE["values"]["line_items"] = [
    {**line, "amount": 1.23} if i == 0 else line
    for i, line in enumerate(BAD_LINE["values"]["line_items"])
]
SAME = [
    ("each page gives its fields", [PAGE_1, second_page(traveller_name="Anna Keller")]),
    ("two values for one field", [answer_from(BOOKING), second_page(trip_price=340.0)]),
    (
        "the same value twice",
        [answer_from(BOOKING), second_page(traveller_name="ANNA KELLER")],
    ),
    (
        "a placeholder and then the value",
        [
            answer_from(BOOKING, traveller_name="Not answerable"),
            second_page(traveller_name="Anna Keller"),
        ],
    ),
    ("page 2 is not an answer", [answer_from(BOOKING), "not json"]),
    ("page 1 is not an answer", [[1, 2], second_page(traveller_name="Anna Keller")]),
    ("no page is an answer", ["no", "no"]),
    (
        "the values of page 1 given for page 2",
        [nothing_on(BOOKING_TYPE), answer_from(BOOKING)],
    ),
    ("a line that is not on its page", [BAD_LINE, nothing_on(BOOKING_TYPE)]),
    ("nothing on any page", [nothing_on(BOOKING_TYPE), nothing_on(BOOKING_TYPE)]),
]


@pytest.mark.parametrize("answers", [a for _, a in SAME], ids=[n for n, _ in SAME])
def test_the_same_page_answers_give_the_same_extraction_in_both_scopes(
    answers: list[Any],
) -> None:
    """The blocks go through the checks and the merge of the page mode. This proves
    the code path, not that the model answers the same in both scopes."""
    data = two_pages()
    page, _ = by_page(data, "booking_confirmation", *answers)
    document, stub = by_document(data, "booking_confirmation", blocks(*answers))
    assert document.model_dump() == page.model_dump()
    assert len(stub.requests) == 2


@pytest.mark.parametrize(
    ("paths", "lines"),
    [((SCAN, SCAN), 0), ((SCAN, BOOKING), 0), ((BOOKING, SCAN), 3)],
    ids=["scan, scan", "scan, text", "text, scan"],
)
def test_scan_pages_give_the_same_extraction_in_both_scopes(
    paths: Any, lines: int
) -> None:
    """Page 1 holds the three lines. On a scan nothing confirms them: no list. With
    page 1 as text they are confirmed; the scan page 2 gives no line here, and the
    reader cannot know if it holds one that the model did not give (a limit of both
    scopes)."""
    data = joined(*paths)
    first = answer_from(paths[0], traveller_name=None)
    answers = [first, second_page(traveller_name="Anna Keller")]
    page, _ = by_page(data, "booking_confirmation", *answers)
    document, _ = by_document(data, "booking_confirmation", blocks(*answers))
    assert document.model_dump() == page.model_dump()
    assert len(document.line_items) == lines
    assert names(document) == ALL_FOUR


# ------------------------------------------------------------------ the blocks


def test_a_field_keeps_the_page_of_its_block() -> None:
    answers = [PAGE_1, second_page(traveller_name="Anna Keller")]
    out, _ = by_document(two_pages(), "booking_confirmation", blocks(*answers))
    assert {f.name: f.source.page for f in out.fields} == {
        "booking_reference": 1,
        "departure_date": 1,
        "trip_price": 1,
        "traveller_name": 2,
    }
    assert {f.confidence for f in out.fields} == {VERIFIED}
    assert len(out.line_items) == 3


def test_a_value_in_the_block_of_another_page_is_not_stored() -> None:
    """The departure date is printed on page 1 only. Given in the block of page 2,
    its snippet is not on that page: the reader does not move it to page 1."""
    answer = blocks(nothing_on(BOOKING_TYPE), answer_from(BOOKING))
    out, _ = by_document(two_pages(), "booking_confirmation", answer)
    assert "departure_date" not in names(out)
    assert {f.source.page for f in out.fields} == {2}


def test_on_a_scan_the_page_of_a_field_is_the_page_of_its_block() -> None:
    """Nothing can check it there: the source is the whole page of the block and a
    person checks the field."""
    data = joined(SCAN, SCAN)
    answer = blocks(nothing_on(BOOKING_TYPE), answer_from(SCAN))
    out, _ = by_document(data, "booking_confirmation", answer)
    assert names(out) == ALL_FOUR
    for f in out.fields:
        assert f.source.page == 2 and f.source.snippet is None
        assert f.source.box is not None and f.confidence == UNVERIFIED


def assert_a_person_checks_all(out: Extraction) -> None:
    assert out.fields, "the fields of the pages that were read stay"
    assert all(f.confidence < LOW_CONFIDENCE for f in out.fields)
    assert out.line_items == []


def test_a_missing_block_is_a_page_that_nobody_read() -> None:
    out, _ = by_document(
        two_pages(), "booking_confirmation", {"page_1": answer_from(BOOKING)}
    )
    assert names(out) == ALL_FOUR and {f.source.page for f in out.fields} == {1}
    assert_a_person_checks_all(out)


@pytest.mark.parametrize(
    "key", ["page_3", "page_0", "page_-1", "page_1.5", "page_02", "Page_2", "pages"]
)
def test_an_unknown_key_is_not_a_page_and_makes_the_answer_incomplete(key: str) -> None:
    """The content of the unknown key is never read as a page: here it holds the
    only traveller name, which is not stored."""
    first = answer_from(BOOKING, traveller_name=None)
    answer = blocks(first, nothing_on(BOOKING_TYPE))
    answer[key] = second_page(traveller_name="Anna Keller")
    out, _ = by_document(two_pages(), "booking_confirmation", answer)
    assert names(out) == ALL_FOUR - {"traveller_name"}
    assert_a_person_checks_all(out)


def test_the_order_of_the_blocks_in_the_answer_does_not_count() -> None:
    """A block is found by its key, never by its place in the answer."""
    answers = [PAGE_1, second_page(traveller_name="Anna Keller")]
    in_order, _ = by_document(two_pages(), "booking_confirmation", blocks(*answers))
    turned = {"page_2": answers[1], "page_1": answers[0]}
    out, _ = by_document(two_pages(), "booking_confirmation", turned)
    assert out.model_dump() == in_order.model_dump()


def test_a_block_given_twice_makes_the_whole_answer_unusable() -> None:
    """JSON with one key twice: which block is the page is not known."""
    one, two = json.dumps(answer_from(BOOKING)), json.dumps(nothing_on(BOOKING_TYPE))
    raw = f'{{"page_1": {one}, "page_2": {two}, "page_1": {two}}}'
    out, _ = by_document(two_pages(), "booking_confirmation", raw)
    assert out.document_type == "booking_confirmation"
    assert out.fields == [] and out.line_items == []


@pytest.mark.parametrize(
    "answer",
    ["not json", "", "[]", '{"page_1": {"values": {', [answer_from(BOOKING)], 7, None],
)
def test_an_answer_that_is_not_an_object_of_blocks_gives_no_field(answer: Any) -> None:
    reply = "null" if answer is None else answer
    out, stub = by_document(two_pages(), "booking_confirmation", reply)
    assert out.document_type == "booking_confirmation"
    assert out.fields == [] and out.line_items == []
    assert len(stub.requests) == 2  # no second try, no call per page


def test_an_answer_that_the_endpoint_cut_is_never_a_complete_document() -> None:
    """The endpoint says `length`. When the text is whole JSON all the same, its
    values are read, as the page scope reads them, but nothing of it is final: a
    person checks every field and no line is kept. Dropping it would leave a
    document with no field, which nothing downstream marks (D80 (13)). When the
    JSON does not close, nothing can be read."""
    answer = blocks(answer_from(BOOKING), nothing_on(BOOKING_TYPE))
    marked, _ = by_document(
        two_pages(), "booking_confirmation", finished(answer, "length")
    )
    assert names(marked) == ALL_FOUR
    assert_a_person_checks_all(marked)
    stopped, _ = by_document(
        two_pages(), "booking_confirmation", finished(answer, "stop")
    )
    assert names(stopped) == ALL_FOUR and len(stopped.line_items) == 3
    assert {f.confidence for f in stopped.fields} == {VERIFIED}
    assert [f.value for f in marked.fields] == [f.value for f in stopped.fields]
    broken, _ = by_document(
        two_pages(),
        "booking_confirmation",
        finished(json.dumps(answer)[:-40], "length"),
    )
    assert broken.fields == [] and broken.line_items == []


def test_a_document_call_that_fails_gives_no_field_and_no_call_per_page() -> None:
    stub = Model(ANSWER, 500, 500, 500)
    out = stub.scoped("document").extract(doc(2), two_pages(), "travel")
    assert out.document_type == "booking_confirmation" and out.fields == []
    assert len(stub.requests) == 4  # classify, then the call and its two retries
    assert all(len(images_of(r)) == 2 for r in stub.requests[1:])


def test_a_document_call_is_tried_again_after_a_429_like_a_page_call() -> None:
    answer = blocks(answer_from(BOOKING), nothing_on(BOOKING_TYPE))
    stub = Model(ANSWER, 429, answer)
    out = stub.scoped("document").extract(doc(2), two_pages(), "travel")
    assert names(out) == ALL_FOUR and len(stub.requests) == 3
    assert stub.waits == [omni.WAITS[0]]


# ------------------------------------------------------------------ the limits


def test_a_pdf_longer_than_the_limit_sends_the_first_pages_and_says_so() -> None:
    limit = omni.MAX_PAGES
    empty = nothing_on(BOOKING_TYPE)
    answer = blocks(answer_from(BOOKING), *[empty] * (limit - 1))
    out, stub = by_document(copies(limit + 2), "booking_confirmation", answer)

    call = stub.requests[1]
    assert len(images_of(call)) == limit
    content = parts(call)
    assert content[1]["text"] == f"Page 1 of {limit + 2}:"
    assert f"the first {limit} of the {limit + 2} pages" in content[0]["text"]
    required = call["response_format"]["json_schema"]["schema"]["required"]
    assert required == [f"page_{n}" for n in range(1, limit + 1)]
    assert names(out) == ALL_FOUR
    assert_a_person_checks_all(out)  # the pages after the limit were not read


def test_pages_that_do_not_fit_in_one_request_are_not_sent_and_not_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The images of one request are bounded. A page that does not fit is not sent:
    it counts as not read, as a page after the page limit does."""
    data = two_pages()
    first, second = omni.pages(data)
    monkeypatch.setattr(omni, "MAX_DOCUMENT_BYTES", len(first.image) + 1)
    out, stub = by_document(
        data, "booking_confirmation", {"page_1": answer_from(BOOKING)}
    )
    call = stub.requests[1]
    assert len(images_of(call)) == 1
    assert parts(call)[1]["text"] == "Page 1 of 2:"
    assert "the first 1 of the 2 pages" in parts(call)[0]["text"]
    schema = call["response_format"]["json_schema"]["schema"]
    assert schema["required"] == ["page_1"]
    assert names(out) == ALL_FOUR
    assert_a_person_checks_all(out)


def test_the_first_page_is_sent_even_when_it_is_larger_than_the_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(omni, "MAX_DOCUMENT_BYTES", 1)
    _, stub = by_document(
        two_pages(), "booking_confirmation", {"page_1": answer_from(BOOKING)}
    )
    assert len(images_of(stub.requests[1])) == 1
