"""The request and the answer parser of a document call (issue #126), offline.

`scope="document"` sends every page image of a document in one request and reads one
block per page from the answer. These tests are written against the note
`backend/.dev-data/issue-126/READER-INTERFACE.md`, not against the code. The two
defects that they found (a very deep answer, `Retry-After: nan`) are fixed. A case
that the note does not settle is pinned as it is and its name ends with
`_undocumented`.

A. the request (what is sent): the images and their marks, the schema, the prompt,
   the limits, the settings, the failures. B. the parser (`omni._blocks` and what
   `extract` makes of it): the keys of the blocks, the whole answer, one block, the
   finish reason, the fields inside a block.

The core tests are in `test_omni_document.py`; the cases are not repeated here.
Stubs and mock transports only: no network, no key.
"""

import base64
import functools
import json
import re
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pypdfium2 as pdfium  # type: ignore[import-untyped]
import pytest
from PIL import Image
from pydantic import BaseModel
from pypdf import PdfReader, PdfWriter

from app.docintel import omni
from app.docintel.omni import UNVERIFIED, VERIFIED, OmniReader, OmniSettings
from app.docintel.schemas import SCHEMAS
from app.fraud.routing import LOW_CONFIDENCE
from app.models import DocumentType, Extraction
from tests.reader_contract import MOCK
from tests.test_omni_document import (
    ORDER,
    Model,
    blocks,
    by_document,
    by_page,
    finished,
    images_of,
    joined,
    parts,
    second_page,
)
from tests.test_omni_reader import (
    ALL_FOUR,
    ANSWER,
    BOOKING,
    CANCELLATION,
    PHOTO,
    URL,
    answer_from,
    doc,
    names,
    nothing_on,
    timeout,
)
from tests.test_omni_settings import CLASSIFY_SHA, PAGE_SHA, sha, without_image

KIND = "booking_confirmation"
T = DocumentType.booking_confirmation
CERTIFICATE = MOCK / "claim-1-travel" / "medical-certificate.pdf"
FINDING = "FINDING: "


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVIDIA_KEY", "test-key-value")
    monkeypatch.setenv("VLM_BASE_URL", URL)
    monkeypatch.setenv("VLM_MODEL", "omni-test")


# ------------------------------------------------------------------------ helpers


class Wire(Model):
    """`Model`, and also the replies of the page stub that `Model` does not take: a
    tuple (status, headers) and a callable that raises (a timeout)."""

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        self.headers.append(request.headers)
        assert str(request.url) == f"{URL}/chat/completions"
        reply = next(self.replies)
        if isinstance(reply, httpx.Response):
            return reply
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


def length_of(data: bytes) -> int:
    return len(PdfReader(BytesIO(data)).pages) if data.startswith(b"%PDF") else 1


def run(
    scope: str, data: bytes, *replies: Any, **settings: Any
) -> tuple[Extraction, Wire]:
    """`extract` with these settings on a stub that gives these replies (the first
    one is the answer to classify)."""
    stub = Wire(*replies)
    reader = OmniReader(
        client=httpx.Client(transport=httpx.MockTransport(stub.handle)),
        sleep=stub.waits.append,
        settings=OmniSettings(scope=scope, **settings),
    )
    return reader.extract(doc(length_of(data)), data, "travel"), stub


def empties(n: int) -> list[dict[str, Any]]:
    return [nothing_on(T) for _ in range(n)]


def pdfs() -> list[Path]:
    """The one-page PDFs of the kit, the booking first. Never data/mock/mp1."""
    first = [BOOKING, CANCELLATION, CERTIFICATE]
    found = sorted(p for d in ("claim-*", "kit-*") for p in MOCK.glob(f"{d}/*.pdf"))
    return first + [p for p in found if p not in first]


@functools.cache
def long_document(n: int) -> bytes:
    """A PDF of n pages, page k from the k-th file of the kit (then again)."""
    files = pdfs()
    assert len(files) >= 10
    return joined(*[files[i % len(files)] for i in range(n)])


@functools.cache
def page_images(n: int) -> tuple[tuple[str, bytes], ...]:
    """The images that the PAGE scope sends for the document of n pages."""
    _, stub = by_page(long_document(n), KIND, *empties(n))
    return tuple(images_of(r)[0] for r in stub.requests[1:])


def document_call(n: int, **settings: Any) -> dict[str, Any]:
    """The extraction request of the document scope for a document of n pages."""
    _, stub = run("document", long_document(n), ANSWER, blocks(*empties(n)), **settings)
    assert len(stub.requests) == 2
    return stub.requests[1]


def data_url(page: omni.Page) -> str:
    return f"data:{page.mime};base64,{base64.b64encode(page.image).decode()}"


def body(content: list[dict[str, Any]], schema: dict[str, Any]) -> dict[str, Any]:
    """The whole body of an extraction request with the default settings."""
    form = {"name": "answer", "schema": schema, "strict": True}
    return {
        "model": "omni-test",
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.2,
        "max_tokens": 2048,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {"type": "json_schema", "json_schema": form},
    }


def same_bytes(a: Any, b: Any) -> bool:
    return json.dumps(a) == json.dumps(b)


def checked_by_a_person(out: Extraction) -> None:
    assert out.fields, "the fields of the pages that were read stay"
    assert all(f.confidence < LOW_CONFIDENCE for f in out.fields)
    assert out.line_items == []


def readable() -> list[DocumentType]:
    return [k for k in SCHEMAS if k not in omni.NOTHING_TO_READ]


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


# =============================================================== A. the request


@pytest.mark.parametrize("n", [2, 3, omni.MAX_PAGES])
def test_a_document_call_holds_each_page_once_in_the_order_of_the_page_scope(
    n: int,
) -> None:
    _, stub = by_document(long_document(n), KIND, blocks(*empties(n)))
    assert len(stub.requests) == 2  # classify and one call, whatever the pages
    sent = images_of(stub.requests[1])
    assert len(sent) == n
    assert sent == list(page_images(n))  # page k is the image of page k
    assert len({image for _, image in sent}) == n  # none twice, none left out


@pytest.mark.parametrize("n", [2, 3, omni.MAX_PAGES])
def test_the_parts_are_the_prompt_then_a_page_mark_and_an_image_for_each_page(
    n: int,
) -> None:
    _, stub = by_document(long_document(n), KIND, blocks(*empties(n)))
    call = stub.requests[1]
    [message] = call["messages"]  # exactly one message, and it is the user's
    assert set(message) == {"role", "content"} and message["role"] == "user"
    assert all(m["role"] != "system" for m in call["messages"])
    content = parts(call)
    assert [p["type"] for p in content] == ["text"] + ["text", "image_url"] * n
    values = omni.values_schema(SCHEMAS[T])
    assert content[0] == {
        "type": "text",
        "text": omni.document_prompt(KIND, n, n, values),
    }
    for k in range(1, n + 1):
        mark, image = content[2 * k - 1], content[2 * k]
        assert mark == {"type": "text", "text": f"Page {k} of {n}:"}
        assert set(image) == {"type", "image_url"} and set(image["image_url"]) == {
            "url"
        }


@pytest.mark.parametrize("n", [2, 3])
def test_every_page_image_is_a_png_data_url_of_the_size_of_the_page(n: int) -> None:
    data = long_document(n)
    call = document_call(n)
    expected = omni.pages(data)
    urls = [p["image_url"]["url"] for p in parts(call) if p["type"] == "image_url"]
    assert len(urls) == n
    for url, page in zip(urls, expected, strict=True):
        assert re.fullmatch(r"data:image/png;base64,[A-Za-z0-9+/]+={0,2}", url)
        png = base64.b64decode(url.split(",", 1)[1], validate=True)
        assert png.startswith(b"\x89PNG\r\n\x1a\n")
        with Image.open(BytesIO(png)) as image:
            assert image.size == page.size and image.format == "PNG"
        assert url == data_url(page)


def photo_png() -> bytes:
    out = BytesIO()
    Image.new("RGB", (320, 240), (200, 30, 30)).save(out, "PNG")
    return out.getvalue()


@pytest.mark.parametrize(
    ("data", "kind", "mime"),
    [
        (PHOTO.read_bytes(), "repair_invoice", "image/jpeg"),
        (photo_png(), "receipt", "image/png"),
    ],
    ids=["jpeg photo", "png photo"],
)
def test_a_photo_has_one_page_and_takes_the_page_call_in_both_scopes(
    data: bytes, kind: str, mime: str
) -> None:
    schema_kind = DocumentType(kind)
    answer = nothing_on(schema_kind)
    page_out, page = run("page", data, {"document_type": kind}, answer)
    document_out, document = run("document", data, {"document_type": kind}, answer)
    assert len(document.requests) == 2 == len(page.requests)
    assert same_bytes(document.requests, page.requests)
    call = document.requests[1]
    assert [p["type"] for p in parts(call)] == ["text", "image_url"]
    assert "Page 1 of" not in json.dumps(call) and "page_1" not in json.dumps(call)
    assert images_of(call) == [(mime, data)]  # sent as it is
    assert document_out.model_dump() == page_out.model_dump()


def test_a_document_with_one_rendered_page_is_a_page_call_never_a_block_call() -> None:
    schema = omni.answer_schema(SCHEMAS[T])
    _, stub = run("document", BOOKING.read_bytes(), ANSWER, answer_from(BOOKING))
    text = parts(stub.requests[1])[0]["text"]
    assert text == omni.page_prompt("v1", KIND, 1, 1, omni.values_schema(SCHEMAS[T]))
    assert stub.requests[1]["response_format"]["json_schema"]["schema"] == schema


# ------------------------------------------------ classification does not change


@pytest.mark.parametrize("n", [2, 3])
def test_classify_is_the_same_request_in_both_scopes_and_holds_page_1_only(
    n: int,
) -> None:
    data = long_document(n)
    _, page = by_page(data, KIND, *empties(n))
    _, document = by_document(data, KIND, blocks(*empties(n)))
    classify = document.requests[0]
    assert same_bytes(classify, page.requests[0])
    assert sha(without_image(classify)) == CLASSIFY_SHA
    assert images_of(classify) == [page_images(n)[0]]
    assert len([r for r in document.requests if "enum" in json.dumps(r)]) == 1
    assert images_of(document.requests[1])[0] == images_of(classify)[0]


def test_classify_renders_only_page_1_in_the_document_scope(
    rendered: list[int],
) -> None:
    stub = Wire(ANSWER)
    reader = OmniReader(
        client=httpx.Client(transport=httpx.MockTransport(stub.handle)),
        settings=OmniSettings(scope="document"),
    )
    assert reader.classify(doc(3), long_document(3)) == KIND
    assert len(rendered) == 1 and len(stub.requests) == 1


def test_extract_after_classify_does_not_classify_again() -> None:
    stub = Wire(ANSWER, blocks(answer_from(BOOKING), nothing_on(T)))
    reader = OmniReader(
        client=httpx.Client(transport=httpx.MockTransport(stub.handle)),
        settings=OmniSettings(scope="document"),
    )
    data = long_document(2)
    assert reader.classify(doc(2), data) == KIND
    out = reader.extract(doc(2), data, "travel")
    assert names(out) == ALL_FOUR
    assert len(stub.requests) == 2  # one classify, one document call
    assert len(images_of(stub.requests[0])) == 1
    assert len(images_of(stub.requests[1])) == 2


def test_a_failed_classify_is_not_cached_and_extract_asks_again() -> None:
    stub = Wire(400, ANSWER, blocks(answer_from(BOOKING), nothing_on(T)))
    reader = OmniReader(
        client=httpx.Client(transport=httpx.MockTransport(stub.handle)),
        settings=OmniSettings(scope="document"),
    )
    data = long_document(2)
    assert reader.classify(doc(2), data) == "unknown"
    out = reader.extract(doc(2), data, "travel")
    assert names(out) == ALL_FOUR
    assert [len(images_of(r)) for r in stub.requests] == [1, 1, 2]


def test_a_document_of_unknown_type_sends_no_document_call() -> None:
    out, stub = run("document", long_document(2), {"document_type": "unknown"})
    assert out.document_type == "unknown" and out.fields == []
    assert len(stub.requests) == 1


# ------------------------------------------------------- the settings of the call

SETTINGS = [
    {"temperature": 0.0},
    {"temperature": 0.9},
    {"max_tokens": 4096},
    {"thinking": True},
    {"thinking": True, "reasoning_budget": 500},
    {"dpi": 72},
    {"dpi": 200},
    {"temperature": 0.5, "max_tokens": 1000, "thinking": True, "reasoning_budget": 7},
]


@pytest.mark.parametrize("settings", SETTINGS, ids=lambda s: ",".join(s))
def test_the_settings_reach_the_document_call_as_they_reach_a_page_call(
    settings: dict[str, Any],
) -> None:
    data = long_document(2)
    _, page = run("page", data, ANSWER, *empties(2), **settings)
    _, document = run("document", data, ANSWER, blocks(*empties(2)), **settings)
    call, reference = document.requests[1], page.requests[1]
    assert list(call) == list(reference)  # the same keys in the same order
    for key in set(call) - {"messages", "response_format"}:
        assert call[key] == reference[key], key
    assert same_bytes(document.requests[0], page.requests[0])  # classify too
    assert call["temperature"] == settings.get("temperature", 0.2)
    assert call["max_tokens"] == settings.get("max_tokens", 2048)
    assert call["chat_template_kwargs"] == {
        "enable_thinking": settings.get("thinking", False)
    }
    assert images_of(call) == [images_of(r)[0] for r in page.requests[1:]]


@pytest.mark.parametrize("settings", SETTINGS, ids=lambda s: ",".join(s))
def test_the_reasoning_budget_is_a_key_only_when_set_and_then_the_last(
    settings: dict[str, Any],
) -> None:
    call = document_call(2, **settings)
    if "reasoning_budget" in settings:
        assert list(call) == [*ORDER, "reasoning_budget"]
        assert call["reasoning_budget"] == settings["reasoning_budget"]
    else:
        assert list(call) == ORDER


@pytest.mark.parametrize(("dpi", "size"), [(72, (595, 842)), (200, (1654, 2339))])
def test_the_dpi_sets_the_size_of_every_image_of_the_document_call(
    dpi: int, size: tuple[int, int]
) -> None:
    call = document_call(3, dpi=dpi)
    sent = images_of(call)
    assert len(sent) == 3
    for (_, png), page in zip(sent, omni.pages(long_document(3), dpi=dpi), strict=True):
        with Image.open(BytesIO(png)) as image:
            assert image.size == page.size
            assert (
                abs(image.size[0] - size[0]) <= 1 and abs(image.size[1] - size[1]) <= 1
            )


@pytest.mark.parametrize("scope", ["page", "document"])
def test_the_timeout_is_the_timeout_of_the_client_in_both_scopes(scope: str) -> None:
    reader = OmniReader(settings=OmniSettings(scope=scope, timeout=12.5))
    try:
        assert reader._client.timeout.read == 12.5
        assert reader._client.timeout.connect == 12.5
    finally:
        reader._client.close()


def test_the_document_call_has_the_default_body_of_the_settings_of_issue_18() -> None:
    data = long_document(2)
    call = document_call(2)
    assert call["model"] == "omni-test"
    content: list[dict[str, Any]] = [{"type": "text", "text": parts(call)[0]["text"]}]
    for k, page in enumerate(omni.pages(data), 1):
        mark = {"type": "text", "text": f"Page {k} of 2:"}
        image = {"type": "image_url", "image_url": {"url": data_url(page)}}
        content += [mark, image]
    assert same_bytes(call, body(content, omni.document_schema(SCHEMAS[T], 2)))


# --------------------------------------------------------------- the schema sent


def refs(node: Any) -> Iterator[str]:
    """Every `$ref` of a JSON schema."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref":
                yield value
            else:
                yield from refs(value)
    elif isinstance(node, list):
        for value in node:
            yield from refs(value)


def resolve(root: dict[str, Any], ref: str) -> Any:
    assert ref.startswith("#/"), ref
    node: Any = root
    for step in ref[2:].split("/"):
        node = node[step]
    return node


def nested_defs(node: Any) -> int:
    """How many `$defs` keys a schema holds below its root."""
    if isinstance(node, dict):
        return sum(k == "$defs" or nested_defs(v) > 0 for k, v in node.items())
    if isinstance(node, list):
        return sum(nested_defs(v) > 0 for v in node)
    return 0


@pytest.mark.parametrize("kind", readable(), ids=lambda k: k.value)
def test_the_schema_of_a_document_answer_is_well_formed_for_every_type(
    kind: DocumentType,
) -> None:
    model: type[BaseModel] = SCHEMAS[kind]
    page = omni.answer_schema(model)
    page_defs = page.pop("$defs", {})
    value_defs = omni.values_schema(model).get("$defs", {})
    assert page_defs == value_defs
    for n in (2, 3, 10):
        schema = omni.document_schema(model, n)
        names = [f"page_{k}" for k in range(1, n + 1)]
        assert set(schema) == {
            "type",
            "properties",
            "required",
            "additionalProperties",
            "$defs",
        }
        assert schema["type"] == "object" and schema["additionalProperties"] is False
        assert schema["required"] == names and list(schema["properties"]) == names
        assert all(p == {"$ref": "#/$defs/page"} for p in schema["properties"].values())
        assert schema["$defs"]["page"] == page  # the answer of a page, no `$defs`
        assert set(schema["$defs"]) == {"page", *value_defs}
        assert {k: v for k, v in schema["$defs"].items() if k != "page"} == value_defs
        assert nested_defs(schema["$defs"]["page"]) == 0
        assert "$defs" not in schema["$defs"]["page"]
        found = list(refs(schema))
        assert found.count("#/$defs/page") == n
        for ref in found:  # every reference of the whole schema resolves
            assert resolve(schema, ref) is not None
        assert json.loads(json.dumps(schema)) == schema  # plain JSON
        assert omni.document_schema(model, n) == schema  # deterministic
        has_lines = "line_items" in model.model_fields
        assert bool(value_defs) == has_lines
        assert (set(schema["$defs"]) == {"page"}) == (not has_lines)


def test_the_two_line_item_types_get_their_own_definition() -> None:
    cancellation = omni.document_schema(
        SCHEMAS[DocumentType.cancellation_confirmation], 2
    )
    booking = omni.document_schema(SCHEMAS[T], 2)
    assert set(cancellation["$defs"]) == {"page", "CancellationLine"}
    assert set(booking["$defs"]) == {"page", "LineItem"}
    assert "LineItem" not in json.dumps(cancellation)
    medical = omni.document_schema(SCHEMAS[DocumentType.medical_certificate], 5)
    assert set(medical["$defs"]) == {"page"} and "LineItem" not in json.dumps(medical)


def test_a_changed_schema_of_one_call_does_not_change_the_next() -> None:
    first = omni.document_schema(SCHEMAS[T], 2)
    first["$defs"]["page"]["properties"]["values"]["properties"].clear()
    first["required"].append("page_9")
    again = omni.document_schema(SCHEMAS[T], 2)
    assert again["required"] == ["page_1", "page_2"]
    assert again["$defs"]["page"]["properties"]["values"]["properties"]


# ---------------------------------------------------------------------- the prompt

VALUES = omni.values_schema(SCHEMAS[T])
LINES = omni.EXTRACT.splitlines()


@pytest.mark.parametrize(
    ("sent", "pages"), [(2, 2), (3, 3), (10, 10), (1, 2), (2, 3), (10, 25)]
)
def test_the_document_prompt_says_the_pages_the_kind_the_form_and_the_schema(
    sent: int, pages: int
) -> None:
    prompt = omni.document_prompt(KIND, sent, pages, VALUES)
    if sent == pages:
        assert f"They are the {pages} pages of one {KIND}" in prompt
        assert "They are the first" not in prompt
    else:
        assert f"They are the first {sent} of the {pages} pages of one {KIND}" in prompt
        assert f"They are the {pages} pages" not in prompt
    assert '"page_1"' in prompt and '"page_2"' in prompt  # the form of the answer
    assert '{"page_1": {"values": {...}, "snippets": {...}}, "page_2": {...}}' in prompt
    assert prompt.endswith("JSON schema of `values`:\n" + json.dumps(VALUES))
    assert "this page" not in prompt
    assert omni.document_prompt(KIND, sent, pages, VALUES) == prompt  # deterministic


def test_the_document_prompt_keeps_the_rule_sentences_of_v1() -> None:
    assert len(LINES) == 8  # the rules below are taken from these lines of v1
    prompt = omni.document_prompt(KIND, 2, 2, VALUES)
    shared = [
        LINES[3],  # dates and amounts
        LINES[4].replace("this page", "that page"),  # the line items
        LINES[6],  # JSON schema of `values`:
        "Every key is required.",
        "Use null for a value that is not on that page: never guess.",
        "`snippets` maps its key to the exact text of that page that shows it, "
        "copied character by character, as short as possible.",
    ]
    for rule in shared:
        assert rule in prompt, rule
    assert "Answer with JSON only, in this form:" in prompt


def test_the_prompt_of_the_page_scope_is_not_the_prompt_of_the_document_scope() -> None:
    page = omni.page_prompt("v1", KIND, 1, 2, VALUES)
    document = omni.document_prompt(KIND, 2, 2, VALUES)
    assert page != document and "this page" in page and "page_1" not in page


@pytest.mark.parametrize("kind", readable(), ids=lambda k: k.value)
def test_the_prompt_of_every_type_holds_its_kind_and_the_keys_of_its_values(
    kind: DocumentType,
) -> None:
    values = omni.values_schema(SCHEMAS[kind])
    prompt = omni.document_prompt(kind.value, 3, 3, values)
    assert f"of one {kind.value}" in prompt
    assert all(f'"{name}"' in prompt for name in SCHEMAS[kind].model_fields)


# ---------------------------------------- single page and the default, unchanged


@pytest.mark.parametrize("scope", ["page", "document"])
def test_one_page_gives_the_two_bodies_of_issue_18_in_both_scopes(scope: str) -> None:
    _, stub = run(scope, BOOKING.read_bytes(), ANSWER, answer_from(BOOKING))
    assert [sha(without_image(b)) for b in stub.requests] == [CLASSIFY_SHA, PAGE_SHA]
    assert all(list(b) == ORDER for b in stub.requests)


@pytest.mark.parametrize("n", [2, 3])
def test_the_default_scope_sends_the_page_request_of_issue_18_for_each_page(
    n: int,
) -> None:
    data = long_document(n)
    _, stub = run("page", data, ANSWER, *empties(n))
    assert len(stub.requests) == 1 + n
    values = omni.values_schema(SCHEMAS[T])
    for k, page in enumerate(omni.pages(data), 1):
        text = omni.page_prompt("v1", KIND, k, n, values)
        image: dict[str, Any] = {
            "type": "image_url",
            "image_url": {"url": data_url(page)},
        }
        content: list[dict[str, Any]] = [{"type": "text", "text": text}, image]
        expected = body(content, omni.answer_schema(SCHEMAS[T]))
        assert same_bytes(stub.requests[k], expected)


def test_the_default_configuration_keeps_its_id_and_is_the_page_scope() -> None:
    assert omni.config_id(OmniSettings()) == "b0f2e2ccab2b"
    assert omni.config_id(OmniSettings(scope="page")) == "b0f2e2ccab2b"
    assert OmniSettings() == OmniSettings(scope="page") == omni.ISSUE_18
    assert omni.config_id(OmniSettings(scope="document")) != "b0f2e2ccab2b"
    assert omni.SCOPES == ("page", "document")


# ------------------------------------------------------------------- the limits


@pytest.mark.parametrize("n", [omni.MAX_PAGES, 11, 12, 25])
def test_a_long_document_sends_ten_images_and_says_how_long_it_is(
    n: int, rendered: list[int]
) -> None:
    limit = omni.MAX_PAGES
    answer = blocks(answer_from(BOOKING), *empties(limit - 1))
    out, stub = by_document(long_document(n), KIND, answer)
    call = stub.requests[1]
    content = parts(call)
    assert len(stub.requests) == 2
    assert len(images_of(call)) == limit
    assert images_of(call) == [(m, i) for m, i in page_images(limit)]
    marks = [content[2 * k - 1]["text"] for k in range(1, limit + 1)]
    assert marks == [f"Page {k} of {n}:" for k in range(1, limit + 1)]
    schema = call["response_format"]["json_schema"]["schema"]
    assert schema["required"] == [f"page_{k}" for k in range(1, limit + 1)]
    assert list(schema["properties"]) == schema["required"]
    assert len(rendered) <= 1 + limit  # the pages after the limit are not rendered
    assert names(out) == ALL_FOUR
    if n == limit:
        assert f"They are the {limit} pages" in content[0]["text"]
        assert {f.confidence for f in out.fields} == {VERIFIED} and out.line_items
    else:
        assert f"They are the first {limit} of the {n} pages" in content[0]["text"]
        checked_by_a_person(out)


@pytest.mark.parametrize("n", [11, 25])
def test_a_page_after_the_limit_that_the_model_names_is_never_read(n: int) -> None:
    """The model gives a block for the page after the limit: not a page sent."""
    limit = omni.MAX_PAGES
    first = answer_from(BOOKING, traveller_name=None)
    answer = blocks(first, *empties(limit - 1))
    answer[f"page_{limit + 1}"] = second_page(traveller_name="Anna Keller")
    out, _ = by_document(long_document(n), KIND, answer)
    assert names(out) == ALL_FOUR - {"traveller_name"}
    checked_by_a_person(out)


def reachable(limit: int, sizes: list[int]) -> int:
    """How many pages a call of at most `limit` bytes sends: the pages in order
    while the sum fits, and page 1 always."""
    sent, total = 0, 0
    for size in sizes:
        total += size
        if sent and total > limit:
            break
        sent += 1
    return sent


@pytest.mark.parametrize("offset", [-1, 0, 1])
@pytest.mark.parametrize("fits", [1, 2, 3])
def test_every_cut_point_of_the_byte_bound_sends_the_pages_that_fit(
    monkeypatch: pytest.MonkeyPatch, fits: int, offset: int
) -> None:
    data = long_document(3)
    sizes = [len(p.image) for p in omni.pages(data)]
    limit = sum(sizes[:fits]) + offset
    sent = reachable(limit, sizes)
    assert sent == (fits if offset >= 0 else max(1, fits - 1))
    monkeypatch.setattr(omni, "MAX_DOCUMENT_BYTES", limit)
    answer = blocks(answer_from(BOOKING), *empties(sent - 1))
    out, stub = by_document(data, KIND, answer)
    call = stub.requests[1]
    assert images_of(call) == list(page_images(3))[:sent]
    assert [p["text"] for p in parts(call) if p["type"] == "text"][1:] == [
        f"Page {k} of 3:" for k in range(1, sent + 1)
    ]
    schema = call["response_format"]["json_schema"]["schema"]
    assert schema["required"] == [f"page_{k}" for k in range(1, sent + 1)]
    assert len(stub.requests) == 2  # no call for the pages that did not fit
    assert names(out) == ALL_FOUR  # page 1 is always sent and always read
    if sent == 3:
        assert {f.confidence for f in out.fields} == {VERIFIED} and out.line_items
    else:
        assert f"the first {sent} of the 3 pages" in parts(call)[0]["text"]
        checked_by_a_person(out)


def test_a_byte_bound_of_zero_still_sends_page_1() -> None:
    sizes = [len(p.image) for p in omni.pages(long_document(3))]
    assert reachable(0, sizes) == 1


@pytest.mark.parametrize("sent", [1, 2])
def test_a_page_that_did_not_fit_is_not_read_even_when_the_model_names_it(
    monkeypatch: pytest.MonkeyPatch, sent: int
) -> None:
    data = long_document(3)
    sizes = [len(p.image) for p in omni.pages(data)]
    monkeypatch.setattr(omni, "MAX_DOCUMENT_BYTES", sum(sizes[:sent]))
    answer = blocks(answer_from(BOOKING, traveller_name=None), *empties(sent - 1))
    answer["page_3"] = second_page(traveller_name="Anna Keller")
    out, _ = by_document(data, KIND, answer)
    assert names(out) == ALL_FOUR - {"traveller_name"}
    checked_by_a_person(out)


def test_the_pixel_bound_still_applies_to_every_image_of_a_document_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(omni, "MAX_PIXELS", 200_000)  # an A4 page at 150 DPI: 2.2 MP
    writer = PdfWriter()
    writer.append(PdfReader(BOOKING))
    writer.add_blank_page(width=5000, height=2500)
    writer.append(PdfReader(CANCELLATION))
    out = BytesIO()
    writer.write(out)
    data = out.getvalue()
    _, page = by_page(data, KIND, *empties(3))
    _, document = by_document(data, KIND, blocks(*empties(3)))
    sent = images_of(document.requests[1])
    assert sent == [images_of(r)[0] for r in page.requests[1:]]
    sizes = []
    for _, png in sent:
        with Image.open(BytesIO(png)) as image:
            sizes.append(image.size)
    assert all(w * h <= 200_000 for w, h in sizes)
    assert abs(sizes[1][0] / sizes[1][1] - 2) < 0.02  # the whole page, not a crop
    assert abs(sizes[0][0] / sizes[0][1] - 595 / 842) < 0.02


# ------------------------------------------------- no fallback, bounded attempts

FAILURES = [
    ("500 three times", (500, 500, 500), 3, [2.0, 8.0]),
    ("a timeout three times", (timeout, timeout, timeout), 3, [2.0, 8.0]),
    ("429 three times", (429, 429, 429), 3, [2.0, 8.0]),
    (
        "429 with a wait three times",
        (((429, {"Retry-After": "5"}),) * 3),
        3,
        [5.0, 5.0],
    ),
    ("500, 429, 503", (500, 429, 503), 3, [2.0, 8.0]),
    ("400", (400,), 1, []),
    ("401", (401,), 1, []),
    ("404", (404,), 1, []),
    ("422", (422,), 1, []),
    ("a 200 with no content", (finished("", "stop"),), 1, []),
    ("a 200 with spaces only", (finished("  \n ", "stop"),), 1, []),
    (
        "a 200 with a null content",
        (httpx.Response(200, json={"choices": [{"message": {"content": None}}]}),),
        1,
        [],
    ),
    ("a 200 with no choice", (httpx.Response(200, json={"choices": []}),), 1, []),
    ("a 200 with a body that is no JSON", (httpx.Response(200, text="<html>"),), 1, []),
    ("a 200 with a body that is a list", (httpx.Response(200, json=[1, 2]),), 1, []),
]


@pytest.mark.parametrize(
    ("replies", "calls", "waits"),
    [f[1:] for f in FAILURES],
    ids=[f[0] for f in FAILURES],
)
@pytest.mark.parametrize("n", [2, 3])
def test_a_failed_document_call_gives_nothing_and_no_call_per_page(
    n: int,
    replies: tuple[Any, ...],
    calls: int,
    waits: list[float],
    caplog: pytest.LogCaptureFixture,
) -> None:
    out, stub = run("document", long_document(n), ANSWER, *replies)
    assert out.document_type == KIND and out.fields == [] and out.line_items == []
    assert len(stub.requests) == 1 + calls <= 1 + 1 + omni.RETRIES
    assert [len(images_of(r)) for r in stub.requests[1:]] == [n] * calls
    assert all(same_bytes(r, stub.requests[1]) for r in stub.requests[1:])
    assert stub.waits == waits
    assert "omni call failed" in caplog.text and "test-key-value" not in caplog.text


@pytest.mark.parametrize(
    ("replies", "waits"),
    [
        ((((429, {"Retry-After": "3"})), "ok"), [3.0]),
        ((429, "ok"), [2.0]),
        ((503, 502, "ok"), [2.0, 8.0]),
        ((timeout, "ok"), [2.0]),
        ((((429, {"Retry-After": "999"})), "ok"), [30.0]),
        ((((429, {"Retry-After": "0"})), "ok"), [0.0]),
        ((((429, {"Retry-After": "-4"})), "ok"), [0.0]),
        ((((429, {"Retry-After": "soon"})), "ok"), [2.0]),
        ((((429, {"Retry-After": "1e400"})), "ok"), [30.0]),
    ],
)
def test_a_document_call_waits_and_retries_as_a_page_call_and_then_reads_it(
    replies: tuple[Any, ...], waits: list[float]
) -> None:
    good = blocks(answer_from(BOOKING), nothing_on(T))
    replies = tuple(good if r == "ok" else r for r in replies)
    out, stub = run("document", long_document(2), ANSWER, *replies)
    assert names(out) == ALL_FOUR and stub.waits == waits
    assert len(stub.requests) == 1 + len(replies)
    assert all(same_bytes(r, stub.requests[1]) for r in stub.requests[1:])


@pytest.mark.parametrize("header", ["nan", "NaN", "-nan"])
def test_a_retry_after_that_is_not_a_number_is_never_a_wait_of_nan(header: str) -> None:
    """The note says the waits are those of a page call and bounded (0 to 30)."""
    good = blocks(answer_from(BOOKING), nothing_on(T))
    _, stub = run(
        "document", long_document(2), ANSWER, (429, {"Retry-After": header}), good
    )
    [wait] = stub.waits
    assert 0.0 <= wait <= omni.MAX_WAIT  # false for nan: every comparison is false


def test_a_document_call_never_makes_a_second_call_after_an_unusable_answer() -> None:
    for reply in ("not json", "", "[]", '{"page_1": 1}', finished("{}", "length")):
        out, stub = run("document", long_document(3), ANSWER, reply)
        assert out.fields == [] and out.line_items == []
        assert len(stub.requests) == 2, reply


# =========================================================== B. the answer parser

A_BLOCK = {"values": {"k": "A"}, "snippets": {}}
B_BLOCK = {"values": {"k": "B"}, "snippets": {}}
C_BLOCK = {"values": {"k": "C"}, "snippets": {}}
D_BLOCK = {"values": {"k": "D"}, "snippets": {}}


def raw(*pairs: tuple[str, Any]) -> str:
    """A JSON object from (key, value) pairs, a key may be given twice."""
    return "{" + ", ".join(f"{json.dumps(k)}: {json.dumps(v)}" for k, v in pairs) + "}"


def checked_shape(found: Any, count: int) -> tuple[list[Any], bool]:
    assert isinstance(found, tuple) and len(found) == 2
    found_blocks, exact = found
    assert isinstance(found_blocks, list) and len(found_blocks) == count
    assert type(exact) is bool
    return found_blocks, exact


# ------------------------------------------------------------ the keys of the blocks

WEIRD_KEYS = [
    "page_3",
    "page_4",
    "page_0",
    "page_-1",
    "page_1.5",
    "page_01",
    "page_02",
    "page_1 ",
    "page_2 ",
    " page_1",
    "PAGE_1",
    "Page_2",
    "page_true",
    "page_None",
    "page_null",
    "1",
    "2",
    "pages",
    "page",
    "",
    "page_",
    "page_+1",
    "page_1e0",
    "page_0x1",
    "page_１",  # a full-width digit
    "page_٢",  # an Arabic-Indic digit
    "page_1​",  # a zero-width space
    "page__1",
    "page-1",
    "page 1",
    "page_1\n",
]


def test_the_blocks_are_found_by_their_key_in_any_order() -> None:
    assert omni._blocks(raw(("page_1", A_BLOCK), ("page_2", B_BLOCK)), 2) == (
        [A_BLOCK, B_BLOCK],
        True,
    )
    assert omni._blocks(raw(("page_2", B_BLOCK), ("page_1", A_BLOCK)), 2) == (
        [A_BLOCK, B_BLOCK],
        True,
    )
    three = raw(("page_3", C_BLOCK), ("page_1", A_BLOCK), ("page_2", B_BLOCK))
    assert omni._blocks(three, 3) == ([A_BLOCK, B_BLOCK, C_BLOCK], True)
    assert omni._blocks(raw(("page_1", A_BLOCK)), 1) == ([A_BLOCK], True)


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (raw(("page_1", A_BLOCK)), ([A_BLOCK, None], False)),
        (raw(("page_2", B_BLOCK)), ([None, B_BLOCK], False)),
        ("{}", ([None, None], False)),
        (raw(("page_1", A_BLOCK), ("page_2", None)), ([A_BLOCK, None], True)),
        (raw(("page_1", None), ("page_2", None)), ([None, None], True)),
    ],
    ids=["no page_2", "no page_1", "empty object", "page_2 null", "both null"],
)
def test_a_missing_block_and_a_null_block_are_no_block(
    content: str, expected: tuple[list[Any], bool]
) -> None:
    """A null block names its page, so `exact` is true by the definition of the note
    (the answer names these pages and nothing else). `exact` is true here and the
    page is still not read: see the next test."""
    assert omni._blocks(content, 2) == expected


@pytest.mark.parametrize("key", WEIRD_KEYS)
def test_a_key_that_is_not_a_page_sent_is_never_a_block_and_makes_it_incomplete(
    key: str,
) -> None:
    extra = raw(("page_1", A_BLOCK), ("page_2", B_BLOCK), (key, C_BLOCK))
    assert omni._blocks(extra, 2) == ([A_BLOCK, B_BLOCK], False)
    instead = raw(("page_1", A_BLOCK), (key, C_BLOCK))
    assert omni._blocks(instead, 2) == ([A_BLOCK, None], False)
    first = raw((key, C_BLOCK), ("page_1", A_BLOCK), ("page_2", B_BLOCK))
    assert omni._blocks(first, 2) == ([A_BLOCK, B_BLOCK], False)
    only = raw((key, C_BLOCK))
    assert omni._blocks(only, 2) == ([None, None], False)


# Every key of WEIRD_KEYS takes one path in the parser (the test above). Through the
# whole reader, one key of each kind is enough.
SOME_KEYS = [
    "page_3",
    "page_0",
    "page_-1",
    "page_1.5",
    "page_01",
    "PAGE_1",
    "pages",
    "",
]
SOME_KEYS += ["page_１"]  # a full-width digit


@pytest.mark.parametrize("key", SOME_KEYS)
def test_the_content_of_a_key_that_is_not_a_page_is_never_stored(key: str) -> None:
    """The unknown block holds a perfect answer of the booking: page 1 would store
    four fields, page 2 two of them. Nothing of it is stored, whichever way the key
    stands (beside the pages, or in the place of page 2)."""
    empty = nothing_on(T)
    beside = raw(("page_1", empty), ("page_2", empty), (key, answer_from(BOOKING)))
    out, stub = by_document(long_document(2), KIND, beside)
    assert out.fields == [] and out.line_items == []
    instead = raw(("page_1", empty), (key, answer_from(BOOKING)))
    out, stub = by_document(long_document(2), KIND, instead)
    assert out.fields == [] and out.line_items == []
    assert len(stub.requests) == 2


@pytest.mark.parametrize("key", SOME_KEYS)
def test_a_key_that_is_not_a_page_sends_every_stored_field_to_a_person(
    key: str,
) -> None:
    good = answer_from(BOOKING)
    beside = raw(("page_1", good), ("page_2", nothing_on(T)), (key, nothing_on(T)))
    out, _ = by_document(long_document(2), KIND, beside)
    assert names(out) == ALL_FOUR and {f.source.page for f in out.fields} == {1}
    checked_by_a_person(out)
    instead = raw(("page_1", good), (key, nothing_on(T)))
    out, _ = by_document(long_document(2), KIND, instead)
    assert names(out) == ALL_FOUR and {f.source.page for f in out.fields} == {1}
    checked_by_a_person(out)


@pytest.mark.parametrize("key", ["page_1", "page_2"])
@pytest.mark.parametrize("where", ["first", "second", "last"])
def test_a_page_key_given_twice_makes_the_whole_answer_unusable(
    key: str, where: str
) -> None:
    other = "page_2" if key == "page_1" else "page_1"
    pairs = {
        "first": [(key, A_BLOCK), (key, B_BLOCK), (other, C_BLOCK)],
        "second": [(other, C_BLOCK), (key, A_BLOCK), (key, B_BLOCK)],
        "last": [(key, A_BLOCK), (other, C_BLOCK), (key, D_BLOCK)],
    }[where]
    assert omni._blocks(raw(*pairs), 2) == ([None, None], False)
    good = answer_from(BOOKING)
    pairs = [(k, good if v is A_BLOCK else nothing_on(T)) for k, v in pairs]
    out, stub = by_document(long_document(2), KIND, raw(*pairs))
    assert out.fields == [] and out.line_items == []
    assert len(stub.requests) == 2


def test_a_page_key_given_twice_with_the_same_block_is_still_unusable() -> None:
    content = raw(("page_1", A_BLOCK), ("page_2", B_BLOCK), ("page_1", A_BLOCK))
    assert omni._blocks(content, 2) == ([None, None], False)


def test_an_unknown_key_given_twice_makes_the_answer_unusable_undocumented() -> None:
    """The note says a PAGE key given twice makes the answer unusable. For another
    key the code does the same (it counts any key of the outer object)."""
    content = raw(
        ("page_1", A_BLOCK),
        ("page_2", B_BLOCK),
        ("page_3", C_BLOCK),
        ("page_3", D_BLOCK),
    )
    assert omni._blocks(content, 2) == ([None, None], False)


def test_a_key_given_twice_inside_a_block_keeps_the_last_one() -> None:
    inner = '{"values": {"k": 1}, "snippets": {}, "values": {"k": 2}}'
    content = '{"page_1": ' + inner + ', "page_2": {"values": {}, "snippets": {}}}'
    found, exact = omni._blocks(content, 2)
    assert found[0] == {"values": {"k": 2}, "snippets": {}} and exact is True
    nested = '{"page_1": {"values": {"a": 1, "a": 2, "a": 3}, "snippets": {}}}'
    assert omni._blocks(nested, 1) == ([{"values": {"a": 3}, "snippets": {}}], True)


def test_a_key_given_twice_inside_the_values_is_read_as_in_the_page_scope() -> None:
    """The note: as in the page mode today, the JSON parser keeps the last one."""
    text = json.dumps(answer_from(BOOKING))
    assert '"traveller_name": "Anna Keller"' in text
    twice = text.replace(
        '"traveller_name": "Anna Keller"',
        '"traveller_name": "Zed Zed", "traveller_name": "Anna Keller"',
    )
    page, _ = by_page(long_document(2), KIND, twice, json.dumps(nothing_on(T)))
    document, _ = by_document(
        long_document(2),
        KIND,
        '{"page_1": ' + twice + ', "page_2": ' + json.dumps(nothing_on(T)) + "}",
    )
    assert names(document) == ALL_FOUR == names(page)
    assert document.model_dump() == page.model_dump()
    assert {f.source.page for f in document.fields} == {1}


@pytest.mark.parametrize("order", [[1, 2, 3], [3, 2, 1], [2, 3, 1], [1, 3, 2]])
def test_the_order_of_three_blocks_does_not_change_the_extraction(
    order: list[int],
) -> None:
    data = long_document(3)
    answers = {
        1: answer_from(BOOKING, traveller_name=None),
        2: second_page(traveller_name="Anna Keller"),
        3: nothing_on(T),
    }
    pairs = [(f"page_{k}", answers[k]) for k in order]
    out, _ = by_document(data, KIND, raw(*pairs))
    straight, _ = by_document(data, KIND, blocks(*answers.values()))
    assert out.model_dump() == straight.model_dump()
    assert {f.name: f.source.page for f in out.fields}["traveller_name"] == 2
    assert {f.confidence for f in out.fields} == {VERIFIED}


def test_a_null_block_is_a_page_that_nobody_read() -> None:
    answer = raw(("page_1", answer_from(BOOKING)), ("page_2", None))
    out, _ = by_document(long_document(2), KIND, answer)
    assert names(out) == ALL_FOUR and {f.source.page for f in out.fields} == {1}
    checked_by_a_person(out)
    both = raw(("page_1", None), ("page_2", second_page(traveller_name="Anna Keller")))
    out, _ = by_document(long_document(2), KIND, both)
    assert names(out) == {"traveller_name"} and out.fields[0].source.page == 2
    checked_by_a_person(out)


def test_a_missing_block_is_never_taken_as_a_page_that_holds_nothing() -> None:
    out, _ = by_document(long_document(3), KIND, raw(("page_1", answer_from(BOOKING))))
    assert names(out) == ALL_FOUR
    checked_by_a_person(out)
    both = blocks(answer_from(BOOKING), nothing_on(T), nothing_on(T))
    complete, _ = by_document(long_document(3), KIND, both)
    assert {f.confidence for f in complete.fields} == {VERIFIED}
    assert len(complete.line_items) == 3


# ------------------------------------------------------------- the whole answer

VALID = json.dumps(blocks(answer_from(BOOKING), nothing_on(T)))
NOT_USABLE = [
    ("not json", "this is not json"),
    ("cut at 10 percent", VALID[: len(VALID) // 10]),
    ("cut at 50 percent", VALID[: len(VALID) // 2]),
    ("cut at 90 percent", VALID[: len(VALID) * 9 // 10]),
    ("one character short", VALID[:-1]),
    ("one character long of a brace", VALID + "}"),
    ("a list", json.dumps([blocks(answer_from(BOOKING), nothing_on(T))])),
    ("an empty list", "[]"),
    ("a number", "7"),
    ("a string", json.dumps(VALID)),
    ("true", "true"),
    ("false", "false"),
    ("null", "null"),
    ("NaN", "NaN"),
    ("an empty text", ""),
    ("spaces", "   \n\t  "),
    ("an empty object", "{}"),
    ("the blocks in a wrapper", '{"answer": ' + VALID + "}"),
    ("the blocks in a list under pages", '{"pages": [' + VALID + "]}"),
    ("the answer and a word", VALID + " Thanks"),
    ("the answer and an object", VALID + "\n{}"),
    ("a word and the answer", "Here it is: " + VALID),
    ("a fence that is not closed", "```json\n" + VALID),
    ("a fence closed with two marks", "```json\n" + VALID + "\n``"),
    ("a fence of another language", "```python\n" + VALID + "\n```"),
    ("a fence with no line break", "```json" + VALID + "```"),
    ("text before a fence", "Answer:\n```json\n" + VALID + "\n```"),
    ("text after a fence", "```json\n" + VALID + "\n```\nDone."),
    ("the byte order mark", "﻿" + VALID),
    ("single quotes", VALID.replace('"', "'")),
    ("a trailing comma", VALID[:-1] + ",}"),
]


@pytest.mark.parametrize(
    "content", [c for _, c in NOT_USABLE], ids=[n for n, _ in NOT_USABLE]
)
def test_an_answer_that_is_not_an_object_of_blocks_gives_no_block(content: str) -> None:
    assert omni._blocks(content, 2) == ([None, None], False)


@pytest.mark.parametrize(
    "content", [c for _, c in NOT_USABLE], ids=[n for n, _ in NOT_USABLE]
)
def test_an_answer_that_is_not_an_object_of_blocks_gives_no_field_and_one_call(
    content: str,
) -> None:
    out, stub = by_document(long_document(2), KIND, content)
    assert out.document_type == KIND
    assert out.fields == [] and out.line_items == []
    assert len(stub.requests) == 2


FENCED = [
    ("a json fence", "```json\n" + VALID + "\n```"),
    ("a plain fence", "```\n" + VALID + "\n```"),
    ("a fence and spaces around", "  \n```json\n" + VALID + "\n```  \n\n"),
    ("a fence with no last line break", "```json\n" + VALID + "```"),
    (
        "a fence over several lines",
        "```json\n" + json.dumps(json.loads(VALID), indent=2) + "\n```",
    ),
]


@pytest.mark.parametrize("content", [c for _, c in FENCED], ids=[n for n, _ in FENCED])
def test_one_json_fence_around_the_answer_is_accepted_as_in_the_page_scope(
    content: str,
) -> None:
    found, exact = omni._blocks(content, 2)
    assert found == [answer_from(BOOKING), nothing_on(T)] and exact is True
    out, _ = by_document(long_document(2), KIND, content)
    assert names(out) == ALL_FOUR and {f.confidence for f in out.fields} == {VERIFIED}
    assert len(out.line_items) == 3


def test_a_fence_with_windows_line_breaks_is_refused_undocumented() -> None:
    """The first line is "```json\r": the page scope does not take it either."""
    content = "```json\r\n" + VALID + "\r\n```"
    assert omni._blocks(content, 2) == ([None, None], False)


def test_the_whitespace_around_an_answer_does_not_matter() -> None:
    padded = "\n\n  " + VALID + "  \n"
    assert omni._blocks(padded, 2)[1] is True
    out, _ = by_document(long_document(2), KIND, padded)
    assert names(out) == ALL_FOUR


def test_a_cut_answer_never_gives_a_field_whatever_the_position_of_the_cut() -> None:
    for at in range(1, len(VALID), max(1, len(VALID) // 40)):
        found = omni._blocks(VALID[:at], 2)
        assert found == ([None, None], False), at


# -------------------------------------------------- a block that is not an answer


def edited(**values: Any) -> dict[str, Any]:
    answer = answer_from(BOOKING)
    answer["values"].update(values)
    return answer


def without_key(*keys: str) -> dict[str, Any]:
    answer = answer_from(BOOKING)
    for key in keys:
        del answer["values"][key]
    return answer


def with_snippets(snippets: Any) -> dict[str, Any]:
    return {"values": answer_from(BOOKING)["values"], "snippets": snippets}


def with_values(values: Any) -> dict[str, Any]:
    return {"values": values, "snippets": answer_from(BOOKING)["snippets"]}


def extra_top() -> dict[str, Any]:
    return {**answer_from(BOOKING), "note": "all fine", "page": 1}


# (id, the block for page 1, the fields stored, the number of line items)
NOTHING: set[str] = set()
BLOCKS = [
    ("a list", [1, 2], NOTHING, 0),
    ("a list of answers", [answer_from(BOOKING)], NOTHING, 0),
    ("a string", "values", NOTHING, 0),
    ("a number", 7, NOTHING, 0),
    ("a float", 1.5, NOTHING, 0),
    ("true", True, NOTHING, 0),
    ("null", None, NOTHING, 0),
    ("an empty object", {}, NOTHING, 0),
    ("a block that is a JSON text", json.dumps(answer_from(BOOKING)), NOTHING, 0),
    ("no values", {"snippets": answer_from(BOOKING)["snippets"]}, NOTHING, 0),
    ("no snippets", {"values": answer_from(BOOKING)["values"]}, NOTHING, 0),
    ("values as a list", with_values([1, 2]), NOTHING, 0),
    ("values as a string", with_values("x"), NOTHING, 0),
    ("values as null", with_values(None), NOTHING, 0),
    ("snippets as a list", with_snippets(["a"]), NOTHING, 0),
    ("snippets as a string", with_snippets("a"), NOTHING, 0),
    ("snippets as null", with_snippets(None), NOTHING, 0),
    ("an unknown key in values", edited(price=1840.0), ALL_FOUR, 3),
    ("a key beside values and snippets", extra_top(), ALL_FOUR, 3),
    (
        "a value of the wrong type",
        edited(trip_price="1840"),
        ALL_FOUR - {"trip_price"},
        3,
    ),
    (
        "a date of the wrong form",
        edited(departure_date="10.08.2026"),
        ALL_FOUR - {"departure_date"},
        3,
    ),
    ("a number for a text", edited(traveller_name=7), ALL_FOUR - {"traveller_name"}, 3),
    ("a blank text", edited(traveller_name="   "), ALL_FOUR - {"traveller_name"}, 3),
    (
        "a missing key in values",
        without_key("traveller_name"),
        ALL_FOUR - {"traveller_name"},
        3,
    ),
    (
        "two missing keys",
        without_key("trip_price", "departure_date"),
        {"booking_reference", "traveller_name"},
        3,
    ),
    ("a missing list of lines", without_key("line_items"), ALL_FOUR, 0),
    ("no key at all in values", with_values({}), NOTHING, 0),
    ("lines as a text", edited(line_items="x"), ALL_FOUR, 0),
    ("lines as an object", edited(line_items={"a": 1}), ALL_FOUR, 0),
    ("lines as null", edited(line_items=None), ALL_FOUR, 0),
    ("a line that is a number", edited(line_items=[1]), ALL_FOUR, 0),
    (
        "a line of the wrong type",
        edited(
            line_items=[
                {"description": 5, "quantity": 1.0, "unit_price": 1.0, "amount": 1.0}
            ]
        ),
        ALL_FOUR,
        0,
    ),
    (
        "a line with a missing key",
        edited(line_items=[{"description": "a"}]),
        ALL_FOUR,
        0,
    ),
    (
        "a line with an unknown key",
        edited(
            line_items=[
                {
                    "description": "a",
                    "quantity": 1.0,
                    "unit_price": 1.0,
                    "amount": 1.0,
                    "tax": 2,
                }
            ]
        ),
        ALL_FOUR,
        0,
    ),
    ("an empty list of lines", edited(line_items=[]), ALL_FOUR, 0),
]


@pytest.mark.parametrize("case", BLOCKS, ids=[b[0] for b in BLOCKS])
def test_a_block_that_is_not_a_page_answer_gives_what_the_page_scope_gives(
    case: tuple[str, Any, set[str], int],
) -> None:
    _, block, expected, lines = case
    data = long_document(2)
    page, _ = by_page(data, KIND, json.dumps(block), nothing_on(T))
    document, stub = by_document(data, KIND, blocks(block, nothing_on(T)))
    assert document.model_dump() == page.model_dump()
    assert len(stub.requests) == 2
    assert names(document) == expected
    assert len(document.line_items) == lines
    if expected:  # the page was read and the answer is complete: checked evidence
        assert {f.confidence for f in document.fields} == {VERIFIED}


@pytest.mark.parametrize("case", BLOCKS[::7], ids=[b[0] for b in BLOCKS[::7]])
def test_the_same_bad_block_as_the_second_page_gives_what_the_page_scope_gives(
    case: tuple[str, Any, set[str], int],
) -> None:
    _, block, _, _ = case
    data = long_document(2)
    first = answer_from(BOOKING, traveller_name=None)
    page, _ = by_page(data, KIND, first, json.dumps(block))
    document, _ = by_document(data, KIND, blocks(first, block))
    assert document.model_dump() == page.model_dump()
    assert {f.source.page for f in document.fields} <= {1, 2}


UNREAD = [b for b in BLOCKS if not b[2] and b[0] != "no key at all in values"]


@pytest.mark.parametrize("case", UNREAD, ids=[b[0] for b in UNREAD])
def test_a_page_that_is_not_read_sends_the_fields_of_the_other_pages_to_a_person(
    case: tuple[str, Any, set[str], int],
) -> None:
    """(`values: {}` is left out: every key is then missing and the page is read as a
    page that holds nothing, as in the page scope.)"""
    _, block, _, _ = case
    first = answer_from(BOOKING)
    out, _ = by_document(long_document(2), KIND, blocks(first, block))
    assert names(out) == ALL_FOUR
    checked_by_a_person(out)


def test_a_block_with_a_wrong_line_list_keeps_the_values_and_loses_the_list() -> None:
    """The lines of the page that failed make the list empty; the totals of the
    other pages stay in the document, each below 0.8 when the total is a field."""
    invoice = MOCK / "claim-2-property" / "repair-invoice.pdf"
    data = joined(invoice, invoice)
    good = answer_from(invoice)
    bad = answer_from(invoice, line_items="x")
    empty = nothing_on(DocumentType.repair_invoice)
    stub = Wire({"document_type": "repair_invoice"}, blocks(bad, empty))
    reader = OmniReader(
        client=httpx.Client(transport=httpx.MockTransport(stub.handle)),
        settings=OmniSettings(scope="document"),
    )
    out = reader.extract(doc(2), data, "property")
    page_stub = Wire({"document_type": "repair_invoice"}, json.dumps(bad), empty)
    page_reader = OmniReader(
        client=httpx.Client(transport=httpx.MockTransport(page_stub.handle)),
    )
    page_out = page_reader.extract(doc(2), data, "property")
    assert out.model_dump() == page_out.model_dump()
    assert out.line_items == []
    total = next(f for f in out.fields if f.name == "total")
    assert total.confidence <= UNVERIFIED
    assert good["values"]["total"] == total.value


# ---------------------------------------------------------------- finish reason


def with_finish(content: Any, reason: Any = "absent") -> httpx.Response:
    """A 200 answer with this content; the finish reason is left out when "absent"."""
    text = content if isinstance(content, str) else json.dumps(content)
    choice: dict[str, Any] = {"message": {"content": text}}
    if reason != "absent":
        choice["finish_reason"] = reason
    return httpx.Response(200, json={"choices": [choice]})


FULL = blocks(answer_from(BOOKING), nothing_on(T))


def test_the_finish_reason_length_sends_an_answer_that_is_whole_json_to_a_person() -> (
    None
):
    """The values are read; the answer is not complete: every field below the limit
    of the routing, no line item."""
    out, stub = by_document(long_document(2), KIND, with_finish(FULL, "length"))
    assert names(out) == ALL_FOUR and out.line_items == []
    assert all(f.confidence < LOW_CONFIDENCE for f in out.fields)
    assert len(stub.requests) == 2  # and no second call


def test_the_finish_reason_length_refuses_a_cut_answer_too() -> None:
    cut = json.dumps(FULL)[:-30]
    out, stub = by_document(long_document(2), KIND, with_finish(cut, "length"))
    assert out.fields == [] and out.line_items == []
    assert len(stub.requests) == 2


def test_a_cut_answer_with_the_finish_reason_stop_is_still_not_used() -> None:
    cut = json.dumps(FULL)[:-30]
    out, _ = by_document(long_document(2), KIND, with_finish(cut, "stop"))
    assert out.fields == [] and out.line_items == []


@pytest.mark.parametrize(
    "reason", ["stop", "absent", None, "content_filter", "tool_calls", "end_turn"]
)
def test_only_the_finish_reason_length_is_refused(reason: Any) -> None:
    out, stub = by_document(long_document(2), KIND, with_finish(FULL, reason))
    assert names(out) == ALL_FOUR and len(out.line_items) == 3
    assert {f.confidence for f in out.fields} == {VERIFIED}
    assert len(stub.requests) == 2


@pytest.mark.parametrize(
    "reason",
    ["LENGTH", "Length", "length ", " length", "", 5, ["length"], {"a": 1}, True, 0.5],
)
def test_a_finish_reason_that_is_not_the_text_length_is_not_refused_undocumented(
    reason: Any,
) -> None:
    """The note: "The endpoint reports the finish reason `length`". Any other value
    (another case, a number, a list) is not `length`: the answer is used."""
    out, _ = by_document(long_document(2), KIND, with_finish(FULL, reason))
    assert names(out) == ALL_FOUR and len(out.line_items) == 3


def test_the_finish_reason_of_the_page_scope_is_not_looked_at() -> None:
    """Only the document call reads the finish reason: a page call is as in #18."""
    answers = [
        with_finish(answer_from(BOOKING), "length"),
        with_finish(nothing_on(T), "length"),
    ]
    out, _ = by_page(long_document(2), KIND, *answers)
    assert names(out) == ALL_FOUR


def test_the_finish_reason_length_of_classify_is_not_looked_at_undocumented() -> None:
    classify = with_finish(ANSWER, "length")
    out, stub = run("document", long_document(2), classify, FULL)
    assert names(out) == ALL_FOUR and len(stub.requests) == 2


# ------------------------------------------------- the fields inside the blocks


def window(text: str, needle: str, size: int) -> str:
    """A part of the page text of `size` characters (spaces at the ends excluded)
    that holds the needle and is shown on the page."""
    at = text.index(needle)
    for start in range(max(0, at - size), at + 1):
        for end in range(at + len(needle), min(len(text), start + size + 40) + 1):
            part = text[start:end]
            if len(part) == size and part == part.strip() and omni._shown(part, text):
                return part
    raise AssertionError(f"no part of {size} characters around {needle}")


def booking_text() -> str:
    return omni.pages(BOOKING.read_bytes())[0].text


def with_snippet(name: str, snippet: Any) -> dict[str, Any]:
    answer = answer_from(BOOKING)
    answer["snippets"][name] = snippet
    return answer


def no_snippet(name: str) -> dict[str, Any]:
    answer = answer_from(BOOKING)
    del answer["snippets"][name]
    return answer


def snippet_for_null() -> dict[str, Any]:
    answer = answer_from(BOOKING, trip_price=None)  # the snippet of the price stays
    assert answer["snippets"]["trip_price"]
    return answer


def long_snippets() -> list[Any]:
    text = booking_text()
    return [
        ("a snippet of 120 characters", window(text, "1,840.00", 120), True),
        ("a snippet of 121 characters", window(text, "1,840.00", 121), False),
        ("a snippet of 300 characters", text[:300], False),
    ]


FIELD_CASES = [
    ("a snippet for a null value", snippet_for_null(), ALL_FOUR - {"trip_price"}),
    ("a value with no snippet", no_snippet("trip_price"), ALL_FOUR - {"trip_price"}),
    (
        "a snippet that is a number",
        with_snippet("trip_price", 1840),
        ALL_FOUR - {"trip_price"},
    ),
    (
        "a snippet that is a list",
        with_snippet("trip_price", ["CHF 1,840.00"]),
        ALL_FOUR - {"trip_price"},
    ),
    (
        "a snippet that is an object",
        with_snippet("trip_price", {"a": "CHF 1,840.00"}),
        ALL_FOUR - {"trip_price"},
    ),
    (
        "a snippet that is null",
        with_snippet("trip_price", None),
        ALL_FOUR - {"trip_price"},
    ),
    (
        "a snippet that is true",
        with_snippet("trip_price", True),
        ALL_FOUR - {"trip_price"},
    ),
    ("an empty snippet", with_snippet("trip_price", ""), ALL_FOUR - {"trip_price"}),
    (
        "a snippet of spaces",
        with_snippet("trip_price", "   \n "),
        ALL_FOUR - {"trip_price"},
    ),
    (
        "a snippet that is not on the page",
        with_snippet("trip_price", "CHF 1,840.01"),
        ALL_FOUR - {"trip_price"},
    ),
    (
        "a snippet of another field",
        with_snippet("trip_price", "Anna Keller"),
        ALL_FOUR - {"trip_price"},
    ),
    ("a snippet for an unknown field", with_snippet("price", "CHF 1,840.00"), ALL_FOUR),
    (
        "a snippet for an unknown field with no value",
        with_snippet("price", None),
        ALL_FOUR,
    ),
    (
        "all the snippets missing",
        {"values": answer_from(BOOKING)["values"], "snippets": {}},
        NOTHING,
    ),
]


@pytest.mark.parametrize("case", FIELD_CASES, ids=[c[0] for c in FIELD_CASES])
def test_a_field_with_no_good_snippet_is_not_stored_as_in_the_page_scope(
    case: tuple[str, dict[str, Any], set[str]],
) -> None:
    _, answer, expected = case
    data = long_document(2)
    page, _ = by_page(data, KIND, answer, nothing_on(T))
    document, _ = by_document(data, KIND, blocks(answer, nothing_on(T)))
    assert document.model_dump() == page.model_dump()
    assert names(document) == expected
    assert all(f.source.snippet or f.source.box for f in document.fields)


@pytest.mark.parametrize("case", long_snippets(), ids=[c[0] for c in long_snippets()])
def test_a_snippet_is_at_most_120_characters_in_a_document_call(
    case: tuple[str, str, bool],
) -> None:
    _, snippet, stored = case
    answer = with_snippet("trip_price", snippet)
    data = long_document(2)
    page, _ = by_page(data, KIND, answer, nothing_on(T))
    document, _ = by_document(data, KIND, blocks(answer, nothing_on(T)))
    assert document.model_dump() == page.model_dump()
    assert ("trip_price" in names(document)) is stored
    if stored:
        price = next(f for f in document.fields if f.name == "trip_price")
        assert price.source.snippet == snippet.strip() and price.source.page == 1
        assert len(price.source.snippet or "") == 120


def test_a_snippet_padded_with_spaces_is_measured_without_them_undocumented() -> None:
    padded = with_snippet("trip_price", " " * 200 + "CHF 1,840.00" + " " * 200)
    out, _ = by_document(long_document(2), KIND, blocks(padded, nothing_on(T)))
    price = next(f for f in out.fields if f.name == "trip_price")
    assert price.source.snippet == "CHF 1,840.00"


def test_the_snippet_of_one_block_is_never_used_for_a_value_of_another_block() -> None:
    """A snippet is looked up in the snippets of its own block."""
    first = answer_from(BOOKING)
    del first["snippets"]["trip_price"]
    second = nothing_on(T)
    second["snippets"]["trip_price"] = "CHF 1,840.00"  # no value in this block
    out, _ = by_document(long_document(2), KIND, blocks(first, second))
    assert names(out) == ALL_FOUR - {"trip_price"}


def test_a_value_given_by_the_second_block_is_checked_on_the_text_of_its_own_page() -> (
    None
):
    """The snippet "CHF 1,840.00" is on page 1 only: given in block 2, never stored,
    and block 1 (which has no price) is not asked to hold it."""
    first = answer_from(BOOKING, trip_price=None)
    second = nothing_on(T)
    second["values"]["trip_price"] = 1840.0
    second["snippets"]["trip_price"] = "CHF 1,840.00"
    out, _ = by_document(long_document(2), KIND, blocks(first, second))
    assert "trip_price" not in names(out)


@pytest.mark.parametrize(
    ("value", "snippet"),
    [("Anna Keller", "Anna Keller"), ("WPR-482913", "WPR-482913")],
)
def test_a_value_that_both_pages_show_keeps_the_page_of_the_first_block(
    value: str, snippet: str
) -> None:
    field = "traveller_name" if value == "Anna Keller" else "booking_reference"
    first = nothing_on(T)
    first["values"][field] = value
    first["snippets"][field] = snippet
    out, _ = by_document(
        long_document(2), KIND, blocks(first, second_page(**{field: value}))
    )
    [stored] = out.fields
    assert stored.source.page == 1 and stored.confidence == VERIFIED


# ------------------------------------------------------- the parser never raises

DEEP = 900  # the limit of the recursion of Python is 1000


def nested(depth: int) -> str:
    return "[" * depth + "]" * depth


def nested_objects(depth: int) -> str:
    return '{"page_1": ' + '{"a": ' * depth + "1" + "}" * depth + "}"


ADVERSARIAL = [
    ("deep lists", nested(100)),
    ("deep lists, 500", nested(500)),
    ("deep lists in a block", '{"page_1": ' + nested(300) + "}"),
    ("deep objects", nested_objects(100)),
    ("deep objects, 400", nested_objects(400)),
    ("a very long key", json.dumps({"k" * 1_000_000: 1})),
    ("a very long page key", json.dumps({"page_1" + "1" * 100_000: 1})),
    ("many keys", json.dumps({f"k{i}": i for i in range(50_000)})),
    (
        "many unknown keys beside pages",
        json.dumps({"page_1": A_BLOCK, **{f"p{i}": 1 for i in range(20_000)}}),
    ),
    (
        "unicode keys",
        json.dumps(
            {"頁_1": A_BLOCK, "page_１": B_BLOCK, "página_2": C_BLOCK},
            ensure_ascii=False,
        ),
    ),
    (
        "an emoji key",
        json.dumps({"page_1": A_BLOCK, "📄": B_BLOCK}, ensure_ascii=False),
    ),
    ("a lone surrogate", '{"page_1": "\\ud800", "\\udc00": 1}'),
    ("a nul character", '{"page_1": "a\\u0000b", "page_2\\u0000": 1}'),
    ("a raw nul character", '{"page_1": 1}\x00'),
    ("NaN", '{"page_1": NaN, "page_2": [NaN, Infinity, -Infinity]}'),
    ("NaN as a key value", '{"NaN": NaN}'),
    ("an exponent that is infinite", '{"page_1": 1e999, "page_2": -1e999}'),
    ("a number as a key", "{1: 2}"),
    ("a number in a string as a key", '{"1": 2, "2": 3, "1.5": 4}'),
    ("a huge integer", '{"page_1": ' + "9" * 5000 + "}"),
    ("a huge negative integer", '{"page_1": -' + "9" * 5000 + "}"),
    (
        "duplicate keys at three levels",
        '{"page_1": {"a": {"b": 1, "b": 2}, "a": 3}, "page_2": {"x": 1, "x": 2}}',
    ),
    (
        "duplicate keys, only below",
        '{"page_1": {"a": 1, "a": 2}, "page_2": {"a": 1, "a": 2}}',
    ),
    ("duplicate keys on top and below", '{"page_1": {"a": 1, "a": 2}, "page_1": 3}'),
    ("duplicate unicode keys", '{"p\\u0061ge_1": 1, "page_1": 2}'),
    ("an escaped page key", '{"p\\u0061ge_1": {"values": {}, "snippets": {}}}'),
    ("a null key text", '{"null": null, "true": true}'),
    ("a list of pages", '[{"page_1": 1}, {"page_2": 2}]'),
    ("an object in a list in an object", '{"page_1": [{"page_1": {"page_1": 1}}]}'),
    ("an empty key twice", '{"": 1, "": 2}'),
    ("a fence only", "```"),
    ("two fences", "```json\n```json\n{}\n```\n```"),
    ("a fence and nothing", "```json\n```"),
    ("a long line", "x" * 2_000_000),
    ("a lone brace", "{"),
    ("a lone quote", '"'),
    ("a comment", '{"page_1": 1} // done'),
    ("a bom and an answer", "﻿" + VALID),
    ("a form feed", "\x0c" + VALID),
    ("a non-breaking space around", " " + VALID + " "),
]


@pytest.mark.parametrize("count", [2])
@pytest.mark.parametrize(
    "content", [c for _, c in ADVERSARIAL], ids=[n for n, _ in ADVERSARIAL]
)
def test_blocks_never_raises_and_always_gives_a_list_of_the_asked_length(
    content: str, count: int
) -> None:
    found, exact = checked_shape(omni._blocks(content, count), count)
    if exact:  # the flag is true only for an answer that names these pages
        assert content.strip().startswith(("{", "```"))
        assert all(
            b is None or isinstance(b, dict | list | str | int | float | bool)
            for b in found
        )


@pytest.mark.parametrize("count", [1, 2, 3, 10])
def test_blocks_of_nothing_is_no_block(count: int) -> None:
    assert omni._blocks(None, count) == ([None] * count, False)
    assert omni._blocks("", count) == ([None] * count, False)


@pytest.mark.parametrize("count", [1, 2, 10])
def test_the_flag_is_true_only_for_an_answer_that_names_every_page_and_nothing_else(
    count: int,
) -> None:
    names_ = [f"page_{k}" for k in range(1, count + 1)]
    exact = {n: A_BLOCK for n in names_}
    assert omni._blocks(json.dumps(exact), count) == ([A_BLOCK] * count, True)
    for key in (*names_, "page_0", f"page_{count + 1}", "x"):
        mutated = {**exact, key: B_BLOCK} if key not in exact else {**exact}
        if key in exact:
            del mutated[key]
        found, flag = omni._blocks(json.dumps(mutated), count)
        assert flag is False, key


@pytest.mark.parametrize(
    "depth",
    [DEEP, 3_000, 100_000],
)
def test_blocks_never_raises_for_a_very_deep_answer(depth: int) -> None:
    for content in (nested(depth), nested_objects(depth)):
        checked_shape(omni._blocks(content, 2), 2)


def test_extract_never_raises_for_a_very_deep_answer() -> None:
    out, _ = by_document(long_document(2), KIND, nested(100_000))
    assert out.fields == []


def test_a_deep_answer_within_reason_gives_no_block_and_no_field() -> None:
    out, _ = by_document(long_document(2), KIND, nested(500))
    assert out.fields == [] and out.line_items == []
    out, _ = by_document(long_document(2), KIND, nested_objects(200))
    assert out.fields == [] and out.line_items == []


def test_an_answer_with_nan_in_a_block_is_one_page_that_is_not_read() -> None:
    content = (
        '{"page_1": '
        + json.dumps(answer_from(BOOKING)).replace(
            '"trip_price": 1840.0', '"trip_price": NaN'
        )
        + ', "page_2": '
        + json.dumps(nothing_on(T))
        + "}"
    )
    assert "NaN" in content
    out, _ = by_document(long_document(2), KIND, content)
    assert "trip_price" not in names(out)


def test_what_the_parser_returns_is_never_the_content_of_a_stored_field_by_magic() -> (
    None
):
    """A value in a key that is not a page, put in the one place where a sloppy
    parser would look for it (the text of the key itself), is never stored."""
    sneaky = raw(
        ("page_1", nothing_on(T)),
        ("page_2", nothing_on(T)),
        ("traveller_name", "Anna Keller"),
        ("values", answer_from(BOOKING)["values"]),
        ("snippets", answer_from(BOOKING)["snippets"]),
    )
    out, _ = by_document(long_document(2), KIND, sneaky)
    assert out.fields == [] and out.line_items == []


def test_an_answer_that_is_one_page_answer_and_not_blocks_gives_no_field() -> None:
    """The answer of a page call given to a document call: no `page_k` key at all."""
    out, _ = by_document(long_document(2), KIND, answer_from(BOOKING))
    assert out.fields == [] and out.line_items == []
    assert omni._blocks(json.dumps(answer_from(BOOKING)), 2) == ([None, None], False)


def test_unverified_is_below_the_routing_threshold() -> None:
    """The cap of a field that a person checks is under the routing threshold."""
    assert UNVERIFIED < LOW_CONFIDENCE <= VERIFIED


def test_a_key_that_is_not_one_of_three_pages_is_never_a_block_of_three() -> None:
    three = {"page_1": A_BLOCK, "page_2": B_BLOCK, "page_3": C_BLOCK}
    assert omni._blocks(json.dumps(three), 3) == ([A_BLOCK, B_BLOCK, C_BLOCK], True)
    for key in ("page_4", "page_0", "page_03", "page_3 ", "Page_3", "page_-3"):
        found = omni._blocks(json.dumps({**three, key: D_BLOCK}), 3)
        assert found == ([A_BLOCK, B_BLOCK, C_BLOCK], False), key
        less = {k: v for k, v in three.items() if k != "page_3"}
        found = omni._blocks(json.dumps({**less, key: D_BLOCK}), 3)
        assert found == ([A_BLOCK, B_BLOCK, None], False), key


@pytest.mark.parametrize(
    ("answer", "page", "complete"),
    [
        (lambda a: blocks(a, nothing_on(DocumentType.medical_certificate)), "ok", True),
        (lambda a: {"page_1": a}, "missing block", False),
        (lambda a: blocks(a, None), "null block", False),
        (
            lambda a: {
                **blocks(a, nothing_on(DocumentType.medical_certificate)),
                "x": a,
            },
            "extra key",
            False,
        ),
        (lambda a: blocks(a, [1]), "bad block", False),
    ],
    ids=["complete", "missing block", "null block", "extra key", "bad block"],
)
def test_the_policy_holds_for_a_type_with_no_line_items(
    answer: Any, page: str, complete: bool
) -> None:
    """The confidence cap does not depend on the list of lines: a medical certificate
    has none, and every stored field is still sent to a person."""
    data = joined(CERTIFICATE, CERTIFICATE)
    first = answer_from(CERTIFICATE)
    out, stub = run(
        "document", data, {"document_type": "medical_certificate"}, answer(first)
    )
    assert {f.name for f in out.fields} == {"patient_name", "cause", "unfit_from"}
    assert out.line_items == [] and len(stub.requests) == 2
    if complete:
        assert {f.confidence for f in out.fields} == {VERIFIED}
    else:
        assert all(f.confidence < LOW_CONFIDENCE for f in out.fields), page


def test_the_log_of_a_refused_answer_holds_no_value_no_snippet_and_no_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cut = json.dumps(FULL)[:-30]
    out, _ = by_document(long_document(2), KIND, with_finish(cut, "length"))
    assert out.fields == [] and "cut at the token limit" in caplog.text
    assert "test-key-value" not in caplog.text
    assert "Anna Keller" not in caplog.text and "WPR-482913" not in caplog.text
    assert "1,840" not in caplog.text
