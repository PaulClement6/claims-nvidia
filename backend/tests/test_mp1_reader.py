"""The multi-page set MP1 (issue #126) through OmniReader in both scopes, with a stub
of the model: no key, no network.

The stub is a perfect model: it answers each request from the answer key of the
document that the request shows. So these tests say what the reader and the harness
do with a right answer and with a few wrong ones. They say nothing about what the
real model answers.

The set itself (its bytes, its keys, its counts): `test_mp1_dataset.py`.
"""

import base64
import hashlib
import json
from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest

from app.docintel import evaluate as ev
from app.docintel import omni
from app.docintel.compare import compare_lines
from app.docintel.omni import UNVERIFIED, VERIFIED, OmniReader, OmniSettings
from app.docintel.schemas import SCHEMAS
from app.fraud.routing import LOW_CONFIDENCE
from app.models import Document, DocumentType, Extraction
from tests.reader_contract import MOCK
from tests.test_omni_reader import URL

MP1 = MOCK / "mp1"
Json = dict[str, Any]
# requests of one run on the 8 documents and 19 pages: one classify call per
# document, then one call per page or one call per document
PER_RUN = {"page": 8 + 19, "document": 8 + 8}


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVIDIA_KEY", "test-key-value")
    monkeypatch.setenv("VLM_BASE_URL", URL)
    monkeypatch.setenv("VLM_MODEL", "omni-test")


def files() -> list[Path]:
    return sorted(MP1.glob("*.pdf"))


@cache
def key_of(name: str) -> Json:
    path = (MP1 / name).with_suffix(".expected.json")
    key: Json = json.loads(path.read_text(encoding="utf-8"))
    return key


def page_answers(key: Json, repeat: bool = False) -> list[Json]:
    """The right answer for each page of a document, from its key: a field in the
    answer of its key page (with `repeat`, of each accepted page, with the text of
    that page), the lines of the page, null for the rest."""
    schema = SCHEMAS[DocumentType(key["document_type"])]
    answers = []
    for number in range(1, key["pages"] + 1):
        values: Json = {n: None for n in schema.model_fields if n != "line_items"}
        snippets: Json = {}
        for f in key["fields"]:
            pages = f.get("accepted_pages", [f["page"]]) if repeat else [f["page"]]
            if f["value"] is not None and number in pages:
                values[f["name"]] = f["value"]
                shown = f.get("page_snippets", {}).get(str(number), f["snippet"])
                snippets[f["name"]] = shown
        if "line_items" in schema.model_fields:
            values["line_items"] = [
                line
                for line, page in zip(key["line_items"], key["line_pages"], strict=True)
                if page == number
            ]
        answers.append({"values": values, "snippets": snippets})
    return answers


def reply(content: Any) -> httpx.Response:
    choice = {"message": {"content": json.dumps(content)}, "finish_reason": "stop"}
    return httpx.Response(200, json={"choices": [choice]})


class Calls:
    """Answers each request in turn and keeps the bodies."""

    def __init__(self, *answers: Any) -> None:
        self.answers = iter(answers)
        self.requests: list[Json] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        return reply(next(self.answers))

    def reader(self, scope: str) -> OmniReader:
        client = httpx.Client(transport=httpx.MockTransport(self.handle))
        return OmniReader(client=client, settings=OmniSettings(scope=scope))


def read(path: Path, scope: str, answers: list[Json]) -> tuple[Extraction, Calls]:
    """The document read in this scope from one answer per page."""
    key = key_of(path.name)
    kind = {"document_type": key["document_type"]}
    whole = {f"page_{n}": a for n, a in enumerate(answers, 1)}
    calls = Calls(kind, *([whole] if scope == "document" else answers))
    document = Document(id="D-mp1", file=path.name, pages=key["pages"])
    return calls.reader(scope).extract(document, path.read_bytes(), "travel"), calls


def by_name(out: Extraction) -> dict[str, Any]:
    return {f.name: f for f in out.fields}


# ----------------------------------------------------------------- a right answer


@pytest.mark.parametrize("path", files(), ids=lambda p: p.name[:6])
@pytest.mark.parametrize("scope", omni.SCOPES)
def test_a_right_answer_gives_every_field_on_its_page(path: Path, scope: str) -> None:
    """Both scopes can read the set: no key snippet is refused by the checks of the
    reader. A text page gives checked evidence, a scan page a whole-page box."""
    key = key_of(path.name)
    out, calls = read(path, scope, page_answers(key))
    scan = key.get("variant") == "scan"

    present = {f["name"]: f for f in key["fields"] if f["value"] is not None}
    assert set(by_name(out)) == set(present)  # no absent field is stored
    for name, field in by_name(out).items():
        assert field.value == present[name]["value"], name
        assert field.source.page == present[name]["page"], name
        assert field.source.document_id == "D-mp1"
        if scan:
            assert field.confidence == UNVERIFIED and field.source.snippet is None
            assert field.source.box is not None
        else:
            assert field.confidence == VERIFIED, name
            assert field.source.snippet == present[name]["snippet"]
    if scan:  # nothing on a scan confirms a line: no list
        assert out.line_items == []
    else:
        lines = compare_lines(key["line_items"], out.line_items)
        assert lines.all_right and lines.read == len(key["line_items"])
    assert len(calls.requests) == 1 + (1 if scope == "document" else key["pages"])


@pytest.mark.parametrize("path", files(), ids=lambda p: p.name[:6])
@pytest.mark.parametrize("repeat", [False, True], ids=["once", "on every page"])
def test_both_scopes_give_the_same_extraction_for_the_same_answers(
    path: Path, repeat: bool
) -> None:
    """Also when the model gives a repeated value on every page that prints it: the
    first page wins and there is no conflict. This compares the code of the two
    scopes, not the answers of the model."""
    answers = page_answers(key_of(path.name), repeat)
    page, _ = read(path, "page", answers)
    document, _ = read(path, "document", answers)
    assert document.model_dump() == page.model_dump()
    first = {f["name"]: f["page"] for f in key_of(path.name)["fields"]}
    assert all(f.source.page == first[f.name] for f in document.fields)


# ------------------------------------------------------------ some wrong answers


@pytest.mark.parametrize("scope", omni.SCOPES)
def test_a_subtotal_given_as_the_total_stays_but_a_person_checks_it(scope: str) -> None:
    """mp1-01: page 1 prints "Subtotal page 1, carried forward 2,435.00". A model
    that gives it as the total, and the real total for page 3: the first value
    stays, with its page, below the limit of the routing."""
    path = MP1 / "mp1-01-repair-invoice-carried-forward.pdf"
    answers = page_answers(key_of(path.name))
    answers[0]["values"]["total"] = 2435.0
    answers[0]["snippets"]["total"] = "2,435.00"
    out, _ = read(path, scope, answers)
    total = by_name(out)["total"]
    assert total.value == 2435.0 and total.source.page == 1
    assert total.confidence < LOW_CONFIDENCE
    assert len(out.line_items) == 12  # the lines are not touched by it


@pytest.mark.parametrize("scope", omni.SCOPES)
def test_a_carried_forward_row_given_as_a_line_is_kept_known_limit(scope: str) -> None:
    """mp1-01: "Brought forward from page 1 2,435.00" given as a line of page 2. Its
    values are on the page, so the reader keeps it: 13 lines. The line check proves
    each line that came back; it does not prove that the list is right or complete.
    The sum against the total (T003) is what can notice it."""
    path = MP1 / "mp1-01-repair-invoice-carried-forward.pdf"
    answers = page_answers(key_of(path.name))
    row = {"description": "Brought forward from page 1", "amount": 2435.0}
    row |= {"quantity": None, "unit_price": None}
    answers[1]["values"]["line_items"].insert(0, row)
    out, _ = read(path, scope, answers)
    assert len(out.line_items) == 13
    assert by_name(out)["total"].confidence == VERIFIED
    assert round(sum(line["amount"] for line in out.line_items), 2) != 6766.0


@pytest.mark.parametrize("scope", omni.SCOPES)
def test_a_row_that_the_model_leaves_out_is_not_seen_known_limit(scope: str) -> None:
    """mp1-05: 11 of the 12 rows come back, each one right. The list is stored as if
    it were whole and the total keeps its confidence."""
    path = MP1 / "mp1-05-repair-invoice-service-rows.pdf"
    answers = page_answers(key_of(path.name))
    del answers[1]["values"]["line_items"][2]
    out, _ = read(path, scope, answers)
    assert len(out.line_items) == 11
    assert by_name(out)["total"].confidence == VERIFIED


@pytest.mark.parametrize("scope", omni.SCOPES)
def test_the_total_with_options_passes_the_label_check_known_limit(scope: str) -> None:
    """mp1-04, page 2: "Total with options CHF 9,763.50" under a total label. The
    label check cannot tell it from the total of the quote: a wrong value at 0.9, in
    both scopes. The set measures this; no rule was changed for it."""
    path = MP1 / "mp1-04-repair-quote-terms.pdf"
    answers = page_answers(key_of(path.name))
    answers[1]["values"]["total"] = 9763.5
    answers[1]["snippets"]["total"] = "CHF 9,763.50"
    out, _ = read(path, scope, answers)
    total = by_name(out)["total"]
    assert total.value == 9763.5 and total.confidence == VERIFIED


@pytest.mark.parametrize("scope", omni.SCOPES)
@pytest.mark.parametrize(
    ("stem", "page", "name", "value", "snippet"),
    [
        ("mp1-07", 2, "trip_price", 430.0, "CHF 430.00"),  # the deposit
        ("mp1-04", 2, "quote_date", "2026-12-18", "2026-12-18"),  # "Valid until"
        ("mp1-04", 3, "quote_date", "2026-02-15", "2026-02-15"),  # the edition
    ],
)
def test_a_lure_given_for_an_absent_field_is_stored_for_a_person(
    scope: str, stem: str, page: int, name: str, value: Any, snippet: str
) -> None:
    [path] = [p for p in files() if p.name.startswith(stem)]
    answers = page_answers(key_of(path.name))
    answers[page - 1]["values"][name] = value
    answers[page - 1]["snippets"][name] = snippet
    out, _ = read(path, scope, answers)
    field = by_name(out)[name]
    assert field.source.page == page and field.confidence < LOW_CONFIDENCE


@pytest.mark.parametrize("scope", omni.SCOPES)
def test_the_date_of_the_report_given_first_hides_the_incident_date(scope: str) -> None:
    """mp1-08: page 1 gives the date of the report as the incident date, page 2 the
    real one. The first value stays (wrong), below the limit: a person checks it."""
    path = MP1 / "mp1-08-police-report-two-dates.pdf"
    answers = page_answers(key_of(path.name))
    answers[0]["values"]["incident_date"] = "2026-11-11"
    answers[0]["snippets"]["incident_date"] = "2026-11-11"
    out, _ = read(path, scope, answers)
    date = by_name(out)["incident_date"]
    assert date.value == "2026-11-11" and date.source.page == 1
    assert date.confidence < LOW_CONFIDENCE


def test_a_document_answer_that_hides_the_first_value_shows_no_conflict() -> None:
    """The one thing that the document scope can lose: with all pages in view, the
    model can leave out a value that it gives for page 1 alone. The reader then sees
    one value and no conflict, so nothing marks the field. Here the value that stays
    is the right one. The reader cannot tell this case from one where the model
    kept the wrong value: what the model left out is not in the answer."""
    path = MP1 / "mp1-01-repair-invoice-carried-forward.pdf"
    answers = page_answers(key_of(path.name))
    answers[0]["values"]["total"] = 2435.0  # what the page scope gets for page 1
    answers[0]["snippets"]["total"] = "2,435.00"
    seen, _ = read(path, "page", answers)
    assert by_name(seen)["total"].confidence < LOW_CONFIDENCE  # 2,435.00, a conflict

    answers[0]["values"]["total"] = None  # the document answer leaves it out
    del answers[0]["snippets"]["total"]
    hidden, _ = read(path, "document", answers)
    total = by_name(hidden)["total"]
    assert total.value == 6766.0 and total.confidence == VERIFIED  # right here


# --------------------------------------------------------------------- the harness


@cache
def shown() -> dict[str, tuple[str, int]]:
    """SHA-256 of each page image that the reader sends -> (file name, page)."""
    found = {}
    for path in files():
        for number, page in enumerate(omni.pages(path.read_bytes()), 1):
            found[hashlib.sha256(page.image).hexdigest()] = (path.name, number)
    return found


def perfect_model(change: Callable[[str, list[Json]], None] | None = None) -> Any:
    """A transport that answers every request from the key of the document that its
    first image shows: the type, one page answer, or one block per page."""

    def handle(request: httpx.Request) -> httpx.Response:
        parts = json.loads(request.content)["messages"][0]["content"]
        images = [
            base64.b64decode(p["image_url"]["url"].split(",", 1)[1])
            for p in parts
            if p["type"] == "image_url"
        ]
        seen = [shown()[hashlib.sha256(i).hexdigest()] for i in images]
        name, first = seen[0]
        key = key_of(name)
        if parts[0]["text"] == omni.CLASSIFY:
            return reply({"document_type": key["document_type"]})
        answers = page_answers(key)
        if change:
            change(name, answers)
        if len(images) == 1 and "Page " not in parts[1].get("text", ""):
            return reply(answers[first - 1])
        assert seen == [(name, n) for n in range(1, key["pages"] + 1)]
        return reply({f"page_{n}": a for n, a in enumerate(answers, 1)})

    return httpx.MockTransport(handle)


def evaluated(scope: str, runs: int = 1) -> Json:
    settings = OmniSettings(scope=scope)
    make, named, meter = ev._omni(transport=perfect_model(), options=settings)
    return ev.evaluate(make, runs, settings=named, meter=meter, document_set="mp1")


@pytest.fixture(scope="module")
def reports() -> dict[str, Json]:
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("NVIDIA_KEY", "test-key-value")
        patch.setenv("VLM_BASE_URL", URL)
        patch.setenv("VLM_MODEL", "omni-test")
        return {scope: evaluated(scope) for scope in omni.SCOPES}


def test_the_harness_reads_the_set_apart_from_the_kit(reports: dict[str, Json]) -> None:
    kit = reports["page"]["kit"]
    assert kit["set"] == "mp1" and kit["documents"] == 8
    assert kit["by_kind"] == {"clean": 6, "scan": 2}
    assert (kit["expected_fields"], kit["expected_line_items"]) == (35, 39)
    assert reports["page"]["complete"] and reports["document"]["complete"]


@pytest.mark.parametrize("scope", omni.SCOPES)
def test_one_run_sends_the_planned_number_of_requests(
    reports: dict[str, Json], scope: str
) -> None:
    """The plan of #126: 3 runs per scope, 3 x 27 + 3 x 16 = 129 requests."""
    http = reports[scope]["run_details"][0]["http"]
    assert http["requests"] == PER_RUN[scope] and http["by_status"] == {
        "200": PER_RUN[scope]
    }
    assert http["finish_reasons"] == {"stop": PER_RUN[scope]}
    assert 3 * PER_RUN["page"] + 3 * PER_RUN["document"] == 129


@pytest.mark.parametrize("scope", omni.SCOPES)
def test_a_perfect_model_gives_every_field_right_in_both_scopes(
    reports: dict[str, Json], scope: str
) -> None:
    overall = reports[scope]["run_details"][0]["overall"]
    fields = overall["fields"]
    assert (fields["expected"], fields["right"]) == (35, 35)
    assert (fields["absent"], fields["right_absent"], fields["right_present"]) == (
        2,
        2,
        33,
    )
    assert fields["wrong"] == fields["missing"] == fields["extra"] == 0
    assert fields["read"] == fields["page_right"] == 33
    assert overall["documents"]["passed"] == overall["documents"]["count"] == 8
    assert overall["documents"]["classified_right"] == 8
    # the 26 present fields of the text PDFs are checked evidence; the 7 fields of
    # the two scans are for a person
    assert fields["right_trusted"] == 26 and fields["incorrect_trusted"] == 0
    # the 4 lines of the scan are refused by the rule of the reader, in both scopes
    lines = overall["line_items"]
    assert (lines["expected"], lines["right"]) == (39, 35)


def test_the_two_scopes_give_the_same_rows_for_a_perfect_model(
    reports: dict[str, Json],
) -> None:
    def rows(report: Json) -> list[Any]:
        docs = report["run_details"][0]["documents"]
        return [(d["id"], d["fields"], d["line_items"], d["passed"]) for d in docs]

    assert rows(reports["document"]) == rows(reports["page"])


def test_a_report_names_its_scope_and_the_two_scopes_do_not_combine(
    reports: dict[str, Json], tmp_path: Path
) -> None:
    page, document = reports["page"], reports["document"]
    assert "scope" not in page["settings"]
    assert document["settings"]["scope"] == "document"
    assert document["settings"]["config_id"] != page["settings"]["config_id"]
    paths = []
    for name, report in reports.items():
        paths.append(tmp_path / f"{name}.json")
        paths[-1].write_text(ev.dump(report), encoding="utf-8")
    with pytest.raises(ev.BadCombine):
        ev.combine(paths)
