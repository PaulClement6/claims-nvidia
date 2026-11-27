"""The safety measure of the harness (issue #125, phase 3).

The harness counts the incorrect values that a reader stores at LOW_CONFIDENCE or
more (the limit of `routing.py`: above it nobody looks at the value again), apart
from the incorrect values at any confidence. It covers wrong values of fields that
the page holds and values given for fields that the page does not hold (the answer
key says null). A right absence is counted apart from a right value. An extra field
(not in the answer key) cannot be called right or wrong: it is counted apart. A
report written before the confidence was recorded gives no number (None), never 0.

No network: the N1 replay answers with the saved answers of the model only.
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest

from app.docintel import evaluate as ev
from app.docintel import labels
from app.docintel.evaluate import evaluate, kit
from app.docintel.fixture import FixtureReader
from app.docintel.omni import CLASSIFY, OmniSettings
from app.fraud import routing
from app.fraud.routing import LOW_CONFIDENCE
from app.models import Document, Extraction
from tests.test_evaluate import KEY, StubReader, doc, row, small_kit

Json = dict[str, Any]
Field = tuple[str, object, int, float]  # name, value, page, confidence
RIGHT: list[Field] = [
    ("booking_reference", "WPR-001", 1, 0.9),
    ("departure_date", "2026-08-10", 1, 0.9),
    ("trip_price", 1840.0, 2, 0.9),
    ("traveller_name", "Anna Keller", 1, 0.9),
]
# the same document, but the page holds no booking reference (the key says null)
ABSENT_KEY: Json = KEY | {
    "fields": [
        f | {"value": None} if f["name"] == "booking_reference" else f
        for f in KEY["fields"]
    ]
}


def reading(fields: list[Field]) -> Callable[[Document], Extraction]:
    def make(document: Document) -> Extraction:
        return Extraction.model_validate(
            {
                "document_type": "booking_confirmation",
                "fields": [
                    {
                        "name": n,
                        "value": v,
                        "confidence": c,
                        "source": {
                            "document_id": document.id,
                            "page": p,
                            "snippet": "x",
                        },
                    }
                    for n, v, p, c in fields
                ],
                "line_items": KEY["line_items"],
            }
        )

    return make


def but(name: str, value: object, confidence: float) -> list[Field]:
    """RIGHT with one field changed."""
    return [
        (n, value, p, confidence) if n == name else (n, v, p, c) for n, v, p, c in RIGHT
    ]


def counts(fields: list[Field], key: Json = KEY, **kit: Any) -> Json:
    report = evaluate(lambda: StubReader({"a.pdf": reading(fields)}))
    found: Json = report["pooled"]["fields"]
    return found


@pytest.fixture
def one_document(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    small_kit(tmp_path, monkeypatch, {"a.pdf": KEY})


@pytest.fixture
def one_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    small_kit(tmp_path, monkeypatch, {"a.pdf": ABSENT_KEY})


# ---------------------------------------------------------------- the threshold


def test_the_threshold_is_the_one_of_routing() -> None:
    assert vars(ev)["LOW_CONFIDENCE"] is routing.LOW_CONFIDENCE == 0.8


@pytest.mark.parametrize(
    ("confidence", "trusted"), [(0.8, 1), (0.9, 1), (1.0, 1), (0.79, 0), (0.5, 0)]
)
def test_a_wrong_value_counts_as_trusted_from_the_threshold_on(
    one_document: None, confidence: float, trusted: int
) -> None:
    f = counts(but("trip_price", 999.0, confidence))
    assert f["wrong"] == f["incorrect_present"] == 1
    assert f["incorrect_trusted"] == f["incorrect_trusted_present"] == trusted
    assert f["incorrect_trusted_absent"] == 0


def test_the_report_records_the_threshold(one_document: None) -> None:
    report = evaluate(lambda: StubReader({"a.pdf": reading(RIGHT)}))
    assert report["safety"] == {"threshold": LOW_CONFIDENCE, "available": True}


# ---------------------------------------------------------------- present and absent


def test_right_values_and_right_absences_are_counted_apart(one_absent: None) -> None:
    f = counts([x for x in RIGHT if x[0] != "booking_reference"])
    assert f["absent"] == 1 and f["right_absent"] == 1
    assert f["right_present"] == 3 and f["right_trusted"] == 3
    assert f["right"] == 4 and f["wrong"] == 0 and f["incorrect_trusted"] == 0


def test_a_value_for_a_field_that_the_page_does_not_hold_is_incorrect(
    one_absent: None,
) -> None:
    f = counts(RIGHT)  # the reader gives WPR-001 where the page has nothing
    assert f["right_absent"] == 0 and f["incorrect_absent"] == 1
    assert f["incorrect_trusted"] == f["incorrect_trusted_absent"] == 1
    assert f["incorrect_present"] == f["incorrect_trusted_present"] == 0


def test_a_value_for_an_absent_field_below_the_threshold_is_not_trusted(
    one_absent: None,
) -> None:
    f = counts(but("booking_reference", "WPR-001", 0.5))
    assert f["incorrect_absent"] == 1 and f["incorrect_trusted"] == 0


def test_a_right_value_below_the_threshold_is_right_but_not_trusted(
    one_document: None,
) -> None:
    f = counts(but("trip_price", 1840.0, 0.5))
    assert f["right_present"] == 4 and f["right_trusted"] == 3
    assert f["incorrect_trusted"] == 0


def test_each_row_records_the_confidence(one_document: None) -> None:
    report = evaluate(
        lambda: StubReader({"a.pdf": reading(but("trip_price", 999.0, 0.5))})
    )
    d = doc(report, "a.pdf")
    assert row(d, "trip_price")["confidence"] == 0.5
    assert row(d, "departure_date")["confidence"] == 0.9


def test_a_field_not_read_has_no_confidence(one_absent: None) -> None:
    fields = [x for x in RIGHT if x[0] != "booking_reference"]
    report = evaluate(lambda: StubReader({"a.pdf": reading(fields)}))
    assert row(doc(report, "a.pdf"), "booking_reference")["confidence"] is None


# ---------------------------------------------------------------- duplicates, extras


def test_a_wrong_copy_of_a_field_counts_with_its_own_confidence(
    one_document: None,
) -> None:
    """Given twice: right at 0.5, wrong at 0.9. The wrong value was stored at 0.9."""
    f = counts(
        RIGHT[:2] + [("trip_price", 1840.0, 2, 0.5), ("trip_price", 999.0, 2, 0.9)]
    )
    assert f["wrong"] == 1 and f["incorrect_trusted"] == 1


def test_a_wrong_copy_below_the_threshold_is_not_trusted(one_document: None) -> None:
    f = counts(
        RIGHT[:2] + [("trip_price", 999.0, 2, 0.5), ("trip_price", 1840.0, 2, 0.9)]
    )
    assert f["wrong"] == 1 and f["incorrect_trusted"] == 0


def test_an_extra_field_is_counted_apart_and_never_as_incorrect(
    one_document: None,
) -> None:
    """The answer key does not say if an extra field is right: apart."""
    f = counts(RIGHT + [("return_date", "2026-08-20", 1, 0.9)])
    assert f["extra"] == 1 and f["extra_trusted"] == 1
    assert f["wrong"] == 0 and f["incorrect_trusted"] == 0
    f = counts(RIGHT + [("return_date", "2026-08-20", 1, 0.5)])
    assert f["extra"] == 1 and f["extra_trusted"] == 0


# ---------------------------------------------------------------- totals


def test_the_measure_adds_up_over_documents_fields_types_and_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    small_kit(tmp_path, monkeypatch, {"a.pdf": KEY, "b.pdf": ABSENT_KEY})
    reader = StubReader(
        {
            "a.pdf": reading(but("trip_price", 999.0, 0.9)),  # 1 trusted, present
            "b.pdf": reading(RIGHT),  # 1 trusted, absent
        }
    )
    report = evaluate(lambda: reader, runs=2)
    for r in report["run_details"]:
        assert r["overall"]["fields"]["incorrect_trusted"] == 2
    pooled = report["pooled"]
    assert pooled["fields"]["incorrect_trusted"] == 4
    assert pooled["fields"]["incorrect_trusted_absent"] == 2
    assert pooled["fields"]["incorrect_trusted_present"] == 2
    assert pooled["by_field"]["trip_price"]["incorrect_trusted"] == 2
    assert pooled["by_field"]["booking_reference"]["incorrect_trusted"] == 2
    assert pooled["by_type"]["booking_confirmation"]["fields"]["incorrect_trusted"] == 4
    assert pooled["by_kind"]["clean"]["fields"]["incorrect_trusted"] == 4
    per_doc = {d["id"]: d["fields"] for d in pooled["per_document"]}
    assert per_doc["trip/a.pdf"]["incorrect_trusted_present"] == 2
    assert per_doc["trip/b.pdf"]["incorrect_trusted_absent"] == 2
    stat = report["summary"]["incorrect_trusted"]
    assert stat == {"mean": 2.0, "min": 2, "max": 2, "spread": 0.0}
    assert "incorrect values at confidence >= 0.8: 4" in ev.summary_text(report)


# ---------------------------------------------------------------- saved reports


def _save(report: Json, path: Path) -> Path:
    path.write_text(ev.dump(report), encoding="utf-8")
    return path


def _old(report: Json) -> Json:
    """The report as #19 and #20 wrote it: no confidence in the rows."""
    old: Json = json.loads(json.dumps(report))
    old.pop("safety")
    for r in old["run_details"]:
        for d in r["documents"]:
            for x in d["fields"] + d["extra"]:
                x.pop("confidence")
            for k in ev.SAFETY:
                d["counts"].pop(k)
            for k in ev.SAFETY:
                r["overall"]["fields"].pop(k)
    return old


def test_two_saved_reports_combine_with_the_measure(
    one_document: None, tmp_path: Path
) -> None:
    make = lambda: StubReader({"a.pdf": reading(but("trip_price", 999.0, 0.9))})  # noqa: E731
    a = _save(evaluate(make), tmp_path / "a.json")
    b = _save(evaluate(make), tmp_path / "b.json")
    combined = ev.combine([a, b])
    assert combined["pooled"]["fields"]["incorrect_trusted"] == 2
    assert combined["summary"]["incorrect_trusted"]["mean"] == 1.0
    assert combined["safety"]["available"] is True


def test_an_old_report_gives_no_number_not_zero(
    one_document: None, tmp_path: Path
) -> None:
    make = lambda: StubReader({"a.pdf": reading(but("trip_price", 999.0, 0.9))})  # noqa: E731
    a = _save(_old(evaluate(make)), tmp_path / "a.json")
    b = _save(_old(evaluate(make)), tmp_path / "b.json")
    combined = ev.combine([a, b])
    f = combined["pooled"]["fields"]
    assert f["incorrect_trusted"] is None and f["right_trusted"] is None
    assert f["extra_trusted"] is None
    assert f["incorrect_present"] == 2 and f["wrong"] == 2  # no confidence needed
    assert combined["summary"]["incorrect_trusted"]["mean"] is None
    assert combined["safety"]["available"] is False
    assert "not available" in ev.summary_text(combined)


def test_an_old_and_a_new_report_give_no_number(
    one_document: None, tmp_path: Path
) -> None:
    make = lambda: StubReader({"a.pdf": reading(RIGHT)})  # noqa: E731
    a = _save(_old(evaluate(make)), tmp_path / "a.json")
    b = _save(evaluate(make), tmp_path / "b.json")
    combined = ev.combine([a, b])
    assert combined["pooled"]["fields"]["incorrect_trusted"] is None
    runs = [
        r["overall"]["fields"]["incorrect_trusted"] for r in combined["run_details"]
    ]
    assert runs == [None, 0]


def test_a_saved_measure_that_does_not_match_its_rows_is_refused(
    one_document: None, tmp_path: Path
) -> None:
    make = lambda: StubReader({"a.pdf": reading(but("trip_price", 999.0, 0.9))})  # noqa: E731
    report = evaluate(make)
    report["run_details"][0]["documents"][0]["counts"]["incorrect_trusted"] = 0
    a = _save(report, tmp_path / "a.json")
    b = _save(evaluate(make), tmp_path / "b.json")
    with pytest.raises(ev.BadCombine, match="counts do not match"):
        ev.combine([a, b])


# ---------------------------------------------------------------- no value, no need


def test_a_missing_field_and_a_right_absence_need_no_confidence(
    one_absent: None,
) -> None:
    """No stored value, no confidence to know: the measure stays available."""
    fields = [x for x in RIGHT if x[0] in ("departure_date", "trip_price")]
    report = evaluate(lambda: StubReader({"a.pdf": reading(fields)}))
    f = report["pooled"]["fields"]
    assert f["missing"] == 1 and f["right_absent"] == 1  # traveller, booking ref
    assert f["incorrect_trusted"] == 0 and f["right_trusted"] == 2
    assert report["safety"]["available"] is True


def test_an_old_report_that_stored_no_value_gives_zero_not_unknown(
    one_document: None, tmp_path: Path
) -> None:
    """An old run where the reader stored nothing: no value lacks a confidence."""
    make = lambda: StubReader({"a.pdf": reading([])})  # noqa: E731
    a = _save(_old(evaluate(make)), tmp_path / "a.json")
    b = _save(_old(evaluate(make)), tmp_path / "b.json")
    f = ev.combine([a, b])["pooled"]["fields"]
    assert f["missing"] == 8 and f["incorrect_trusted"] == 0


# ---------------------------------------------------------------- what one count is


def test_a_field_given_twice_wrong_is_one_incorrect_field(one_document: None) -> None:
    """The measure counts fields of the answer key, not stored copies: two wrong
    copies of one field are one incorrect field; the copies are listed apart."""
    fields = RIGHT[:2] + [("trip_price", 999.0, 2, 0.9), ("trip_price", 998.0, 2, 0.9)]
    report = evaluate(lambda: StubReader({"a.pdf": reading(fields)}))
    f = report["pooled"]["fields"]
    assert f["wrong"] == f["incorrect_trusted"] == 1
    assert doc(report, "a.pdf")["duplicates"] == ["trip_price"]


def test_extra_fields_are_counted_per_stored_value(one_document: None) -> None:
    """An extra field has no place in the answer key: each stored value counts."""
    extra: list[Field] = [
        ("return_date", "2026-08-20", 1, 0.9),
        ("return_date", "2026-08-21", 1, 0.5),
    ]
    f = counts(RIGHT + extra)
    assert f["extra"] == 2 and f["extra_trusted"] == 1


def test_a_combined_report_gives_the_numbers_of_the_same_runs_in_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    small_kit(tmp_path, monkeypatch, {"a.pdf": KEY, "b.pdf": ABSENT_KEY})
    reader = StubReader(
        {"a.pdf": reading(but("trip_price", 999.0, 0.9)), "b.pdf": reading(RIGHT)}
    )
    both = evaluate(lambda: reader, runs=2)
    a = _save(evaluate(lambda: reader), tmp_path / "a.json")
    b = _save(evaluate(lambda: reader), tmp_path / "b.json")
    combined = ev.combine([a, b])
    keys = ev.SAFETY + (
        "absent",
        "right_absent",
        "incorrect_absent",
        "incorrect_present",
    )

    def pick(c: Json) -> Json:
        return {k: c[k] for k in keys}

    x, y = both["pooled"], combined["pooled"]
    assert pick(x["fields"]) == pick(y["fields"])
    assert {n: pick(c) for n, c in x["by_field"].items()} == {
        n: pick(c) for n, c in y["by_field"].items()
    }
    assert {n: pick(a["fields"]) for n, a in x["by_kind"].items()} == {
        n: pick(a["fields"]) for n, a in y["by_kind"].items()
    }
    runs = [
        [pick(r["overall"]["fields"]) for r in z["run_details"]]
        for z in (both, combined)
    ]
    assert runs[0] == runs[1]
    assert (
        both["summary"]["incorrect_trusted"] == combined["summary"]["incorrect_trusted"]
    )


# ---------------------------------------------------------------- the N1 set


def test_the_n1_set_is_read_apart_from_the_kit() -> None:
    n1 = kit("n1")
    assert len(n1) == 9 and len(kit()) == 35
    assert all(d.id.startswith("n1/") and d.key is not None for d in n1)
    assert sorted(d.kind for d in n1).count("clean") == 6
    assert {d.kind for d in n1} == {"clean", "scan", "photo"}
    assert not {d.id for d in n1} & {d.id for d in kit()}


def test_an_unknown_set_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown set"):
        kit("n2")


def test_a_perfect_reader_on_n1_gives_every_absence_and_no_incorrect_value() -> None:
    report = evaluate(FixtureReader, document_set="n1")
    f = report["pooled"]["fields"]
    assert report["kit"]["documents"] == 9 and report["kit"]["set"] == "n1"
    assert f["expected"] == 39 and f["absent"] == 16 and f["right_absent"] == 16
    assert f["right_present"] == 23 and f["right_trusted"] == 23
    assert f["wrong"] == 0 and f["incorrect_trusted"] == 0


def test_a_report_of_the_kit_names_no_set() -> None:
    report = evaluate(FixtureReader)
    assert "set" not in report["kit"] and report["kit"]["documents"] == 35


def test_the_command_evaluates_the_n1_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The report of N1 goes to latest-n1.json: latest.json stays the report of the
    kit, which the page "Business numbers" shows (review F5)."""
    monkeypatch.setenv("CLAIMS_EVALUATION", str(tmp_path))
    assert ev.main(["--reader", "fixture", "--set", "n1"]) == 0
    saved = json.loads((tmp_path / "latest-n1.json").read_text(encoding="utf-8"))
    assert saved["kit"]["set"] == "n1" and saved["kit"]["documents"] == 9
    assert not (tmp_path / "latest.json").exists()
    assert len(list(tmp_path.glob("evaluation-fixture-n1-*.json"))) == 1
    assert "Safety" in capsys.readouterr().out


def test_the_summary_gives_no_number_when_a_run_has_no_confidence(
    one_document: None, tmp_path: Path
) -> None:
    """Review F2: one old run and one new run: no mean of the safety measure."""
    make = lambda: StubReader({"a.pdf": reading(RIGHT)})  # noqa: E731
    a = _save(_old(evaluate(make)), tmp_path / "a.json")
    b = _save(evaluate(make), tmp_path / "b.json")
    stat = ev.combine([a, b])["summary"]["incorrect_trusted"]
    assert stat == {"mean": None, "min": None, "max": None, "spread": None}


def test_a_null_copy_does_not_hide_a_wrong_copy(one_document: None) -> None:
    """Review F3: null first, then a wrong value at 0.9: a wrong value was stored."""
    f = counts(
        RIGHT[:2] + [("trip_price", None, 2, 0.9), ("trip_price", 999.0, 2, 0.9)]
    )
    assert f["wrong"] == 1 and f["incorrect_trusted"] == 1


def test_a_null_copy_does_not_make_a_right_value_wrong(one_document: None) -> None:
    """Review F4: a right value at 0.9 and a null copy: nothing wrong was stored."""
    f = counts(RIGHT + [("trip_price", None, 2, 0.9)])
    assert f["wrong"] == 0 and f["incorrect_trusted"] == 0 and f["right"] == 4


def test_the_set_does_not_go_with_combine(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        ev.main(["--combine", "a.json", "b.json", "--set", "n1"])


# ---------------------------------------------------------------- the N1 numbers again

FIXTURES = Path(__file__).parent / "fixtures" / "n1"
SAVED: Json = json.loads((FIXTURES / "answers.json").read_text("utf-8"))


def _replay(prompt: str, monkeypatch: pytest.MonkeyPatch) -> Json:
    """The harness on the N1 set with the saved answers of the 3 live runs, in the
    order of the set: classify, then the page, for each document."""
    files = [d.path.name for d in kit("n1")]
    queue = [
        SAVED["answers"][prompt][run][f][call]
        for run in ("1", "2", "3")
        for f in files
        for call in ("classify", "page")
    ]
    asked: list[bool] = []

    def endpoint(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://replay.invalid/v1/chat/completions"
        text = json.loads(request.content)["messages"][0]["content"][0]["text"]
        asked.append(text == CLASSIFY)
        assert asked[-1] == (len(asked) % 2 == 1), "classify, then the page"
        return httpx.Response(
            200, json={"choices": [{"message": {"content": queue.pop(0)}}]}
        )

    monkeypatch.setenv("NVIDIA_KEY", "test-not-a-key")
    monkeypatch.setenv("VLM_BASE_URL", "http://replay.invalid/v1")
    monkeypatch.setenv("VLM_MODEL", SAVED["source"]["model"])
    make, settings, meter = ev._omni(
        httpx.MockTransport(endpoint), lambda s: None, OmniSettings(prompt=prompt)
    )
    report = evaluate(make, 3, settings=settings, meter=meter, document_set="n1")
    assert queue == [], "every saved answer is used, no other request"
    return report


def _per_run(report: Json, key: str) -> list[int]:
    return [r["overall"]["fields"][key] for r in report["run_details"]]


@pytest.mark.parametrize(
    ("prompt", "before_absent", "after_absent"),
    [("v1", [6, 6, 6], [5, 5, 6]), ("schema-only", [9, 9, 6], [7, 7, 5])],
)
def test_the_harness_gives_the_n1_safety_numbers_before_and_after(
    monkeypatch: pytest.MonkeyPatch,
    prompt: str,
    before_absent: list[int],
    after_absent: list[int],
) -> None:
    """The numbers of the offline replay (`.dev-data/issue-125/after-v3`): before
    #125, 4 incorrect values at 0.9 per run; after, none at the threshold or above.
    The other incorrect values are still counted, below the threshold."""
    after = _replay(prompt, monkeypatch)
    assert _per_run(after, "incorrect_trusted") == [0, 0, 0]
    assert _per_run(after, "incorrect_absent") == after_absent
    with monkeypatch.context() as off:  # the reader as it was before #125
        off.setattr(labels, "verified", lambda *a: True)
        off.setattr(labels, "is_placeholder", lambda v: False)
        before = _replay(prompt, monkeypatch)
    assert _per_run(before, "incorrect_trusted") == [4, 4, 4]
    assert _per_run(before, "incorrect_trusted_absent") == [4, 4, 4]
    assert _per_run(before, "incorrect_absent") == before_absent
    for report in (before, after):
        for r in report["run_details"]:
            f = r["overall"]["fields"]
            assert f["absent"] == 16 and f["right_absent"] + f["incorrect_absent"] == 16
