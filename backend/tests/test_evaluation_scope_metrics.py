"""The supplementary numbers of the harness for the scope comparison (issue #126).

A field that several pages print can name them in the answer key
(`accepted_pages`). The official source page is still the key page (`page_right`,
`page_accuracy`, `verified`, `passed`). Next to it the harness gives the same
numbers for the accepted pages, so the owner of the project can see both before
he decides how they count. Nothing official moves: the first tests pin the
official numbers of MP1 and of the kit, and old saved reports must score and
combine as before.

Denominators (the same table as `HARNESS-INTERFACE.md`, section 1):

- right_present, incorrect_present, missing: the present fields (expected - absent)
- right_absent, incorrect_absent: the absent fields (the key says null)
- right_untrusted: right_present is right_trusted + right_untrusted
- accepted_page_accuracy: page_accepted / read (the stored values)
- verified_accepted: out of the expected fields, as `verified`
- wrong_page_trusted, unaccepted_page_trusted: counts of the values at 0.8 or more

No network: a stub of a perfect model answers from the answer keys.
"""

import copy
import json
from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from app.docintel import evaluate as ev
from app.docintel import omni
from app.docintel.evaluate import evaluate
from app.docintel.fixture import FixtureReader
from app.docintel.omni import OmniSettings
from app.models import Document, Extraction
from tests.test_evaluate import KEY, StubReader, _pdf, doc, row, small_kit
from tests.test_mp1_reader import key_of, perfect_model
from tests.test_omni_reader import URL

Json = dict[str, Any]
Field = tuple[str, object, int, float]  # name, value, page, confidence

# the numbers of a report of #19, #20 and #125, and the ones that are new
OLD_COUNTS = (
    "expected right wrong missing extra read page_right verified absent right_absent "
    "right_present incorrect_absent incorrect_present incorrect_trusted "
    "incorrect_trusted_absent incorrect_trusted_present right_trusted extra_trusted "
    "accuracy verified_accuracy page_accuracy"
).split()
NEW_COUNTS = ("page_accepted", "verified_accepted", "accepted_page_accuracy")
NEEDS_CONFIDENCE = ("right_untrusted", "wrong_page_trusted", "unaccepted_page_trusted")


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVIDIA_KEY", "test-key-value")
    monkeypatch.setenv("VLM_BASE_URL", URL)
    monkeypatch.setenv("VLM_MODEL", "omni-test")


# ================================================================ a small kit
# Three pages, so a page can be one that no key accepts.

BASE: list[Field] = [
    ("booking_reference", "WPR-001", 1, 0.9),
    ("departure_date", "2026-08-10", 1, 0.9),
    ("trip_price", 1840.0, 2, 0.9),
    ("traveller_name", "Anna Keller", 1, 0.9),
]


def with_pages(key: Json, name: str, pages: list[int]) -> Json:
    """The key with `accepted_pages` for one field (its page is the first one)."""
    fields = []
    for f in key["fields"]:
        if f["name"] == name:
            shown = {str(p): f"text of page {p}" for p in pages}
            f = f | {"snippet": shown[str(f["page"])], "accepted_pages": pages}
            f |= {"page_snippets": shown}
        fields.append(f)
    return key | {"fields": fields}


REPEATED = with_pages(KEY, "booking_reference", [1, 2])  # key page 1, 3 pages in all
ABSENT = KEY | {
    "fields": [
        f | {"value": None} if f["name"] == "booking_reference" else f
        for f in KEY["fields"]
    ]
}


def three_pages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: Json) -> None:
    small_kit(tmp_path, monkeypatch, {"a.pdf": key})
    (tmp_path / "mock" / "trip" / "a.pdf").write_bytes(_pdf(3, "a.pdf"))


@pytest.fixture
def repeated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    three_pages(tmp_path, monkeypatch, REPEATED)


@pytest.fixture
def plain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    three_pages(tmp_path, monkeypatch, KEY)


@pytest.fixture
def absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    three_pages(tmp_path, monkeypatch, ABSENT)


def reading(
    fields: list[Field], document_id: str | None = None
) -> Callable[[Document], Extraction]:
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
                            "document_id": document_id or document.id,
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


def moved(**changes: tuple[int, float]) -> list[Field]:
    """BASE with the page and the confidence of some fields changed."""
    return [(n, v, *changes.get(n, (p, c))) for n, v, p, c in BASE]


def report_of(fields: list[Field], **kwargs: Any) -> Json:
    return evaluate(lambda: StubReader({"a.pdf": reading(fields, **kwargs)}))


def counts(report: Json) -> Json:
    found: Json = report["pooled"]["fields"]
    return found


def saved(report: Json, path: Path) -> Path:
    path.write_text(ev.dump(report), encoding="utf-8")
    return path


# ---------------------------------------------------------------- the row


def test_a_value_on_a_later_accepted_page_is_not_the_key_page_but_is_accepted(
    repeated: None,
) -> None:
    """Right value, page 2 of the accepted pages [1, 2], key page 1."""
    report = report_of(moved(booking_reference=(2, 0.9)))
    r = row(doc(report, "a.pdf"), "booking_reference")
    assert (r["status"], r["page"], r["expected_page"]) == ("right", 2, 1)
    assert r["accepted_pages"] == [1, 2]
    assert r["page_right"] is False and r["page_accepted"] is True
    assert r["source"] == "wrong_page"  # the official word does not change


def test_a_value_on_a_page_that_no_key_accepts_is_not_accepted(repeated: None) -> None:
    r = row(
        doc(report_of(moved(booking_reference=(3, 0.9))), "a.pdf"), "booking_reference"
    )
    assert r["page"] == 3 and r["page_right"] is False and r["page_accepted"] is False


def test_a_key_page_value_is_both(repeated: None) -> None:
    r = row(doc(report_of(BASE), "a.pdf"), "booking_reference")
    assert r["page_right"] is True and r["page_accepted"] is True


def test_a_field_with_no_list_accepts_its_key_page_only(repeated: None) -> None:
    """departure_date has no list: page 2 is neither right nor accepted, and its row
    has no `accepted_pages` entry."""
    d = doc(report_of(moved(departure_date=(2, 0.9))), "a.pdf")
    r = row(d, "departure_date")
    assert r["page_right"] is False and r["page_accepted"] is False
    assert "accepted_pages" not in r
    assert "accepted_pages" in row(d, "booking_reference")
    ok = row(doc(report_of(BASE), "a.pdf"), "departure_date")
    assert ok["page_right"] is True and ok["page_accepted"] is True


def test_an_invalid_source_is_never_accepted_even_on_an_accepted_page(
    repeated: None,
) -> None:
    """A source of another document, on page 2 of the accepted pages."""
    report = report_of(moved(booking_reference=(2, 0.9)), document_id="D-other")
    r = row(doc(report, "a.pdf"), "booking_reference")
    assert r["source"] == "invalid" and r["page"] == 2
    assert r["page_right"] is False and r["page_accepted"] is False
    f = counts(report)
    assert (f["read"], f["page_accepted"], f["accepted_page_accuracy"]) == (4, 0, 0.0)


def test_a_page_after_the_last_page_is_not_accepted(repeated: None) -> None:
    r = row(
        doc(report_of(moved(booking_reference=(4, 0.9))), "a.pdf"), "booking_reference"
    )
    assert r["source"] == "invalid" and r["page_accepted"] is False


def test_nothing_read_has_no_page_to_judge(repeated: None) -> None:
    """None, as `page_right`: not False, not counted as read."""
    report = report_of([f for f in BASE if f[0] != "booking_reference"])
    r = row(doc(report, "a.pdf"), "booking_reference")
    assert r["status"] == "missing"
    assert r["page_right"] is None and r["page_accepted"] is None
    assert r["accepted_pages"] == [1, 2]  # the key says it, whatever the reader did
    f = counts(report)
    assert (f["missing"], f["read"], f["page_accepted"]) == (1, 3, 3)


def test_a_key_with_no_list_gives_rows_without_accepted_pages(plain: None) -> None:
    """The rows of a key of #19: the new entry `page_accepted` only, equal to
    `page_right`; no `accepted_pages` entry."""
    d = doc(report_of(moved(trip_price=(1, 0.9))), "a.pdf")
    assert all("accepted_pages" not in r for r in d["fields"])
    assert all(r["page_accepted"] == r["page_right"] for r in d["fields"])
    assert row(d, "trip_price")["page_accepted"] is False


# ---------------------------------------------------------------- the counts


def test_the_later_accepted_page_counts_in_the_supplementary_numbers_only(
    repeated: None,
) -> None:
    report = report_of(moved(booking_reference=(2, 0.9)))
    f, d = counts(report), doc(report, "a.pdf")
    # official
    assert (f["expected"], f["right"], f["read"]) == (4, 4, 4)
    assert (f["page_right"], f["verified"], f["page_accuracy"]) == (3, 3, 0.75)
    assert (d["passed"], d["counts"]["verified"]) == (False, 3)
    # supplementary: denominator `read` for the page, `expected` for the verified
    assert (f["page_accepted"], f["accepted_page_accuracy"]) == (4, 1.0)
    assert f["verified_accepted"] == 4
    assert d["passed_accepted"] is True
    docs = report["pooled"]["documents"]
    assert (docs["passed"], docs["pass_rate"]) == (0, 0.0)
    assert (docs["passed_accepted"], docs["pass_rate_accepted"]) == (1, 1.0)
    # a trusted value off the key page, on an accepted page
    assert (f["wrong_page_trusted"], f["unaccepted_page_trusted"]) == (1, 0)
    assert (f["right_untrusted"], f["right_trusted"]) == (0, 4)


def test_a_page_that_no_key_accepts_counts_in_both_forms(repeated: None) -> None:
    report = report_of(moved(booking_reference=(3, 0.9)))
    f, d = counts(report), doc(report, "a.pdf")
    assert (f["page_right"], f["page_accepted"], f["read"]) == (3, 3, 4)
    assert (f["page_accuracy"], f["accepted_page_accuracy"]) == (0.75, 0.75)
    assert (f["verified"], f["verified_accepted"]) == (3, 3)
    assert (d["passed"], d["passed_accepted"]) == (False, False)
    assert (f["wrong_page_trusted"], f["unaccepted_page_trusted"]) == (1, 1)


def test_the_same_page_under_the_limit_is_not_a_trusted_value(repeated: None) -> None:
    f = counts(report_of(moved(booking_reference=(3, 0.5))))
    assert (f["wrong_page_trusted"], f["unaccepted_page_trusted"]) == (0, 0)
    assert (f["right_untrusted"], f["right_trusted"]) == (1, 3)


@pytest.mark.parametrize(
    ("confidence", "trusted", "untrusted"), [(0.8, 4, 0), (0.79, 3, 1), (0.5, 3, 1)]
)
def test_right_untrusted_is_a_right_present_value_below_the_limit(
    plain: None, confidence: float, trusted: int, untrusted: int
) -> None:
    """right_present = right_trusted + right_untrusted; the limit is 0.8 and a value
    at 0.8 is trusted."""
    f = counts(report_of(moved(trip_price=(2, confidence))))
    assert (f["right_trusted"], f["right_untrusted"]) == (trusted, untrusted)
    assert f["right_present"] == trusted + untrusted == 4


def test_a_wrong_value_below_the_limit_is_not_a_right_untrusted_value(
    plain: None,
) -> None:
    f = counts(
        report_of(
            [(n, 999.0 if n == "trip_price" else v, p, 0.5) for n, v, p, _ in BASE]
        )
    )
    assert (f["wrong"], f["right_untrusted"], f["right_trusted"]) == (1, 3, 0)
    assert f["right_present"] == 3  # the 3 right values; the wrong one is apart


def test_a_wrong_value_on_a_wrong_page_is_a_trusted_wrong_page(plain: None) -> None:
    """Denominator: the stored values at 0.8 or more, right or wrong. The value is
    wrong, the page is not the key page: counted in both, once."""
    bad = [
        (n, 999.0, 1, c) if n == "trip_price" else (n, v, p, c) for n, v, p, c in BASE
    ]
    f = counts(report_of(bad))
    assert (f["wrong"], f["incorrect_trusted"], f["right_trusted"]) == (1, 1, 3)
    assert (f["wrong_page_trusted"], f["unaccepted_page_trusted"]) == (1, 1)
    assert f["page_accepted"] == f["page_right"] == 3


def test_a_right_absence_is_verified_in_both_forms(absent: None) -> None:
    report = report_of([f for f in BASE if f[0] != "booking_reference"])
    f = counts(report)
    assert (f["absent"], f["right_absent"], f["right_present"]) == (1, 1, 3)
    assert (f["read"], f["page_right"], f["page_accepted"]) == (3, 3, 3)
    assert (f["verified"], f["verified_accepted"], f["expected"]) == (4, 4, 4)
    assert doc(report, "a.pdf")["passed_accepted"] is True


def test_a_lure_for_an_absent_field_is_judged_against_page_1(absent: None) -> None:
    """The key page of an absent field is 1 (the form of N1), it has no list. A
    value stored for it on page 2 is a value read on a page that is neither: the
    same rule as the official `page_right`. At 0.9 it is a trusted wrong page."""
    report = report_of([("booking_reference", "WPR-001", 2, 0.9), *BASE[1:]])
    f, r = counts(report), row(doc(report, "a.pdf"), "booking_reference")
    assert r["expected"] is None and r["status"] == "wrong"
    assert (r["page_right"], r["page_accepted"]) == (False, False)
    assert (
        f["incorrect_absent"],
        f["right_absent"],
        f["incorrect_trusted_absent"],
    ) == (1, 0, 1)
    assert (f["read"], f["page_right"], f["page_accepted"]) == (4, 3, 3)
    assert (f["verified"], f["verified_accepted"]) == (3, 3)
    assert (f["wrong_page_trusted"], f["unaccepted_page_trusted"]) == (1, 1)
    assert (f["right_present"], f["right_untrusted"]) == (3, 0)  # the lure is not one


def test_a_failed_reader_keeps_the_right_absences_and_misses_the_rest(
    absent: None,
) -> None:
    """The reader raised: the present fields are missing, the absent one is right
    (nothing stored) and verified in both forms, the document fails in both forms."""
    report = evaluate(lambda: StubReader({"a.pdf": RuntimeError("down")}))
    f, d = counts(report), doc(report, "a.pdf")
    assert (f["missing"], f["right_present"], f["incorrect_present"]) == (3, 0, 0)
    assert (f["right_absent"], f["absent"]) == (1, 1)
    assert (f["verified"], f["verified_accepted"]) == (1, 1)
    assert (f["read"], f["page_accepted"], f["accepted_page_accuracy"]) == (0, 0, None)
    assert (d["passed"], d["passed_accepted"]) == (False, False)
    assert f["right_untrusted"] == f["wrong_page_trusted"] == 0  # no value stored


def test_an_extra_field_is_not_in_the_page_numbers(plain: None) -> None:
    """Extra values (not in the key) are counted apart, per stored value: they are
    not read values for the page share, but one at 0.9 is a trusted extra value."""
    extra: list[Field] = [("return_date", "2026-08-20", 2, 0.9)]
    f = counts(report_of(BASE + extra))
    assert (f["extra"], f["extra_trusted"]) == (1, 1)
    assert (f["read"], f["page_accepted"], f["right_untrusted"]) == (4, 4, 0)
    assert (f["wrong_page_trusted"], f["unaccepted_page_trusted"]) == (0, 0)


def test_the_present_and_absent_fields_add_up(absent: None) -> None:
    """Denominators: right + incorrect + missing among the present fields, and
    right + incorrect among the absent ones."""
    fields = [
        ("booking_reference", "WPR-001", 1, 0.5),  # a lure for the absent field
        ("departure_date", "2026-08-11", 1, 0.9),  # wrong
        ("trip_price", 1840.0, 2, 0.9),
    ]  # traveller_name is not read: missing
    f = counts(report_of(fields))
    assert f["expected"] - f["absent"] == 3
    assert f["right_present"] + f["incorrect_present"] + f["missing"] == 3
    assert (f["right_present"], f["incorrect_present"], f["missing"]) == (1, 1, 1)
    assert f["right_absent"] + f["incorrect_absent"] == f["absent"] == 1
    assert f["incorrect_absent"] == 1  # right absences are never mixed with values
    assert f["right"] == f["right_present"] + f["right_absent"] == 1


# ---------------------------------------------------------------- no value, no number


def without_confidence(report: Json) -> Json:
    """The rows of #19 and #20: no confidence, so no count that needs one."""
    out: Json = json.loads(json.dumps(report))
    for run in out["run_details"]:
        for d in run["documents"]:
            for x in d["fields"] + d["extra"]:
                x.pop("confidence")
            for k in ev.SAFETY:
                d["counts"].pop(k)
        for k in ev.SAFETY:
            run["overall"]["fields"].pop(k)
    return out


def test_a_report_with_no_confidence_gives_none_for_the_counts_that_need_one(
    repeated: None, tmp_path: Path
) -> None:
    fresh = [report_of(moved(booking_reference=(2, 0.9))) for _ in range(2)]
    files = [
        saved(without_confidence(r), tmp_path / f"{i}.json")
        for i, r in enumerate(fresh)
    ]
    combined = ev.combine(files)
    f = counts(combined)
    for name in NEEDS_CONFIDENCE:
        assert f[name] is None, name
        assert combined["summary"][name]["mean"] is None, name
        assert [r["overall"]["fields"][name] for r in combined["run_details"]] == [
            None
        ] * 2
    # what needs no confidence stays a number
    assert (f["page_accepted"], f["verified_accepted"], f["read"]) == (8, 8, 8)
    assert f["accepted_page_accuracy"] == 1.0
    text = ev.summary_text(combined)
    assert "not available" in text
    assert "values at >= 0.8 on a page that is not the key page: not available" in text


def test_a_run_with_one_value_without_confidence_gives_none_for_the_part(
    repeated: None, tmp_path: Path
) -> None:
    """A part with one stored value that has no confidence: None, not a count of
    the others. The other run of the same report keeps its numbers."""
    old = without_confidence(report_of(BASE))
    new = report_of(moved(booking_reference=(2, 0.9)))
    combined = ev.combine(
        [saved(old, tmp_path / "o.json"), saved(new, tmp_path / "n.json")]
    )
    runs = [
        r["overall"]["fields"]["wrong_page_trusted"] for r in combined["run_details"]
    ]
    assert runs == [None, 1] and counts(combined)["wrong_page_trusted"] is None


def test_no_value_read_gives_no_share(repeated: None) -> None:
    report = evaluate(lambda: StubReader({"a.pdf": reading([])}))
    f = counts(report)
    assert f["accepted_page_accuracy"] is None and f["page_accuracy"] is None
    assert f["page_accepted"] == 0 and f["read"] == 0
    assert "- Source page on an accepted page (supplementary" in ev.summary_text(report)


# ---------------------------------------------------------------- old reports


def as_old(report: Json) -> Json:
    """The report as #19, #20 and #125 wrote it: none of the new entries."""
    old: Json = copy.deepcopy(report)
    for run in old["run_details"]:
        for d in run["documents"]:
            for x in d["fields"]:
                x.pop("page_accepted")
                x.pop("accepted_pages", None)
            for k in NEW_COUNTS + NEEDS_CONFIDENCE:
                d["counts"].pop(k)
            d.pop("passed_accepted")
        for k in NEW_COUNTS + NEEDS_CONFIDENCE:
            run["overall"]["fields"].pop(k)
        for k in ("passed_accepted", "pass_rate_accepted"):
            run["overall"]["documents"].pop(k)
    return old


def official(counts_: Json) -> Json:
    return {k: counts_[k] for k in OLD_COUNTS}


def test_an_old_report_loads_and_falls_back_to_the_key_page(
    repeated: None, tmp_path: Path
) -> None:
    """No `page_accepted` in the rows: the key page stands for it, so the
    supplementary numbers equal the official ones. Not an error, not None."""
    new = report_of(moved(booking_reference=(2, 0.9)))
    old = as_old(new)
    assert "page_accepted" not in doc(old, "a.pdf")["fields"][0]
    report, _ = ev._load_saved(saved(old, tmp_path / "old.json"))
    again = ev._rescored(doc(report, "a.pdf"))
    c = again["counts"]
    assert (c["page_accepted"], c["verified_accepted"]) == (
        3,
        3,
    )  # page_right, verified
    assert (
        c["page_accepted"] == c["page_right"]
        and c["verified_accepted"] == c["verified"]
    )
    assert again["passed_accepted"] is again["passed"] is False
    assert c["accepted_page_accuracy"] == c["page_accuracy"] == 0.75
    assert (c["wrong_page_trusted"], c["unaccepted_page_trusted"]) == (1, 1)


def test_two_old_reports_combine_as_before(repeated: None, tmp_path: Path) -> None:
    fresh = [report_of(moved(booking_reference=(2, 0.9))) for _ in range(2)]
    old = ev.combine(
        [saved(as_old(r), tmp_path / f"o{i}.json") for i, r in enumerate(fresh)]
    )
    new = ev.combine([saved(r, tmp_path / f"n{i}.json") for i, r in enumerate(fresh)])
    assert official(counts(old)) == official(counts(new))
    assert (
        old["pooled"]["documents"]["passed"]
        == new["pooled"]["documents"]["passed"]
        == 0
    )
    assert counts(old)["page_accepted"] == 6 and counts(new)["page_accepted"] == 8


def test_an_old_report_and_a_new_report_of_a_kit_without_lists_combine(
    plain: None, tmp_path: Path
) -> None:
    a = as_old(report_of(BASE))
    b = report_of(moved(trip_price=(1, 0.9)))
    combined = ev.combine(
        [saved(a, tmp_path / "a.json"), saved(b, tmp_path / "b.json")]
    )
    f = counts(combined)
    assert (f["page_right"], f["page_accepted"], f["read"]) == (7, 7, 8)


def test_an_old_report_does_not_combine_with_a_report_that_has_accepted_pages(
    repeated: None, tmp_path: Path
) -> None:
    """The answer keys differ: one of them names accepted pages."""
    a = as_old(report_of(BASE))
    b = report_of(BASE)
    with pytest.raises(ev.BadCombine, match="answer keys"):
        ev.combine([saved(a, tmp_path / "a.json"), saved(b, tmp_path / "b.json")])


def test_two_reports_scored_with_different_lists_do_not_combine(
    repeated: None, tmp_path: Path
) -> None:
    a, b = report_of(BASE), report_of(BASE)
    for d in b["run_details"][0]["documents"]:
        row(d, "booking_reference")["accepted_pages"] = [1, 2, 3]
    with pytest.raises(ev.BadCombine, match="answer keys"):
        ev.combine([saved(a, tmp_path / "a.json"), saved(b, tmp_path / "b.json")])


def test_the_signature_holds_the_accepted_pages_only_when_the_row_has_them(
    repeated: None,
) -> None:
    new = report_of(BASE)["run_details"][0]
    fields = {f[0]: f for d in ev._documents_signature(new) for f in d[6]}
    assert fields["booking_reference"][-1] == (1, 2)
    assert len(fields["booking_reference"]) == len(fields["departure_date"]) + 1
    old = as_old({"run_details": [new]})["run_details"][0]
    again = as_old({"run_details": [new]})["run_details"][0]
    assert ev._documents_signature(old) == ev._documents_signature(again)
    assert all(len(f) == 4 for d in ev._documents_signature(old) for f in d[6])


def test_a_saved_new_count_that_does_not_match_its_rows_is_refused(
    repeated: None, tmp_path: Path
) -> None:
    report = report_of(moved(booking_reference=(2, 0.9)))
    d = report["run_details"][0]["documents"][0]
    d["counts"]["verified_accepted"] = 3
    other = report_of(BASE)
    with pytest.raises(ev.BadCombine, match="counts do not match"):
        ev.combine(
            [saved(report, tmp_path / "a.json"), saved(other, tmp_path / "b.json")]
        )
    report = report_of(BASE)
    report["run_details"][0]["documents"][0]["passed_accepted"] = False
    with pytest.raises(ev.BadCombine, match="counts do not match"):
        ev.combine(
            [saved(report, tmp_path / "c.json"), saved(other, tmp_path / "d.json")]
        )


# ================================================================ MP1

FIRST = "mp1-01-repair-invoice-carried-forward.pdf"
SCAN3 = "mp1-03-cancellation-confirmation-scan.pdf"
MEDICAL = "mp1-02-medical-invoice-continued.pdf"
QUOTE = "mp1-04-repair-quote-terms.pdf"
SCAN6 = "mp1-06-medical-report-scan.pdf"
BOOKING = "mp1-07-booking-confirmation-no-price.pdf"
Change = Callable[[str, list[Json]], None]


def key_field(file: str, name: str) -> Json:
    found: Json = next(f for f in key_of(file)["fields"] if f["name"] == name)
    return found


def move(file: str, answers: list[Json], name: str, to: int, snippet: str) -> None:
    """The value of a field of the key, given in the answer of page `to` (from 1)
    only, with this snippet."""
    value = key_field(file, name)["value"]
    for number, a in enumerate(answers, 1):
        a["values"][name] = value if number == to else None
        a["snippets"].pop(name, None)
    answers[to - 1]["snippets"][name] = snippet


def on_page_2(file: str, name: str) -> tuple[str, int, str]:
    f = key_field(file, name)
    return name, 2, f["page_snippets"]["2"] if "page_snippets" in f else f["snippet"]


def later_page(file: str, answers: list[Json]) -> None:
    """mp1-01: the invoice number only on page 2 (accepted pages 1, 2, 3)."""
    if file == FIRST:
        move(file, answers, *on_page_2(file, "invoice_number"))


def scan_pages(file: str, answers: list[Json]) -> None:
    """The scans: a value in the block of the wrong page, and one in the block of
    page 2 that is accepted (patient name of mp1-06: pages 1, 2)."""
    if file == SCAN3:  # cancellation date: key page 1, no list
        move(
            file,
            answers,
            "cancellation_date",
            2,
            key_field(file, "cancellation_date")["snippet"],
        )
    if file == SCAN6:
        move(file, answers, *on_page_2(file, "patient_name"))


def total_with_options(file: str, answers: list[Json]) -> None:
    if file == QUOTE:
        answers[1]["values"]["total"] = 9763.5
        answers[1]["snippets"]["total"] = "CHF 9,763.50"


def lure(file: str, answers: list[Json]) -> None:
    if file == BOOKING:  # the deposit as the trip price: the key says null
        answers[1]["values"]["trip_price"] = 430.0
        answers[1]["snippets"]["trip_price"] = "CHF 430.00"


def conflict(file: str, answers: list[Json]) -> None:
    """mp1-04: the right total on page 2, another amount on page 3."""
    if file == QUOTE:
        answers[2]["values"]["total"] = 50000.0
        answers[2]["snippets"]["total"] = "CHF 50,000.00"


def left_out(file: str, answers: list[Json]) -> None:
    if file == MEDICAL:
        answers[0]["values"]["patient_name"] = None
        answers[0]["snippets"].pop("patient_name")


def mixed(file: str, answers: list[Json]) -> None:
    for change in (later_page, scan_pages, total_with_options, lure, left_out):
        change(file, answers)


CHANGES: dict[str, Change | None] = {
    "perfect": None,
    "later_page": later_page,
    "scan_pages": scan_pages,
    "total_with_options": total_with_options,
    "lure": lure,
    "conflict": conflict,
    "left_out": left_out,
    "mixed": mixed,
}


@cache
def scenario(name: str, scope: str) -> Json:
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("NVIDIA_KEY", "test-key-value")
        patch.setenv("VLM_BASE_URL", URL)
        patch.setenv("VLM_MODEL", "omni-test")
        make, named, meter = ev._omni(
            transport=perfect_model(CHANGES[name]), options=OmniSettings(scope=scope)
        )
        return ev.evaluate(make, 1, settings=named, meter=meter, document_set="mp1")


SCOPES = pytest.mark.parametrize("scope", omni.SCOPES)


def pooled_fields(name: str, scope: str) -> Json:
    found: Json = scenario(name, scope)["pooled"]["fields"]
    return found


def part(name: str, scope: str, kind: str) -> Json:
    found: Json = scenario(name, scope)["pooled"]["by_kind"][kind]
    return found


def mp1_row(name: str, scope: str, file: str, field: str) -> Json:
    return row(doc(scenario(name, scope), file), field)


# ---------------------------------------------------------------- official numbers


def numbers(report: Json) -> Json:
    """The official numbers of the pooled report, as the harness gave them before
    the supplementary numbers (taken from the unchanged module, 2026-11-27)."""
    p = report["pooled"]
    return {
        "fields": official(p["fields"]),
        "documents": {
            k: p["documents"][k]
            for k in "count passed pass_rate errors classified_right".split()
        }
        | {"classification_accuracy": p["documents"]["classification_accuracy"]},
        "line_items": p["line_items"],
    }


PERFECT_FIELDS = {
    "expected": 35, "right": 35, "wrong": 0, "missing": 0, "extra": 0, "read": 33,
    "page_right": 33, "verified": 35, "absent": 2, "right_absent": 2,
    "right_present": 33, "incorrect_absent": 0, "incorrect_present": 0,
    "incorrect_trusted": 0, "incorrect_trusted_absent": 0,
    "incorrect_trusted_present": 0, "right_trusted": 26, "extra_trusted": 0,
    "accuracy": 1.0, "verified_accuracy": 1.0, "page_accuracy": 1.0,
}  # fmt: skip


@SCOPES
def test_the_official_numbers_of_mp1_with_a_perfect_model_are_the_same(
    scope: str,
) -> None:
    report = scenario("perfect", scope)
    assert numbers(report) == {
        "fields": PERFECT_FIELDS,
        "documents": {
            "count": 8, "passed": 8, "pass_rate": 1.0, "errors": 0,
            "classified_right": 8, "classification_accuracy": 1.0,
        },
        "line_items": {
            "documents": 5, "documents_right": 4, "expected": 39, "read": 35,
            "right": 35, "accuracy": 0.8974,
        },
    }  # fmt: skip
    kinds = report["pooled"]["by_kind"]
    assert (
        kinds["clean"]["fields"]["expected"],
        kinds["scan"]["fields"]["expected"],
    ) == (28, 7)
    assert (
        kinds["clean"]["fields"]["right_trusted"],
        kinds["scan"]["fields"]["right_trusted"],
    ) == (26, 0)
    assert kinds["clean"]["line_items"] == {
        "documents": 4, "documents_right": 4, "expected": 35, "read": 35,
        "right": 35, "accuracy": 1.0,
    }  # fmt: skip
    assert kinds["scan"]["line_items"]["expected"] == 4
    assert kinds["scan"]["line_items"]["right"] == 0


@SCOPES
def test_a_perfect_model_gives_the_supplementary_numbers_of_the_official_ones(
    scope: str,
) -> None:
    f = pooled_fields("perfect", scope)
    assert (f["page_accepted"], f["accepted_page_accuracy"]) == (33, 1.0)
    assert f["verified_accepted"] == 35
    assert (f["right_untrusted"], f["right_trusted"], f["right_present"]) == (7, 26, 33)
    assert (f["wrong_page_trusted"], f["unaccepted_page_trusted"]) == (0, 0)
    d = scenario("perfect", scope)["pooled"]["documents"]
    assert (d["passed_accepted"], d["pass_rate_accepted"]) == (8, 1.0)
    assert part("perfect", scope, "scan")["fields"]["right_untrusted"] == 7  # a box
    assert part("perfect", scope, "clean")["fields"]["right_untrusted"] == 0


def test_the_kit_with_the_fixture_reader_has_the_same_official_numbers() -> None:
    report = evaluate(FixtureReader)
    assert numbers(report) == {
        "fields": {
            "expected": 132, "right": 132, "wrong": 0, "missing": 0, "extra": 0,
            "read": 132, "page_right": 132, "verified": 132, "absent": 0,
            "right_absent": 0, "right_present": 132, "incorrect_absent": 0,
            "incorrect_present": 0, "incorrect_trusted": 0,
            "incorrect_trusted_absent": 0, "incorrect_trusted_present": 0,
            "right_trusted": 132, "extra_trusted": 0, "accuracy": 1.0,
            "verified_accuracy": 1.0, "page_accuracy": 1.0,
        },
        "documents": {
            "count": 35, "passed": 35, "pass_rate": 1.0, "errors": 0,
            "classified_right": 35, "classification_accuracy": 1.0,
        },
        "line_items": {
            "documents": 23, "documents_right": 23, "expected": 73, "read": 73,
            "right": 73, "accuracy": 1.0,
        },
    }  # fmt: skip
    f = counts(report)
    assert (f["page_accepted"], f["verified_accepted"]) == (132, 132)
    assert (
        f["right_untrusted"],
        f["wrong_page_trusted"],
        f["unaccepted_page_trusted"],
    ) == (0, 0, 0)
    assert report["pooled"]["documents"]["passed_accepted"] == 35
    runs = report["run_details"][0]["documents"]
    assert all("accepted_pages" not in r for d in runs for r in d["fields"])
    assert "accepted" not in ev.summary_text(report)  # the text of a kit report


# ---------------------------------------------------------------- MP1, one change each


@SCOPES
def test_a_repeated_value_given_on_page_2_only_is_accepted_not_the_key_page(
    scope: str,
) -> None:
    """mp1-01 invoice number, accepted pages 1, 2, 3, given on page 2 only."""
    r = mp1_row("later_page", scope, FIRST, "invoice_number")
    assert (r["status"], r["page"], r["expected_page"]) == ("right", 2, 1)
    assert r["accepted_pages"] == [1, 2, 3]
    assert (r["page_right"], r["page_accepted"], r["source"]) == (
        False,
        True,
        "wrong_page",
    )
    assert r["confidence"] == omni.VERIFIED
    c = doc(scenario("later_page", scope), FIRST)["counts"]
    assert (c["expected"], c["right"], c["read"]) == (5, 5, 5)
    assert (c["page_right"], c["page_accepted"]) == (4, 5)
    assert (c["verified"], c["verified_accepted"]) == (4, 5)
    assert (c["wrong_page_trusted"], c["unaccepted_page_trusted"]) == (1, 0)
    d = doc(scenario("later_page", scope), FIRST)
    assert (d["passed"], d["passed_accepted"]) == (False, True)
    f = pooled_fields("later_page", scope)
    assert (f["page_right"], f["page_accuracy"]) == (32, 0.9697)
    assert (f["page_accepted"], f["accepted_page_accuracy"]) == (33, 1.0)
    assert (f["verified"], f["verified_accepted"]) == (34, 35)
    assert (f["right"], f["accuracy"]) == (35, 1.0)  # the value is right
    docs = scenario("later_page", scope)["pooled"]["documents"]
    assert (docs["passed"], docs["passed_accepted"]) == (7, 8)
    assert (docs["pass_rate"], docs["pass_rate_accepted"]) == (0.875, 1.0)
    assert (f["wrong_page_trusted"], f["unaccepted_page_trusted"]) == (1, 0)


@SCOPES
def test_scan_values_in_the_block_of_another_page(scope: str) -> None:
    """mp1-03 cancellation date (key page 1, no list) in the block of page 2: not
    accepted. mp1-06 patient name (pages 1, 2) in the block of page 2: accepted.
    A scan value is stored at 0.5: not a trusted wrong page."""
    date = mp1_row("scan_pages", scope, SCAN3, "cancellation_date")
    assert (date["status"], date["page"], date["confidence"]) == (
        "right",
        2,
        omni.UNVERIFIED,
    )
    assert (date["page_right"], date["page_accepted"]) == (False, False)
    assert "accepted_pages" not in date
    name = mp1_row("scan_pages", scope, SCAN6, "patient_name")
    assert (name["page"], name["accepted_pages"]) == (2, [1, 2])
    assert (name["page_right"], name["page_accepted"]) == (False, True)
    scans = part("scan_pages", scope, "scan")["fields"]
    assert (scans["expected"], scans["read"]) == (7, 7)
    assert (scans["page_right"], scans["page_accuracy"]) == (5, 0.7143)
    assert (scans["page_accepted"], scans["accepted_page_accuracy"]) == (6, 0.8571)
    assert (scans["verified"], scans["verified_accepted"]) == (5, 6)
    assert (scans["wrong_page_trusted"], scans["unaccepted_page_trusted"]) == (0, 0)
    assert (scans["right_untrusted"], scans["right"]) == (7, 7)
    clean = part("scan_pages", scope, "clean")["fields"]  # nothing moved there
    assert clean["page_right"] == clean["page_accepted"] == clean["read"] == 26
    assert part("scan_pages", scope, "scan")["documents"]["passed"] == 0
    assert part("scan_pages", scope, "scan")["documents"]["passed_accepted"] == 1


@SCOPES
def test_a_wrong_total_with_options_at_0_9_is_a_trusted_wrong_value_on_the_key_page(
    scope: str,
) -> None:
    r = mp1_row("total_with_options", scope, QUOTE, "total")
    assert (r["status"], r["read"], r["page"], r["confidence"]) == (
        "wrong",
        9763.5,
        2,
        0.9,
    )
    assert (r["page_right"], r["page_accepted"]) == (True, True)
    f = pooled_fields("total_with_options", scope)
    assert (f["right"], f["wrong"], f["missing"]) == (34, 1, 0)
    assert (f["right_present"], f["incorrect_present"], f["incorrect_absent"]) == (
        32,
        1,
        0,
    )
    assert (f["incorrect_trusted"], f["incorrect_trusted_present"]) == (1, 1)
    assert (f["right_trusted"], f["right_untrusted"]) == (25, 7)
    assert (f["read"], f["page_right"], f["page_accepted"]) == (33, 33, 33)
    assert (f["verified"], f["verified_accepted"]) == (34, 34)
    assert (f["wrong_page_trusted"], f["unaccepted_page_trusted"]) == (0, 0)


@SCOPES
def test_a_lure_for_an_absent_field_at_0_5(scope: str) -> None:
    """mp1-07: the deposit as the trip price, which the key says is absent. The
    absent field has the key page 1 and no list: the lure on page 2 is on neither."""
    r = mp1_row("lure", scope, BOOKING, "trip_price")
    assert (r["expected"], r["status"], r["read"]) == (None, "wrong", 430.0)
    assert (r["page"], r["confidence"]) == (2, omni.UNVERIFIED)
    assert (r["page_right"], r["page_accepted"]) == (False, False)
    f = pooled_fields("lure", scope)
    assert (f["absent"], f["right_absent"], f["incorrect_absent"]) == (2, 1, 1)
    assert (f["right"], f["wrong"], f["right_present"], f["incorrect_present"]) == (
        34,
        1,
        33,
        0,
    )
    assert (f["incorrect_trusted"], f["incorrect_trusted_absent"]) == (0, 0)
    assert (f["read"], f["page_right"], f["page_accepted"]) == (34, 33, 33)
    assert (f["page_accuracy"], f["accepted_page_accuracy"]) == (0.9706, 0.9706)
    assert (f["verified"], f["verified_accepted"]) == (34, 34)
    assert (f["wrong_page_trusted"], f["unaccepted_page_trusted"]) == (0, 0)
    assert (f["right_trusted"], f["right_untrusted"]) == (26, 7)


@SCOPES
def test_a_right_value_downgraded_by_a_conflict_is_right_untrusted(scope: str) -> None:
    """mp1-04: the total right on page 2, another amount on page 3. The reader keeps
    the first value and marks it for a person (0.5)."""
    r = mp1_row("conflict", scope, QUOTE, "total")
    assert (r["status"], r["read"], r["page"], r["confidence"]) == (
        "right",
        8488.5,
        2,
        0.5,
    )
    assert (r["page_right"], r["page_accepted"]) == (True, True)
    f = pooled_fields("conflict", scope)
    assert (f["right"], f["wrong"]) == (35, 0)
    assert (f["right_trusted"], f["right_untrusted"], f["right_present"]) == (25, 8, 33)
    assert (f["incorrect_trusted"], f["verified_accepted"]) == (0, 35)


@SCOPES
def test_a_value_left_out_is_missing_among_the_present_fields(scope: str) -> None:
    r = mp1_row("left_out", scope, MEDICAL, "patient_name")
    assert (r["status"], r["read"], r["page_right"], r["page_accepted"]) == (
        "missing",
        None,
        None,
        None,
    )
    f = pooled_fields("left_out", scope)
    assert (f["missing"], f["right_present"], f["incorrect_present"]) == (1, 32, 0)
    assert f["expected"] - f["absent"] == 33 == 32 + 1 + 0  # the present fields
    assert (f["right_absent"], f["incorrect_absent"]) == (2, 0)  # the absent ones
    assert (f["read"], f["page_right"], f["page_accepted"]) == (32, 32, 32)
    assert (f["verified"], f["verified_accepted"]) == (34, 34)
    assert (f["right_trusted"], f["right_untrusted"]) == (25, 7)
    docs = scenario("left_out", scope)["pooled"]["documents"]
    assert (docs["passed"], docs["passed_accepted"]) == (7, 7)


@SCOPES
def test_all_the_changes_together_in_both_forms(scope: str) -> None:
    """The six changes on six different documents, counted by hand. Right 32, wrong 2
    (the total with options, the lure), missing 1."""
    f = pooled_fields("mixed", scope)
    assert (f["expected"], f["right"], f["wrong"], f["missing"], f["extra"]) == (
        35,
        32,
        2,
        1,
        0,
    )
    assert (f["absent"], f["right_absent"], f["incorrect_absent"]) == (2, 1, 1)
    assert (f["right_present"], f["incorrect_present"]) == (31, 1)
    assert (f["read"], f["page_right"], f["page_accepted"]) == (33, 29, 31)
    assert (f["page_accuracy"], f["accepted_page_accuracy"]) == (0.8788, 0.9394)
    assert (f["verified"], f["verified_accepted"]) == (29, 31)
    assert (f["incorrect_trusted"], f["incorrect_trusted_present"]) == (1, 1)
    assert (f["right_trusted"], f["right_untrusted"]) == (24, 7)
    assert f["right_trusted"] + f["right_untrusted"] == f["right_present"]
    assert (f["wrong_page_trusted"], f["unaccepted_page_trusted"]) == (1, 0)
    docs = scenario("mixed", scope)["pooled"]["documents"]
    assert (docs["passed"], docs["pass_rate"]) == (2, 0.25)
    assert (docs["passed_accepted"], docs["pass_rate_accepted"]) == (4, 0.5)
    clean, scan = (part("mixed", scope, k)["fields"] for k in ("clean", "scan"))
    assert (clean["expected"], clean["right"], clean["wrong"], clean["missing"]) == (
        28,
        25,
        2,
        1,
    )
    assert (clean["read"], clean["page_right"], clean["page_accepted"]) == (26, 24, 25)
    assert (
        scan["expected"],
        scan["read"],
        scan["page_right"],
        scan["page_accepted"],
    ) == (7, 7, 5, 6)
    for name in (
        "page_accepted",
        "verified_accepted",
        "right_untrusted",
        "wrong_page_trusted",
    ):
        assert clean[name] + scan[name] == f[name], name


def test_both_scopes_give_the_same_supplementary_numbers_for_the_same_answers() -> None:
    for name in CHANGES:
        page, document = (pooled_fields(name, scope) for scope in omni.SCOPES)
        assert page == document, name


@SCOPES
def test_the_perfect_model_gives_the_same_rows_with_the_new_entries(scope: str) -> None:
    """Row by row: `page_accepted` equals `page_right` when the model answers on the
    key page, and a key with a list adds `accepted_pages`."""
    docs = scenario("perfect", scope)["run_details"][0]["documents"]
    rows = [r for d in docs for r in d["fields"]]
    assert all(r["page_accepted"] == r["page_right"] for r in rows)
    listed = [r for r in rows if "accepted_pages" in r]
    assert len(listed) == 12  # the repeated fields of the set (MP1-SPEC, section 3)
    assert all(r["page"] in r["accepted_pages"] for r in listed if r["page"])


# ---------------------------------------------------------------- the other measures


@SCOPES
def test_classification_and_the_lines_are_apart_and_per_kind(scope: str) -> None:
    report = scenario("later_page", scope)
    p = report["pooled"]
    assert (
        p["documents"]["classified_right"],
        p["documents"]["classification_accuracy"],
    ) == (8, 1.0)
    clean, scan = p["by_kind"]["clean"], p["by_kind"]["scan"]
    assert (
        clean["documents"]["classified_right"],
        scan["documents"]["classified_right"],
    ) == (6, 2)
    assert (clean["line_items"]["expected"], clean["line_items"]["right"]) == (35, 35)
    assert (scan["line_items"]["expected"], scan["line_items"]["right"]) == (4, 0)
    assert (p["line_items"]["expected"], p["line_items"]["right"]) == (39, 35)


def test_a_wrong_type_is_counted_in_the_classification_only(plain: None) -> None:
    reader = StubReader({"a.pdf": reading(BASE)}, kinds={"a.pdf": "receipt"})
    report = evaluate(lambda: reader)
    d, f = report["pooled"]["documents"], counts(report)
    assert (d["classified_right"], d["classification_accuracy"]) == (0, 0.0)
    assert (d["passed"], d["passed_accepted"]) == (1, 1)  # the type is not a field
    assert (f["right"], f["verified_accepted"]) == (4, 4)


def test_the_seconds_per_document_are_the_time_of_classify_and_extract() -> None:
    """A clock that moves 0.5 s at each reading: 0.5 for classify, 0.5 for extract."""
    ticks = iter(x / 2 for x in range(10_000))
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("NVIDIA_KEY", "test-key-value")
        patch.setenv("VLM_BASE_URL", URL)
        patch.setenv("VLM_MODEL", "omni-test")
        make, named, meter = ev._omni(
            transport=perfect_model(), options=OmniSettings(scope="page")
        )
        report = ev.evaluate(
            make,
            1,
            settings=named,
            meter=meter,
            document_set="mp1",
            clock=lambda: next(ticks),
        )
    p = report["pooled"]
    assert p["seconds"] == {
        "count": 8,
        "total": 8.0,
        "mean": 1.0,
        "median": 1.0,
        "min": 1.0,
        "max": 1.0,
    }
    assert p["by_kind"]["clean"]["seconds"]["total"] == 6.0
    assert p["by_kind"]["scan"]["seconds"]["total"] == 2.0
    assert all(
        d["seconds"]["total"] == 1.0 for d in report["run_details"][0]["documents"]
    )


# ---------------------------------------------------------------- the text


@SCOPES
def test_the_summary_text_has_one_more_line_for_a_set_with_accepted_pages(
    scope: str,
) -> None:
    text = ev.summary_text(scenario("later_page", scope))
    [line] = [
        x
        for x in text.splitlines()
        if x.startswith("- Source page on an accepted page")
    ]
    assert "33 of 33" in line  # page_accepted of read
    assert "35 of 35" in line  # verified_accepted of expected
    assert "8 of 8" in line  # documents, every field verified on an accepted page
    assert "on a page that is not the key page: 1" in line
    assert "on a page that is not accepted: 0" in line
    assert "right values below 0.8: 7 of 33" in line
    perfect = ev.summary_text(scenario("perfect", scope))
    assert "- Source page right (of the values read):" in perfect  # the official line


def test_a_text_that_has_no_numbers_says_so_and_never_writes_0(
    repeated: None, tmp_path: Path
) -> None:
    fresh = [report_of(moved(booking_reference=(2, 0.9))) for _ in range(2)]
    combined = ev.combine(
        [
            saved(without_confidence(r), tmp_path / f"{i}.json")
            for i, r in enumerate(fresh)
        ]
    )
    [line] = [
        x
        for x in ev.summary_text(combined).splitlines()
        if x.startswith("- Source page on an accepted page")
    ]
    assert line.count("not available") == 3
    assert "right values below 0.8: not available" in line
    assert "not the key page: 0" not in line and "not accepted: 0" not in line


def test_the_text_of_a_kit_without_lists_is_not_changed(plain: None) -> None:
    text = ev.summary_text(report_of(BASE))
    assert "accepted" not in text
