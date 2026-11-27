"""The evaluation harness (issue #19): a reader on the whole kit, against expected.json.

The fixture reader must give 100% (it reads the answer keys). The other tests give
the harness a small kit and readers that are wrong on purpose: the harness must see
each error, and must not count it as right.
"""

import json
from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfWriter

from app.demo import NOT_KIT
from app.docintel.evaluate import evaluate, kit
from app.docintel.fixture import FixtureReader
from app.models import Document, ExtractedField, Extraction
from tests.reader_contract import MOCK

Json = dict[str, Any]

KIT = sorted(
    p
    for e in ("pdf", "jpg", "png")
    for p in MOCK.glob(f"*/*.{e}")
    if p.is_file() and p.parent.name not in NOT_KIT
)


def _answer(path: Path) -> Json:
    data: Json = json.loads(path.with_suffix(".expected.json").read_text())
    return data


def test_the_fixture_reader_gives_100_percent_on_the_whole_kit() -> None:
    report = evaluate(FixtureReader, runs=1)

    expected_fields = sum(len(_answer(p)["fields"]) for p in KIT)
    pooled = report["pooled"]
    assert report["complete"] is True and report["answer_key_errors"] == []
    assert report["kit"]["documents"] == len(KIT)
    assert report["kit"]["expected_fields"] == expected_fields
    assert pooled["documents"]["count"] == len(KIT)
    assert pooled["documents"]["passed"] == len(KIT)
    assert pooled["documents"]["pass_rate"] == 1.0
    assert pooled["documents"]["errors"] == 0
    assert pooled["documents"]["classified_right"] == len(KIT)
    fields = pooled["fields"]
    assert fields["expected"] == fields["right"] == expected_fields
    assert fields["verified"] == fields["page_right"] == expected_fields
    assert (fields["wrong"], fields["missing"], fields["extra"]) == (0, 0, 0)
    assert fields["accuracy"] == fields["verified_accuracy"] == 1.0
    assert fields["page_accuracy"] == 1.0
    lines = pooled["line_items"]
    assert lines["expected"] == lines["read"] == lines["right"]
    assert lines["documents_right"] == lines["documents"]
    for by_type in pooled["by_type"].values():  # one table per type, all right
        assert by_type["fields"]["accuracy"] == 1.0


def test_the_harness_reads_the_same_documents_as_the_demo_kit() -> None:
    from app import demo

    docs = kit()
    assert [d.id for d in docs] == [f"{f}/{n}" for f, n in demo._kit()]
    assert len(docs) == len(KIT)


def test_kind_and_workflow_come_from_the_index_of_the_kit() -> None:
    index = json.loads((MOCK / "index.json").read_text())
    docs = {d.id: d for d in kit()}
    hard = {h["file"]: h["variant"] for h in index["hard"]}
    for doc_id, d in docs.items():
        assert d.kind == hard.get(doc_id, "clean")
    by_kind = evaluate(FixtureReader)["kit"]["by_kind"]
    assert by_kind["clean"] == len(KIT) - len(hard)
    assert by_kind["scan"] + by_kind["photo"] == len(hard)
    # a hard version has the workflow of the scenario of its source
    assert docs["claim-1-travel/booking-confirmation.pdf"].workflow == "travel"
    assert docs["hard/claim-2-property-repair-invoice-scan.pdf"].workflow == "property"
    assert docs["claim-3-health/medical-invoice.pdf"].workflow == "health"
    assert {d.workflow for d in docs.values()} == {"travel", "property", "health"}


# ---------------------------------------------------------------- a small kit

KEY: Json = {
    "document_type": "booking_confirmation",
    "fields": [
        {"name": "booking_reference", "value": "WPR-001", "confidence": 1.0, "page": 1},
        {"name": "departure_date", "value": "2026-08-10", "confidence": 1.0, "page": 1},
        {"name": "trip_price", "value": 1840.0, "confidence": 1.0, "page": 2},
        {
            "name": "traveller_name",
            "value": "Anna Keller",
            "confidence": 1.0,
            "page": 1,
        },
    ],
    "line_items": [
        {"description": "Flights", "quantity": 1, "unit_price": 840.0, "amount": 840.0}
    ],
}
RIGHT: dict[str, tuple[object, int]] = {
    "booking_reference": ("WPR-001", 1),
    "departure_date": ("2026-08-10", 1),
    "trip_price": ("1,840.00", 2),  # the same amount, written another way
    "traveller_name": ("anna  keller", 1),  # case and spaces do not count
}


def _pdf(pages: int, tag: str) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    writer.add_metadata({"/Title": tag})  # different bytes per document
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def small_kit(
    root: Path, monkeypatch: pytest.MonkeyPatch, keys: dict[str, Any]
) -> None:
    """data/mock with one travel scenario `trip` of two-page PDFs. `keys` maps a
    file name to its answer key: a dict, raw text, or None for no expected.json."""
    mock = root / "mock"
    (mock / "trip").mkdir(parents=True)
    policies = {"policies": [{"number": "TRV-1", "line_of_business": "Travel"}]}
    (mock / "policies.json").write_text(json.dumps(policies))
    index = {"scenarios": [{"folder": "trip", "policy": "TRV-1"}], "hard": []}
    (mock / "index.json").write_text(json.dumps(index))
    for name, key in keys.items():
        path = mock / "trip" / name
        path.write_bytes(_pdf(2, name))
        if isinstance(key, dict):
            path.with_suffix(".expected.json").write_text(json.dumps(key))
        elif isinstance(key, str):
            path.with_suffix(".expected.json").write_text(key)
    monkeypatch.setenv("CLAIMS_DATA", str(root))


Answer = Callable[[Document], Extraction]


def answer(
    values: dict[str, tuple[object, int]] | None = None,
    lines: list[Json] | None = None,
    document_id: str | None = None,
    also: list[tuple[str, object, int]] | None = None,
) -> Answer:
    """An extraction with each value on its page; `also` adds fields at the end."""
    pairs = [(n, v, p) for n, (v, p) in (RIGHT if values is None else values).items()]
    pairs += also or []

    def make(document: Document) -> Extraction:
        return Extraction.model_validate(
            {
                "document_type": "booking_confirmation",
                "fields": [
                    {
                        "name": n,
                        "value": v,
                        "confidence": 0.9,
                        "source": {
                            "document_id": document_id or document.id,
                            "page": p,
                            "snippet": "x",
                        },
                    }
                    for n, v, p in pairs
                ],
                "line_items": KEY["line_items"] if lines is None else lines,
            }
        )

    return make


class StubReader:
    name = "stub"

    def __init__(
        self,
        answers: dict[str, Answer | Exception],
        kinds: dict[str, str | Exception] | None = None,
    ) -> None:
        self.answers, self.kinds = answers, kinds or {}
        self.calls: list[tuple[str, str]] = []

    def classify(self, document: Document, data: bytes) -> str:
        kind = self.kinds.get(document.file, "booking_confirmation")
        if isinstance(kind, Exception):
            raise kind
        return kind

    def extract(self, document: Document, data: bytes, workflow: str) -> Extraction:
        self.calls.append((document.file, workflow))
        found = self.answers.get(document.file, answer())
        if isinstance(found, Exception):
            raise found
        return found(document)


def one(reader: StubReader) -> Json:
    return evaluate(lambda: reader)


def doc(report: Json, file: str) -> Json:
    found: Json = next(
        d for d in report["run_details"][0]["documents"] if d["file"] == file
    )
    return found


def row(document: Json, name: str) -> Json:
    found: Json = next(r for r in document["fields"] if r["name"] == name)
    return found


@pytest.fixture
def kit_of_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    small_kit(tmp_path, monkeypatch, {"a.pdf": KEY})


def test_a_right_reading_passes_with_equal_amounts_text_and_pages(
    kit_of_one: None,
) -> None:
    reader = StubReader({})
    d = doc(one(reader), "a.pdf")
    assert d["passed"] is True and d["error"] is None
    assert d["counts"]["right"] == d["counts"]["verified"] == 4
    assert d["workflow"] == "travel" and reader.calls == [("a.pdf", "travel")]
    assert d["pages"] == 2 and d["kind"] == "clean"
    assert row(d, "trip_price")["source"] == "right"
    assert d["line_items"] == {"expected": 1, "read": 1, "right": 1, "all_right": True}


def test_a_wrong_amount_is_wrong_and_the_document_fails(kit_of_one: None) -> None:
    report = one(StubReader({"a.pdf": answer(RIGHT | {"trip_price": (1804.0, 2)})}))
    d = doc(report, "a.pdf")
    r = row(d, "trip_price")
    assert r["status"] == "wrong" and r["read"] == 1804.0 and r["expected"] == 1840.0
    assert d["passed"] is False
    f = report["pooled"]["fields"]
    assert (f["right"], f["wrong"], f["missing"], f["expected"]) == (3, 1, 0, 4)
    assert f["accuracy"] == 0.75
    price = report["pooled"]["by_type"]["booking_confirmation"]["by_field"][
        "trip_price"
    ]
    assert price["wrong"] == 1 and price["accuracy"] == 0
    assert report["pooled"]["documents"]["pass_rate"] == 0


def test_a_wrong_text_is_wrong(kit_of_one: None) -> None:
    values = RIGHT | {"traveller_name": ("Anna Keler", 1)}
    d = doc(one(StubReader({"a.pdf": answer(values)})), "a.pdf")
    assert row(d, "traveller_name")["status"] == "wrong"


def test_an_absent_field_and_a_null_field_are_missing_not_wrong(
    kit_of_one: None,
) -> None:
    values = {k: v for k, v in RIGHT.items() if k != "booking_reference"}
    values["departure_date"] = (None, 1)
    report = one(StubReader({"a.pdf": answer(values)}))
    d = doc(report, "a.pdf")
    assert row(d, "booking_reference")["status"] == "missing"
    assert row(d, "booking_reference")["reason"] == "absent"
    assert row(d, "departure_date")["status"] == "missing"
    assert row(d, "departure_date")["page_right"] is None
    f = report["pooled"]["fields"]
    assert (f["right"], f["wrong"], f["missing"], f["expected"]) == (2, 0, 2, 4)
    assert f["page_accuracy"] == 1.0  # of the 2 values read, both on their page


def test_a_right_value_on_the_wrong_page_is_not_verified(kit_of_one: None) -> None:
    report = one(StubReader({"a.pdf": answer(RIGHT | {"trip_price": (1840.0, 1)})}))
    d = doc(report, "a.pdf")
    r = row(d, "trip_price")
    assert r["status"] == "right"  # the value is right ...
    assert r["source"] == "wrong_page" and r["page_right"] is False  # ... its page not
    assert d["counts"]["right"] == 4 and d["counts"]["verified"] == 3
    assert d["passed"] is False
    f = report["pooled"]["fields"]
    assert f["accuracy"] == 1.0 and f["verified_accuracy"] == 0.75
    assert f["page_accuracy"] == 0.75


def test_a_source_of_another_document_is_invalid(kit_of_one: None) -> None:
    d = doc(one(StubReader({"a.pdf": answer(document_id="D-other")})), "a.pdf")
    assert {r["source"] for r in d["fields"]} == {"invalid"}
    assert d["counts"]["verified"] == 0 and d["passed"] is False


def test_a_page_after_the_last_page_is_an_invalid_source(kit_of_one: None) -> None:
    wrong = answer(RIGHT | {"trip_price": (1840.0, 3)})
    r = row(doc(one(StubReader({"a.pdf": wrong})), "a.pdf"), "trip_price")
    assert r["source"] == "invalid" and r["page"] == 3 and r["page_right"] is False


def test_a_field_with_no_source_is_invalid_too(kit_of_one: None) -> None:
    """A reader that skips validation (`model_construct`) cannot slip a field with
    no source past the harness (non-negotiable 3)."""

    def no_source(document: Document) -> Extraction:
        out = answer()(document)
        bare = ExtractedField.model_construct(
            name="trip_price", value=1840.0, confidence=1.0
        )
        return Extraction.model_construct(
            document_type=out.document_type,
            fields=[f for f in out.fields if f.name != "trip_price"] + [bare],
            line_items=out.line_items,
        )

    d = doc(one(StubReader({"a.pdf": no_source})), "a.pdf")
    r = row(d, "trip_price")
    assert r["source"] == "invalid" and r["page"] is None and d["passed"] is False


def test_an_extra_field_is_counted_apart_and_does_not_fail_the_document(
    kit_of_one: None,
) -> None:
    report = one(StubReader({"a.pdf": answer(also=[("vat_number", "CHE-1", 1)])}))
    d = doc(report, "a.pdf")
    assert d["extra"] == [
        {"name": "vat_number", "value": "CHE-1", "page": 1, "confidence": 0.9}
    ]
    assert d["counts"]["extra"] == 1 and d["counts"]["expected"] == 4
    assert d["passed"] is True
    assert report["pooled"]["by_field"]["vat_number"]["expected"] == 0
    assert report["pooled"]["by_field"]["vat_number"]["extra"] == 1


def test_a_field_given_twice_with_two_values_is_wrong(kit_of_one: None) -> None:
    same = doc(
        one(StubReader({"a.pdf": answer(also=[("trip_price", 1840, 2)])})), "a.pdf"
    )
    assert same["duplicates"] == ["trip_price"]
    assert row(same, "trip_price")["status"] == "right"
    other = answer(also=[("trip_price", 1500.0, 2)])
    d = doc(one(StubReader({"a.pdf": other})), "a.pdf")
    assert row(d, "trip_price")["status"] == "wrong"
    assert row(d, "trip_price")["reason"] == "given twice with different values"
    assert d["counts"]["extra"] == 0  # a duplicate is not an extra field


def test_a_wrong_type_is_counted_but_does_not_change_the_fields(
    kit_of_one: None,
) -> None:
    report = one(StubReader({}, kinds={"a.pdf": "receipt"}))
    d = doc(report, "a.pdf")
    assert d["classified"] == "receipt" and d["classified_right"] is False
    assert d["passed"] is True  # classification is reported apart (issue #21)
    assert report["pooled"]["documents"]["classified_right"] == 0


def test_wrong_line_items_are_reported_apart_from_the_fields(
    kit_of_one: None,
) -> None:
    lines = [
        {"description": "Flights", "quantity": 1, "unit_price": 840.0, "amount": 480.0}
    ]
    report = one(StubReader({"a.pdf": answer(lines=lines)}))
    d = doc(report, "a.pdf")
    assert d["line_items"] == {"expected": 1, "read": 1, "right": 0, "all_right": False}
    assert d["passed"] is True and report["pooled"]["fields"]["accuracy"] == 1.0
    assert report["pooled"]["line_items"]["documents_right"] == 0


def test_a_reader_that_raises_fails_one_document_and_the_run_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    small_kit(tmp_path, monkeypatch, {"a.pdf": KEY, "b.pdf": KEY})
    report = one(StubReader({"a.pdf": TimeoutError("model too slow")}))
    a, b = doc(report, "a.pdf"), doc(report, "b.pdf")
    assert a["error"] == "extract: TimeoutError: model too slow"
    assert {r["status"] for r in a["fields"]} == {"missing"}
    assert {r["reason"] for r in a["fields"]} == {"the reader failed"}
    assert a["passed"] is False and a["extracted_type"] is None
    assert b["passed"] is True
    pooled = report["pooled"]
    # the failed document stays in every denominator
    assert pooled["documents"]["count"] == 2 and pooled["documents"]["errors"] == 1
    assert pooled["fields"]["expected"] == 8 and pooled["fields"]["missing"] == 4
    assert pooled["fields"]["accuracy"] == 0.5
    assert pooled["per_document"][0]["errors"] == [a["error"]]


def test_a_classify_that_raises_or_returns_junk_is_an_error(kit_of_one: None) -> None:
    d = doc(one(StubReader({}, kinds={"a.pdf": ValueError("bad page")})), "a.pdf")
    assert d["classified"] is None and d["error"] == "classify: ValueError: bad page"
    assert d["passed"] is False and d["counts"]["right"] == 4

    class Junk(StubReader):
        def extract(self, document: Document, data: bytes, workflow: str) -> Extraction:
            return {"fields": []}  # type: ignore[return-value]

    d = doc(one(Junk({})), "a.pdf")
    assert d["error"] == "extract: returned dict" and d["counts"]["missing"] == 4


BROKEN_PRICE = {"name": "trip_price", "value": "1840", "page": 1}


@pytest.mark.parametrize(
    ("key", "error"),
    [
        (None, "no expected.json"),
        ("{not json", "JSONDecodeError"),
        (KEY | {"document_type": "menu"}, "unknown document_type"),
        (KEY | {"fields": [*KEY["fields"], KEY["fields"][0]]}, "twice"),
        (KEY | {"fields": [{"name": "colour", "value": "red", "page": 1}]}, "schema"),
        (KEY | {"fields": [BROKEN_PRICE]}, "BadAnswerKey"),
        (KEY | {"fields": [BROKEN_PRICE | {"value": 1840.0, "page": 0}]}, "page"),
        (KEY | {"line_items": [{"amount": "lots"}]}, "BadAnswerKey"),
    ],
)
def test_a_broken_answer_key_is_listed_and_not_scored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: object, error: str
) -> None:
    small_kit(tmp_path, monkeypatch, {"a.pdf": KEY, "b.pdf": key})
    report = one(StubReader({}))
    assert report["complete"] is False
    [found] = report["answer_key_errors"]
    assert found["document"] == "trip/b.pdf" and error in found["error"]
    assert report["kit"]["documents"] == 2
    assert report["pooled"]["documents"]["count"] == 1  # b is not scored
    assert report["pooled"]["fields"]["expected"] == 4


def test_runs_get_a_new_reader_each_and_give_mean_lowest_and_highest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    small_kit(tmp_path, monkeypatch, {"a.pdf": KEY, "b.pdf": KEY})
    readers: list[StubReader] = []

    def make() -> StubReader:  # wrong on a.pdf in the second run only
        wrong = answer(RIGHT | {"trip_price": (1.0, 2)})
        readers.append(StubReader({"a.pdf": wrong} if len(readers) == 1 else {}))
        return readers[-1]

    report = evaluate(make, runs=3)
    assert len(readers) == 3 and report["runs"] == 3
    assert [r["run"] for r in report["run_details"]] == [1, 2, 3]
    s = report["summary"]
    assert s["accuracy"] == {"mean": 0.9583, "min": 0.875, "max": 1.0, "spread": 0.125}
    assert s["pass_rate"]["min"] == 0.5 and s["pass_rate"]["max"] == 1.0
    assert s["wrong"] == {"mean": 0.3333, "min": 0, "max": 1, "spread": 1}
    pooled = report["pooled"]
    assert pooled["fields"]["expected"] == 24 and pooled["fields"]["wrong"] == 1
    assert pooled["documents"]["count"] == 6 and pooled["documents"]["passed"] == 5
    a = next(d for d in pooled["per_document"] if d["id"] == "trip/a.pdf")
    assert (a["runs"], a["passed_runs"]) == (3, 2)
    assert a["fields"]["wrong"] == 1 and a["fields"]["expected"] == 12


def test_seconds_come_from_the_clock_per_call_document_and_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    small_kit(tmp_path, monkeypatch, {"a.pdf": KEY, "b.pdf": KEY})
    ticks = iter(range(1000))  # each read of the clock is one second later

    report = evaluate(lambda: StubReader({}), clock=lambda: float(next(ticks)))
    a = doc(report, "a.pdf")
    assert a["seconds"] == {"classify": 1.0, "extract": 1.0, "total": 2.0}
    assert report["pooled"]["seconds"] == {
        "count": 2,
        "total": 4.0,
        "mean": 2.0,
        "median": 2.0,
        "min": 2.0,
        "max": 2.0,
    }
    assert report["run_details"][0]["seconds"] == 9.0  # start, 4 calls x 2, end
    assert report["summary"]["seconds_per_document"]["mean"] == 2.0


def test_the_same_reading_gives_the_same_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    small_kit(tmp_path, monkeypatch, {"b.pdf": KEY, "a.pdf": KEY})

    def run() -> Json:
        report = evaluate(lambda: StubReader({}), clock=lambda: 0.0)
        for r in report["run_details"]:
            r.pop("started_at"), r.pop("run_id")  # new for each run, by design
        return {k: v for k, v in report.items() if k != "created_at"}

    first = run()
    assert first == run()
    ids = [d["id"] for d in first["pooled"]["per_document"]]
    assert ids == ["trip/a.pdf", "trip/b.pdf"]
    totals = first["pooled"]["fields"]
    docs = first["run_details"][0]["documents"]
    for k in ("expected", "right", "wrong", "missing", "extra", "verified"):
        assert totals[k] == sum(d["counts"][k] for d in docs)


def test_zero_runs_is_refused() -> None:
    with pytest.raises(ValueError, match="1 or more"):
        evaluate(FixtureReader, runs=0)


def test_a_field_expected_empty_and_not_read_is_right_with_no_page_to_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An answer key can say `null`: the value is not on the document. Not reading
    it is right; it has no source, so it is out of the page denominator."""
    fields = [*KEY["fields"][:3], KEY["fields"][3] | {"value": None}]
    small_kit(tmp_path, monkeypatch, {"a.pdf": KEY | {"fields": fields}})
    values = {k: v for k, v in RIGHT.items() if k != "traveller_name"}
    f = one(StubReader({"a.pdf": answer(values)}))["pooled"]["fields"]
    assert (f["right"], f["read"], f["page_right"], f["verified"]) == (4, 3, 3, 4)
    assert f["accuracy"] == f["verified_accuracy"] == f["page_accuracy"] == 1.0
    # a value read where none is expected is wrong, and its page is checked
    also = answer(RIGHT)
    f = one(StubReader({"a.pdf": also}))["pooled"]["fields"]
    assert (f["wrong"], f["read"], f["page_right"]) == (1, 4, 4)


def test_only_the_first_of_two_equal_fields_gives_the_source(
    kit_of_one: None,
) -> None:
    """The checks of the base read the first field of a name: so does the harness."""
    first_on_wrong_page = answer(
        RIGHT | {"trip_price": (1840.0, 1)}, also=[("trip_price", 1840.0, 2)]
    )
    d = doc(one(StubReader({"a.pdf": first_on_wrong_page})), "a.pdf")
    assert row(d, "trip_price")["status"] == "right"
    assert row(d, "trip_price")["source"] == "wrong_page" and d["passed"] is False
