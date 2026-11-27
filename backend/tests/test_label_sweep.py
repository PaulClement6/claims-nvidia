"""The label check (#125, `app/docintel/labels.py`) against every printed candidate.

Offline and deterministic: no model, no network. For each document with a text layer
(the mock kit of `app.demo._kit()` and the safety set `data/mock/n1`) and each
safeguarded identifier, date and amount field of its type, every printed candidate of
the page is tried: every date (ISO and DD.MM.YYYY), every number, every identifier-like
token. The candidate goes in as a bare value (the worst case: a snippet with no label
in it, as the `schema-only` prompt gives it). A candidate that equals the value of
the answer key is RIGHT: it must be verified (a false downgrade is a bug). Any other
candidate is WRONG (for a field that the page does not hold, all are wrong): it must
not be verified (a false accept is listed, with its reason, or it is a bug).

Out of scope: free text fields (cause, diagnosis, offence, item_description,
damage_description) and the lines are not verified by meaning here, and they are not
swept. Organisation and person fields are checked on the values of the answer keys
and on the names of the doctors.
"""

import json
import re
from collections import defaultdict
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any, NamedTuple

import pytest

from app import demo
from app.docintel import omni
from app.docintel.compare import Kind, compare_field, field_kind
from app.docintel.labels import SAFEGUARDED, verified
from app.docintel.schemas import SCHEMAS
from app.models import DocumentType
from tests.reader_contract import MOCK

ISO = re.compile(r"\d{4}-\d{2}-\d{2}")
SWISS = re.compile(r"\d{2}\.\d{2}\.\d{4}")
TOKEN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9\-/._]*[A-Za-z0-9])?")
DOCTOR = re.compile(r"Dr\. med\. [A-Z][a-z]+ [A-Z][a-z]+")
SWEPT: dict[str, Kind] = {"id": "identifier", "date": "date", "amount": "amount"}

# A wrong candidate that is verified, with the reason why it is not a bug: the same
# printed value carries a label that fits the field. Nothing is listed here when the
# sweep finds none.
COINCIDENCES: dict[tuple[str, str, str], str] = {}


class Sample(NamedTuple):
    document: str
    source: str  # "kit" or "n1"
    type: str
    text: str
    key: dict[str, Any]  # field -> value of the answer key (None: not on the page)
    key_snippets: dict[str, str]


class Result(NamedTuple):
    document: str
    source: str
    field: str
    kind: str
    snippet: str
    right: bool
    accepted: bool


def samples() -> Iterator[Sample]:
    """Every page with a text layer, with the answer key of its document."""
    paths = [(f"{f}/{n}", "kit", p) for (f, n), p in demo._kit().items()]
    paths += [(f"n1/{p.name}", "n1", p) for p in sorted((MOCK / "n1").glob("*.pdf"))]
    for name, source, path in paths:
        key_file = path.with_suffix(".expected.json")
        if not key_file.is_file():
            continue
        key = json.loads(key_file.read_text("utf-8"))
        for page in omni.pages(path.read_bytes()):
            if page.text.strip():
                yield Sample(
                    name,
                    source,
                    key["document_type"],
                    page.text,
                    {f["name"]: f["value"] for f in key["fields"]},
                    {f["name"]: f.get("snippet") or "" for f in key["fields"]},
                )


def candidates(kind: str, text: str) -> list[tuple[str, Any]]:
    """(printed text, typed value) of every possible answer of this kind."""
    found: dict[str, Any] = {}
    if kind == "date":
        for form, pattern in (("iso", ISO), ("swiss", SWISS)):
            for m in pattern.finditer(text):
                s = m.group()
                parts = [int(p) for p in re.split(r"[-.]", s)]
                y, mo, d = parts if form == "iso" else parts[::-1]
                try:
                    found[s] = date(y, mo, d)
                except ValueError:
                    continue
    elif kind == "amount":
        for m in omni.NUMBER.finditer(text):
            found[m.group()] = omni._numbers(m.group())[0]
    else:
        for m in TOKEN.finditer(text):
            s = m.group()
            if len(s) >= 3 and (any(c.isdigit() for c in s) or "-" in s):
                found[s] = s
    return list(found.items())


def is_right(kind: str, expected: Any, value: Any) -> bool:
    if expected is None:
        return False
    got = value.isoformat() if isinstance(value, date) else value
    return compare_field(SWEPT[kind], expected, got).status == "right"


_cache: list[Result] = []


def sweep() -> list[Result]:
    if _cache:
        return _cache
    for sample in samples():
        fields = SCHEMAS[DocumentType(sample.type)].model_fields
        for name, kind in SAFEGUARDED.items():
            if name not in fields or kind not in SWEPT:
                continue
            for snippet, value in candidates(kind, sample.text):
                right = is_right(kind, sample.key.get(name), value)
                accepted = verified(sample.type, name, value, snippet, sample.text)
                _cache.append(
                    Result(
                        sample.document,
                        sample.source,
                        name,
                        kind,
                        snippet,
                        right,
                        accepted,
                    )
                )
    return _cache


def test_the_sweep_has_something_to_sweep() -> None:
    results = sweep()
    right = [r for r in results if r.right]
    assert len({r.document for r in results if r.source == "kit"}) >= 20
    assert len({r.document for r in results if r.source == "n1"}) >= 5
    assert len(right) >= 60 and len(results) >= 800
    for kind in SWEPT:
        assert any(r.right and r.kind == kind for r in results)


def test_every_present_field_of_the_key_is_a_candidate_of_its_page() -> None:
    """The sweep is not vacuous: each safeguarded id, date or amount of an answer key
    with a text layer is among the candidates, as a right one."""
    results = sweep()
    for sample in samples():
        fields = SCHEMAS[DocumentType(sample.type)].model_fields
        for name, kind in SAFEGUARDED.items():
            if name in fields and kind in SWEPT and sample.key.get(name) is not None:
                hit = [
                    r
                    for r in results
                    if (r.document, r.field) == (sample.document, name) and r.right
                ]
                assert hit, f"{sample.document}: {name} is no candidate"


def test_no_right_value_is_downgraded_on_the_kit_and_on_the_safety_set() -> None:
    wrong = [r for r in sweep() if r.right and not r.accepted]
    assert not wrong, "false downgrades:\n" + "\n".join(
        f"{r.document}: {r.field} = {r.snippet!r}" for r in wrong
    )


def false_accepts() -> list[Result]:
    return [r for r in sweep() if not r.right and r.accepted]


def test_no_wrong_value_is_accepted_unless_it_is_a_listed_coincidence() -> None:
    found = [
        r
        for r in false_accepts()
        if (r.document, r.field, r.snippet) not in COINCIDENCES
    ]
    assert not found, "false accepts:\n" + "\n".join(
        f"{r.document}: {r.field} = {r.snippet!r}" for r in found
    )
    assert set(COINCIDENCES) <= {
        (r.document, r.field, r.snippet) for r in false_accepts()
    }


def test_the_sweep_report(capsys: pytest.CaptureFixture[str]) -> None:
    """Prints, per field, the right candidates, the false downgrades and the false
    accepts (run with -s to see it)."""
    table: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    for r in sweep():
        row = table[f"{r.kind}:{r.field}"]
        row[0] += 1
        row[1] += r.right
        row[2] += r.right and not r.accepted
        row[3] += (not r.right) and r.accepted
    with capsys.disabled():
        print(
            "\nlabel sweep: field, candidates, right, false downgrades, false accepts"
        )
        for field, (n, right, down, acc) in sorted(table.items()):
            print(f"  {field:28} {n:5} {right:4} {down:3} {acc:3}")
    assert all(row[2] == 0 for row in table.values())


def typed(kind: str, value: Any) -> Any:
    """The value as the schema types it: a date, a float, a str."""
    if kind == "date":
        return date.fromisoformat(value)
    return float(value) if kind == "amount" else value


def test_the_snippet_of_the_answer_key_is_verified() -> None:
    """A perfect model gives the snippet of the key (with its words around the value,
    or the bare value): it must keep VERIFIED."""
    checked = 0
    for sample in samples():
        fields = SCHEMAS[DocumentType(sample.type)].model_fields
        for name, kind in SAFEGUARDED.items():
            value = sample.key.get(name)
            if name in fields and kind in SWEPT and value is not None:
                snippet = sample.key_snippets[name]
                assert verified(
                    sample.type, name, typed(kind, value), snippet, sample.text
                ), f"{sample.document}: {name} = {snippet!r}"
                checked += 1
    assert checked >= 60


# ------------------------------------------------- organisations and persons


def test_the_organisation_and_person_values_of_the_keys_are_kept() -> None:
    checked = 0
    for sample in samples():
        fields = SCHEMAS[DocumentType(sample.type)].model_fields
        for name, kind in SAFEGUARDED.items():
            value = sample.key.get(name)
            if name in fields and kind in ("organisation", "person") and value:
                for snippet in (value, sample.key_snippets[name] or value):
                    assert verified(sample.type, name, value, snippet, sample.text), (
                        f"{sample.document}: {name} = {value!r}"
                    )
                checked += 1
    assert checked >= 25


def test_a_doctor_is_never_the_provider_of_a_document_of_the_kit() -> None:
    """The provider is the clinic. The name of the doctor, on the same pages, reads as
    a person and is not verified; the clinic is."""
    doctors = 0
    for sample in samples():
        if sample.type not in ("medical_invoice", "medical_report"):
            continue
        for doctor in set(DOCTOR.findall(sample.text)):
            assert not verified(sample.type, "provider", doctor, doctor, sample.text)
            doctors += 1
        clinic = sample.key.get("provider")
        if clinic:
            assert verified(sample.type, "provider", clinic, clinic, sample.text)
    assert doctors >= 4


# ------------------------------------------------ the shapes of a real snippet
# A model does not always give the bare value: it gives the line, two lines, or some
# words before the value. Each of these shapes of a right value must stay verified.


class Shape(NamedTuple):
    document: str
    field: str
    shape: str
    snippet: str


def printed_places(kind: str, value: Any, text: str) -> list[tuple[int, int]]:
    """Where the value is printed on the page (start, end): the ISO and the Swiss
    form of a date, every number with the value of an amount, the text of an id."""
    if kind == "amount":
        found = []
        for m in omni.NUMBER.finditer(text):
            number = omni._numbers(m.group())[0]
            if abs(number - float(value)) < 0.005:
                found.append(m.span())
        return found
    if kind == "date":
        day = date.fromisoformat(value)
        forms = [day.isoformat(), day.strftime("%d.%m.%Y")]
    else:
        forms = [value]
    return [m.span() for f in forms for m in re.finditer(re.escape(f), text)]


def whole_words(text: str, start: int, end: int) -> str:
    """text[start:end], without a word that the start cuts in two."""
    piece = text[start:end]
    if 0 < start and text[start - 1].isalnum() and text[start].isalnum():
        cut = re.search(r"\s", piece)
        piece = piece[cut.end() :] if cut else piece
    return piece.strip()


def shapes_of(text: str, a: int, b: int) -> list[tuple[str, str]]:
    """The snippets that a model may give for the value at text[a:b]."""
    lines = text.split("\n")
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line) + 1)
    i = max(k for k in range(len(lines)) if starts[k] <= a)
    first, last = starts[i], starts[i] + len(lines[i])
    out = [("bare", text[a:b]), ("line", text[first:last].strip())]
    if i > 0:
        out.append(("previous line + line", text[starts[i - 1] : last].strip()))
    if i + 1 < len(lines):
        out.append(
            ("line + next line", text[first : starts[i + 1] + len(lines[i + 1])])
        )
    out.append(("25 characters + value", whole_words(text, max(0, a - 25), b)))
    return [(name, snippet.strip()) for name, snippet in out]


def all_shapes() -> Iterator[tuple[Shape, Sample, Any]]:
    for sample in samples():
        fields = SCHEMAS[DocumentType(sample.type)].model_fields
        for name, kind in SAFEGUARDED.items():
            value = sample.key.get(name)
            if name not in fields or kind not in SWEPT or value is None:
                continue
            typed_value = typed(kind, value)
            for a, b in printed_places(kind, value, sample.text):
                for shape, snippet in shapes_of(sample.text, a, b):
                    if (
                        snippet
                        and len(snippet) <= omni.MAX_SNIPPET
                        and omni._shown(snippet, sample.text)
                        and omni._holds(snippet, typed_value)
                    ):
                        yield (
                            Shape(sample.document, name, shape, snippet),
                            sample,
                            (typed_value),
                        )


def test_every_shape_of_a_right_value_stays_verified() -> None:
    """The bare value, the line, the line with the one above or the one below, and the
    25 characters before the value: 0 false downgrades on the kit and the N1 pages."""
    seen: dict[str, int] = defaultdict(int)
    wrong = []
    for shape, sample, value in all_shapes():
        seen[shape.shape] += 1
        if not verified(sample.type, shape.field, value, shape.snippet, sample.text):
            wrong.append(shape)
    assert set(seen) == {
        "bare",
        "line",
        "previous line + line",
        "line + next line",
        "25 characters + value",
    }
    assert min(seen.values()) >= 50 and sum(seen.values()) >= 400, dict(seen)
    assert not wrong, f"{len(wrong)} false downgrades:\n" + "\n".join(
        f"{w.document}: {w.field} [{w.shape}] {w.snippet!r}" for w in wrong
    )


# ------------------------------------------- the fields of the live run S7 (#20)

S7 = json.loads(
    (Path(__file__).parent / "fixtures" / "kit" / "s7-snippets.json").read_text("utf-8")
)["fields"]


def _s7_verdicts() -> Iterator[tuple[dict[str, Any], bool | None, bool]]:
    """For each field that the reader stored at 0.9 in the three runs (before #125):
    (the entry, right per the answer key (None: no kind or no key), verified now)."""
    paths = {f"{f}/{n}": p for (f, n), p in demo._kit().items()}
    pages: dict[str, list[Any]] = {}
    for entry in S7:
        path = paths[entry["document"]]
        if entry["document"] not in pages:
            pages[entry["document"]] = omni.pages(path.read_bytes())
        text = pages[entry["document"]][entry["page"] - 1].text
        key = json.loads(path.with_suffix(".expected.json").read_text("utf-8"))
        expected = {f["name"]: f["value"] for f in key["fields"]}
        kind = field_kind(entry["document_type"], entry["field"])
        right = None
        if kind is not None and entry["field"] in expected:
            wanted = expected[entry["field"]]
            right = (
                wanted is not None
                and compare_field(kind, wanted, entry["value"]).status == "right"
            )
        value = entry["value"]
        scope = SAFEGUARDED.get(entry["field"])
        if scope is not None:
            value = typed(scope, value)
        yield (
            entry,
            right,
            verified(
                entry["document_type"], entry["field"], value, entry["snippet"], text
            ),
        )


def test_the_s7_fixture_is_the_fields_stored_at_0_9() -> None:
    assert len(S7) >= 90
    assert all(e["page"] >= 1 and e["snippet"].strip() for e in S7)
    assert all(e["runs"] and set(e["runs"]) <= {"r1-s7", "r2-s7", "r3-s7"} for e in S7)
    assert len(
        {(e["document"], e["field"], e["snippet"], str(e["value"])) for e in S7}
    ) == len(S7)


def test_every_right_value_that_s7_stored_at_0_9_stays_verified() -> None:
    rows = list(_s7_verdicts())
    right = [(e, ok) for e, r, ok in rows if r]
    assert len(right) >= 60
    lost = [e for e, ok in right if not ok]
    assert not lost, "false downgrades:\n" + "\n".join(
        f"{e['document']}: {e['field']} = {e['value']!r} [{e['snippet']!r}]"
        for e in lost
    )


def test_every_wrong_safeguarded_value_that_s7_stored_at_0_9_is_not_verified() -> None:
    """The doctors that the model gave as the provider were stored at 0.9 and are
    wrong: the safeguards must not verify them."""
    rows = list(_s7_verdicts())
    wrong = [(e, ok) for e, r, ok in rows if r is False and e["field"] in SAFEGUARDED]
    assert wrong, "the live run has wrong values that are in scope"
    kept = [e for e, ok in wrong if ok]
    assert not kept, "wrong values still verified:\n" + "\n".join(
        f"{e['document']}: {e['field']} = {e['value']!r} [{e['snippet']!r}]"
        for e in kept
    )
