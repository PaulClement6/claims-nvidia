"""The settings of OmniReader (issue #20), with a stubbed HTTP client: no key, no
network. The settings of #18 must send the requests of #18, byte for byte: the
saved reports of #19 are the control of the tuning only then."""

import functools
import hashlib
import json
import logging
import re
from io import BytesIO
from typing import Any

import httpx2 as httpx
import pypdfium2 as pdfium  # type: ignore[import-untyped]
import pytest
from PIL import Image
from pypdf import PdfReader

from app.demo import NOT_KIT
from app.docintel import omni
from app.docintel.omni import OmniReader
from app.docintel.schemas import SCHEMAS
from app.models import DocumentType
from app.workflows import WORKFLOWS
from tests.reader_contract import MOCK
from tests.test_omni_reader import (
    ALL_FOUR,
    BOOKING,
    URL,
    Stub,
    answer_from,
    doc,
    image_of,
)

SCHEMAS_BOOKING = SCHEMAS[DocumentType.booking_confirmation]
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVIDIA_KEY", "test-key-value")
    monkeypatch.setenv("VLM_BASE_URL", URL)
    monkeypatch.setenv("VLM_MODEL", "omni-test")


def requests_of(reader: OmniReader, stub: Stub) -> list[dict[str, Any]]:
    """The bodies of the classify call and the page call for the booking."""
    reader.extract(doc(), BOOKING.read_bytes(), "travel")
    return stub.requests


def without_image(body: dict[str, Any]) -> dict[str, Any]:
    """The body with the image replaced by a mark: the rest must not change."""
    copy: dict[str, Any] = json.loads(json.dumps(body))
    for part in copy["messages"][0]["content"]:
        if part["type"] == "image_url":
            part["image_url"]["url"] = "<image>"
    return copy


def sha(body: Any) -> str:
    return hashlib.sha256(json.dumps(body).encode()).hexdigest()


def stub_for_booking() -> Stub:
    return Stub({"document_type": "booking_confirmation"}, answer_from(BOOKING))


def test_the_settings_of_issue_18_send_the_requests_of_issue_18() -> None:
    stub = stub_for_booking()
    classify, page = requests_of(stub.reader(), stub)

    # the order of the keys is the order of the bytes sent
    order = [
        "model",
        "messages",
        "temperature",
        "max_tokens",
        "chat_template_kwargs",
        "response_format",
    ]
    assert list(classify) == order and list(page) == order
    assert sha(without_image(classify)) == CLASSIFY_SHA
    assert sha(without_image(page)) == PAGE_SHA
    # the image: the whole page rendered at 150 DPI, pixel for pixel
    expected = pdfium.PdfDocument(BOOKING.read_bytes())[0].render(scale=150 / 72)
    for body in (classify, page):
        mime, png = image_of(body)
        assert mime == "image/png"
        with Image.open(BytesIO(png)) as sent:
            assert sent.tobytes() == expected.to_pil().tobytes()


def reader_with(stub: Stub, **settings: Any) -> OmniReader:
    client = httpx.Client(transport=httpx.MockTransport(stub.handle))
    return OmniReader(client=client, settings=omni.OmniSettings(**settings))


def test_the_default_settings_are_the_settings_of_issue_18() -> None:
    assert omni.OmniSettings() == omni.ISSUE_18
    stub = stub_for_booking()
    requests_of(reader_with(stub), stub)
    assert [sha(without_image(b)) for b in stub.requests] == [CLASSIFY_SHA, PAGE_SHA]


@pytest.mark.parametrize("temperature", [0.0, 0.6])
def test_the_temperature_reaches_both_calls(temperature: float) -> None:
    stub = stub_for_booking()
    requests_of(reader_with(stub, temperature=temperature), stub)
    assert [b["temperature"] for b in stub.requests] == [temperature] * 2


@pytest.mark.parametrize(("dpi", "size"), [(72, (595, 842)), (200, (1654, 2339))])
def test_the_dpi_sets_the_size_of_the_page_image(
    dpi: int, size: tuple[int, int]
) -> None:
    stub = stub_for_booking()
    requests_of(reader_with(stub, dpi=dpi), stub)
    for body in stub.requests:  # classify renders page 1 at the same DPI
        with Image.open(BytesIO(image_of(body)[1])) as sent:
            assert abs(sent.size[0] - size[0]) <= 1
            assert abs(sent.size[1] - size[1]) <= 1


def test_the_dpi_does_not_change_a_photo() -> None:
    jpg = MOCK / "hard" / "kit-anna-luggage-receipt-jacket-shoes-photo.jpg"
    stub = Stub({"document_type": "receipt"})
    reader_with(stub, dpi=72).classify(doc(), jpg.read_bytes())
    assert image_of(stub.requests[0]) == ("image/jpeg", jpg.read_bytes())


def test_thinking_and_its_budget_reach_both_calls() -> None:
    stub = stub_for_booking()
    reader = reader_with(stub, thinking=True, reasoning_budget=512, max_tokens=3584)
    requests_of(reader, stub)
    for body in stub.requests:
        assert body["chat_template_kwargs"] == {"enable_thinking": True}
        assert body["reasoning_budget"] == 512 and body["max_tokens"] == 3584
        assert body["response_format"]["json_schema"]["strict"] is True


def test_thinking_with_no_budget_sends_no_budget() -> None:
    stub = stub_for_booking()
    requests_of(reader_with(stub, thinking=True), stub)
    assert all("reasoning_budget" not in b for b in stub.requests)
    assert all(b["chat_template_kwargs"]["enable_thinking"] for b in stub.requests)


@pytest.mark.parametrize(
    "bad",
    [
        {"reasoning_budget": 512},  # a budget needs thinking
        {"thinking": True, "reasoning_budget": 2048, "max_tokens": 2048},
        {"thinking": True, "reasoning_budget": 0},
        {"temperature": -0.1},
        {"temperature": 2.5},
        {"dpi": 10},
        {"dpi": 1000},
        {"max_tokens": 0},
        {"max_tokens": omni.MAX_TOKENS_LIMIT + 1},
        {"timeout": 0},
        {"timeout": float("nan")},
        {"timeout": float("inf")},
        {"temperature": float("nan")},
        {"prompt": "v9"},
    ],
)
def test_settings_that_make_no_sense_are_refused(bad: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        omni.OmniSettings(**bad)


def test_the_largest_max_tokens_of_the_api_is_accepted() -> None:
    assert omni.OmniSettings(max_tokens=omni.MAX_TOKENS_LIMIT).max_tokens == 65536


@pytest.mark.parametrize(
    "bad",
    [
        {"dpi": 150.0},  # a float dpi would give a second config id for one setting
        {"dpi": True},
        {"max_tokens": 2048.0},
        {"thinking": "off"},  # a non-empty text is true: thinking would be on
        {"thinking": 1},
        {"thinking": True, "reasoning_budget": 512.0},
        {"temperature": True},
        {"temperature": "0.2"},
        {"timeout": "90"},
        {"prompt": 1},
    ],
)
def test_settings_of_the_wrong_type_are_refused(bad: dict[str, Any]) -> None:
    with pytest.raises(TypeError):
        omni.OmniSettings(**bad)


def test_the_timeout_is_the_timeout_of_the_client() -> None:
    reader = OmniReader(settings=omni.OmniSettings(timeout=240.0))
    assert reader._client.timeout.read == 240.0


# ---------------------------------------------------------------- prompt variants


def page_call(prompt: str, answer: dict[str, Any] | None = None) -> tuple[Any, Any]:
    """The extraction and the body of the page call for the booking."""
    stub = Stub(
        {"document_type": "booking_confirmation"}, answer or answer_from(BOOKING)
    )
    out = reader_with(stub, prompt=prompt).extract(doc(), BOOKING.read_bytes(), "x")
    return out, stub.requests[1]


def text_of(body: dict[str, Any]) -> str:
    return str(body["messages"][0]["content"][0]["text"])


def schema_of(body: dict[str, Any]) -> dict[str, Any]:
    form = body["response_format"]
    assert form["type"] == "json_schema" and form["json_schema"]["strict"] is True
    return dict(form["json_schema"]["schema"])


def no_descriptions(schema: Any) -> Any:
    """The schema with no description text. `description` is also a field of a
    line item: that one is a schema (a dict), not a text, and it stays."""
    if isinstance(schema, dict):
        return {
            k: no_descriptions(v)
            for k, v in schema.items()
            if not (k == "description" and isinstance(v, str))
        }
    if isinstance(schema, list):
        return [no_descriptions(v) for v in schema]
    return schema


@pytest.mark.parametrize("prompt", omni.PROMPTS)
def test_every_prompt_keeps_the_strict_schema_and_asks_for_snippets(
    prompt: str,
) -> None:
    out, body = page_call(prompt)
    schema = schema_of(body)
    assert schema["required"] == ["values", "snippets"]
    assert no_descriptions(schema) == omni.answer_schema(SCHEMAS_BOOKING)
    assert "snippets" in text_of(body)
    # the perfect answer: every field stored, with its snippet checked on the page
    assert {f.name for f in out.fields} == ALL_FOUR
    assert all(f.confidence == omni.VERIFIED for f in out.fields)


@pytest.mark.parametrize("prompt", omni.PROMPTS)
def test_every_prompt_keeps_the_check_of_the_snippet(prompt: str) -> None:
    answer = answer_from(BOOKING, booking_reference="WPR-999999")
    answer["snippets"]["booking_reference"] = "WPR-999999"  # not on the page
    out, _ = page_call(prompt, answer)
    assert {f.name for f in out.fields} == ALL_FOUR - {"booking_reference"}


def test_schema_only_keeps_the_form_of_the_answer_and_drops_the_rules() -> None:
    _, v1 = page_call("v1")
    _, bare = page_call("schema-only")
    assert schema_of(bare) == schema_of(v1)
    assert json.dumps(SCHEMAS_BOOKING.model_json_schema()) in text_of(bare)
    for rule in ("never guess", "Dates are", "`line_items` lists"):
        assert rule in text_of(v1) and rule not in text_of(bare)


def test_schema_only_keeps_the_rule_of_the_snippets_of_v1() -> None:
    """The variant measures the rules of the values only. What a source must be
    (the exact text, as short as possible) is the same in every prompt."""
    _, v1 = page_call("v1")
    _, bare = page_call("schema-only")
    rule = [line for line in text_of(v1).splitlines() if "`snippets` maps" in line]
    assert len(rule) == 1 and "character by character" in rule[0]
    assert rule[0] in text_of(bare).splitlines()


def test_described_is_v1_with_a_description_of_each_field() -> None:
    _, v1 = page_call("v1")
    _, described = page_call("described")
    values = schema_of(described)["properties"]["values"]
    line = schema_of(described)["$defs"]["LineItem"]
    for part in (values, line):
        assert all(p["description"] for p in part["properties"].values())
    sent = omni.values_schema(SCHEMAS_BOOKING, described=True)
    assert no_descriptions(sent) == SCHEMAS_BOOKING.model_json_schema()
    # the same text as v1, with the described schema in place of the plain one
    assert text_of(described) == text_of(v1).replace(
        json.dumps(SCHEMAS_BOOKING.model_json_schema()), json.dumps(sent)
    )
    assert DESCRIPTION_OF_TRIP_PRICE in text_of(described)


DESCRIPTION_OF_TRIP_PRICE = "The total price of the trip that the traveller paid."


def test_every_field_of_every_schema_has_one_description() -> None:
    names: set[str] = set()
    for schema in SCHEMAS.values():
        values = schema.model_json_schema()
        for part in [values, *values.get("$defs", {}).values()]:
            names |= set(part["properties"])
    assert names == set(omni.DESCRIPTIONS)


def test_the_cause_is_described_with_the_causes_of_the_workflows() -> None:
    causes = {c for w in WORKFLOWS.values() for c in w.all_causes()}
    assert all(c in omni.DESCRIPTIONS["cause"] for c in causes)


def kit_values() -> tuple[set[str], set[float], set[str]]:
    """Texts, amounts and dates of every answer key of the kit."""
    texts, amounts, dates = set[str](), set[float](), set[str]()
    for path in MOCK.rglob("*.expected.json"):
        if path.relative_to(MOCK).parts[0] in NOT_KIT:
            continue  # N1 (#125) is not the kit; its n5 has "Price of the trip"
        key = json.loads(path.read_text(encoding="utf-8"))
        values = [f["value"] for f in key["fields"]]
        values += [v for line in key.get("line_items", []) for v in line.values()]
        for v in values:
            if isinstance(v, int | float) and not isinstance(v, bool):
                amounts.add(round(float(v), 2))
            elif isinstance(v, str) and DATE.fullmatch(v):
                dates.add(v)
            elif isinstance(v, str):
                texts.add(" ".join(v.split()).casefold())
    return texts, amounts, dates


def test_no_description_copies_a_value_of_the_kit() -> None:
    """A description says what a field means; it never gives an answer. The only
    kit words allowed are the causes of the workflows, the words of the business."""
    texts, _, _ = kit_values()
    causes = {c for w in WORKFLOWS.values() for c in w.all_causes()}
    for name, text in omni.DESCRIPTIONS.items():
        found = {t for t in texts if t in text.casefold()}
        assert found <= causes, (name, found)


def test_the_example_shares_no_value_with_the_answer_keys() -> None:
    texts, amounts, dates = kit_values()
    page = " ".join(omni.EXAMPLE_TEXT.split()).casefold()
    values = omni.EXAMPLE_ANSWER["values"]
    assert not {t for t in texts if len(t) > 3 and t in page}
    numbers = [values["total"]] + [
        line[k] for line in values["line_items"] for k in ("unit_price", "amount")
    ]
    assert not {round(n, 2) for n in numbers} & amounts
    assert values["invoice_date"] not in dates


# A word: letters and digits, with . , : / ' - inside it (09.03.2026, QM-30741).
WORD = re.compile(r"[0-9a-zà-ÿ]+(?:[.,:/'-][0-9a-zà-ÿ]+)*")
# The words of the form of any invoice. The example may share these with the kit;
# every other name of the example (person, company, street, town) may not.
FORM_WORDS = {"CHF", "Date", "Dr", "GmbH", "Invoice", "Patient", "Total"}


@functools.cache
def kit_text() -> tuple[frozenset[str], frozenset[tuple[str, str, str]]]:
    """The words of the kit, and each run of 3 words: every JSON of data/mock (the
    answer keys, the customers, the index), the text layer of every mock PDF, the
    generator of the documents (scripts/mockdocs) and docs/MOCK-DATA.md. A name can
    be on a page and not in an answer key: the doctor, the street, the town."""
    texts = [p.read_text(encoding="utf-8") for p in sorted(MOCK.rglob("*.json"))]
    logging.getLogger("pypdf").setLevel(logging.ERROR)  # the mock PDFs are valid
    for path in sorted(MOCK.rglob("*.pdf")):
        texts += [page.extract_text() or "" for page in PdfReader(path).pages]
    texts += [p.read_text(encoding="utf-8") for p in sorted(MOCKDOCS.glob("*.py"))]
    texts.append((MOCK.parents[1] / "docs" / "MOCK-DATA.md").read_text("utf-8"))
    words: set[str] = set()
    runs: set[tuple[str, str, str]] = set()
    for text in texts:
        found = WORD.findall(text.casefold())
        words |= set(found)
        runs |= set(zip(found, found[1:], found[2:], strict=False))
    return frozenset(words), frozenset(runs)


MOCKDOCS = MOCK.parents[1] / "scripts" / "mockdocs"


def test_the_kit_text_holds_the_names_of_the_kit() -> None:
    """The check below finds a name of the kit (this fails if the text is empty)."""
    words, runs = kit_text()
    assert {"lindenhof", "brunner", "jonas"} <= words
    assert ("physiotherapy", "session", "30") in runs


def test_the_example_names_no_person_place_or_number_of_the_kit() -> None:
    """Each name of the example (a word with a capital letter that is not a word of
    the form) and each number of 4 characters or more (a postcode, an identifier, a
    date, an amount) is invented: it is in no text of the kit. The small numbers
    (a quantity, a house number) and the common words are not checked."""
    words, _ = kit_text()
    names = set(re.findall(r"\b[A-ZÄÖÜ][\w-]+", omni.EXAMPLE_TEXT)) - FORM_WORDS
    numbers = {
        w
        for w in WORD.findall(omni.EXAMPLE_TEXT.casefold())
        if len(w) >= 4 and any(c.isdigit() for c in w)
    }
    assert len(names) >= 8 and len(numbers) >= 6  # the check sees the example
    assert not {n for n in names if n.casefold() in words}
    assert not numbers & words
    assert omni.EXAMPLE_ANSWER["values"]["invoice_date"] not in words


def test_the_example_copies_no_text_of_the_kit() -> None:
    """No run of 3 words of the example page is in a text of the kit: no line of a
    kit document is copied, even with other numbers."""
    _, runs = kit_text()
    for line in omni.EXAMPLE_TEXT.splitlines():
        found = WORD.findall(line.casefold())
        assert not set(zip(found, found[1:], found[2:], strict=False)) & runs, line


def test_the_example_ends_by_sending_the_model_back_to_its_page() -> None:
    _, example = page_call("example")
    last = text_of(example).splitlines()[-1]
    assert last == omni.EXAMPLE_END
    assert "example" in last and "image" in last


def test_the_example_is_a_right_answer_for_its_page() -> None:
    """The example teaches what the checks accept: every value is valid and its
    snippet is on the page of the example and holds the value."""
    page = omni.Page(b"", "image/png", (1, 1), omni.EXAMPLE_TEXT)
    read = omni._read(SCHEMAS[DocumentType.medical_invoice], omni.EXAMPLE_ANSWER)
    assert read is not None
    values, snippets, refused = read
    assert not refused
    stored = [
        omni._field("D", 1, page, name, getattr(values, name), snippets)
        for name in type(values).model_fields
        if name != "line_items"
    ]
    assert all(f is not None and f.confidence == omni.VERIFIED for f in stored)
    lines = omni.EXAMPLE_ANSWER["values"]["line_items"]
    assert lines and all(omni._on_page(line, omni.EXAMPLE_TEXT) for line in lines)


def test_example_is_described_with_the_example_at_the_end() -> None:
    _, described = page_call("described")
    _, example = page_call("example")
    assert schema_of(example) == schema_of(described)
    assert text_of(example).startswith(text_of(described))
    assert omni.EXAMPLE_TEXT in text_of(example)
    assert omni.EXAMPLE_TEXT not in text_of(described)


def test_the_classify_call_is_the_same_for_every_prompt() -> None:
    bodies = []
    for prompt in omni.PROMPTS:
        stub = Stub({"document_type": "booking_confirmation"})
        reader_with(stub, prompt=prompt).classify(doc(), BOOKING.read_bytes())
        bodies.append(sha(without_image(stub.requests[0])))
    assert bodies == [CLASSIFY_SHA] * len(omni.PROMPTS)


# SHA-256 of the two bodies of #18 with no image, taken from the code of #18 (omni.py
# with the fingerprint d84bcd22…bef4 of the saved reports), before any change of #20
CLASSIFY_SHA = "d248b74b81d706dce918732089b0d59286b66ff23d29c68cc345b36f6614d8c9"
PAGE_SHA = "64ef7156d45a80c2f764917f6819d1b2c2c6cee7cc2f81604f0284e25fc87d9d"
