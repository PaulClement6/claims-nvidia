"""The two pilot documents of issue #126: a text PDF of two pages and a scan of three
pages, made by `scripts/mockdocs/pilot.py`.

The pilot sends one request per document with every page image in it, to learn if the
hosted endpoint takes such a request. The documents are NOT part of MP1, N1 or the
kit, and NOT under `data/mock/`: the runner generates them into its own folder. So
these tests generate them into a temporary folder.

The table `SPEC` is typed by hand from the design notes of the two documents (not read
from the generator): every test compares the generated files with it. The checks are
the functions of `test_mp1_dataset.py` (they return a list of problems), so that the
positive tests (no problem) and the negative tests (the problem is named) use the same
function. The perfect model is the stub of `test_mp1_reader.py`.

What the scan tests can and cannot prove: the scan is an image. Its ground truth is its
synthetic source, the strings that the generator printed before the page was made into
an image (`build` returns them, the manifest holds their SHA-256). Nothing here proves
that a reader can read the scan, or that the endpoint takes the request.
"""

import base64
import copy
import hashlib
import importlib
import json
import logging
import math
import re
import sys
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest
from pypdf import PdfReader

from app import demo
from app.docintel import evaluate, labels, omni
from app.docintel.compare import BadAnswerKey
from app.docintel.fixture import FixtureReader
from app.models import Document, Extraction
from tests.reader_contract import MOCK
from tests.test_mp1_dataset import (
    FOOT,
    FORM_WORDS,
    GENERIC,
    HEAD_MEDICAL,
    HEAD_REPAIR,
    IBAN,
    PHONE,
    SMALL_NUMBER,
    UID,
    WORD,
    YEAR,
    Accepted,
    Fld,
    Spec,
    arithmetic_problems,
    counts_of,
    expected_key,
    flat,
    has,
    many,
    page_texts,
    problems,
    scan_problems,
    source_problems,
    typed,
)
from tests.test_mp1_reader import Calls, page_answers
from tests.test_omni_reader import URL

ROOT = MOCK.parents[1]
MP1 = MOCK / "mp1"


@pytest.fixture(autouse=True)
def _omni_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVIDIA_KEY", "test-key-value")
    monkeypatch.setenv("VLM_BASE_URL", URL)
    monkeypatch.setenv("VLM_MODEL", "omni-test")


# ===================================================== the specification, by hand
# One row per field: name, value, key page, key snippet, accepted pages (page, the
# evidence printed on it, the label text before the value there). One row per line:
# description, quantity, unit price, amount, page.

A = "pilot-01-medical-invoice-two-pages"
B = "pilot-02-repair-quote-scan-three-pages"
INVOICE_A = "Invoice no.: SRC-61942"
QUOTE_B = "Quote no.: ZD/26/0318"
MP1_LARGEST_ANSWER = 2851  # characters of the largest right answer of MP1 (indent 2)

SPEC: tuple[Spec, ...] = (
    Spec(
        A,
        "medical_invoice",
        2,
        "text",
        (
            Fld(
                "invoice_number",
                "SRC-61942",
                1,
                "SRC-61942",
                many("SRC-61942", {1: INVOICE_A, 2: INVOICE_A}),
            ),
            Fld("invoice_date", None, 1, None),
            Fld(
                "provider",
                "Sonnrain Rehabilitation AG",
                1,
                "Sonnrain Rehabilitation AG",
            ),
            Fld("patient_name", "Elmar Sigrist", 1, "Elmar Sigrist"),
            Fld("total", 883.0, 2, "CHF 883.00"),
        ),
        (
            ("Gait analysis with report", 1, 145.0, 145.0, 1),
            ("Shockwave treatment, per session", 3, 78.5, 235.5, 1),
            ("Kinesiology taping, per application", 4, 19.75, 79.0, 1),
            ("Resistance training, supervised, 40 min", 6, 34.25, 205.5, 2),
            ("Insole, custom made, pair", 1, 218.0, 218.0, 2),
        ),
        (
            (
                "Sonnrain Rehabilitation AG",
                "Talmattweg 31, 9435 Heerbrugg",
                "Tel. +41 71 555 67 24",
                "Elmar Sigrist",
                "Haldenstrasse 17",
                "7320 Sargans",
                "Switzerland",
                "Invoice no.:",
                "SRC-61942",
                "Treatment:",
                "2026-09-29 to 2026-10-19",
                "Invoice",
                "Patient:",
                *HEAD_MEDICAL,
                "The table goes on overleaf",
                "Page 1 of 2",
            ),
            (
                INVOICE_A,
                *HEAD_MEDICAL,
                "Total",
                "CHF 883.00",
                "Invoices are due 30 days after receipt.",
                "Page 2 of 2",
            ),
        ),
        {2: (INVOICE_A, True)},
        "document",
        (
            "table_over_page_break",
            "repeated_header",
            "value_on_two_pages",
            "total_on_last_page",
            "absent_field",
            "misleading_candidate",
        ),
        {"invoice_date": "lure"},
    ),
    Spec(
        B,
        "repair_quote",
        3,
        "scan",
        (
            Fld(
                "quote_number",
                "ZD/26/0318",
                1,
                "ZD/26/0318",
                many("ZD/26/0318", {1: QUOTE_B, 2: QUOTE_B, 3: QUOTE_B}),
            ),
            Fld("quote_date", "2026-11-19", 3, "2026-11-19"),
            Fld("repair_company", "Zelgli Dachbau GmbH", 1, "Zelgli Dachbau GmbH"),
            Fld("total", 2442.6, 2, "CHF 2,442.60"),
            Fld("customer_name", "Hedwig Marti", 1, "Hedwig Marti"),
        ),
        (
            ("Take off old edge flashing, per metre", 18, 12.4, 223.2, 2),
            ("Zinc edge flashing, supply and fit, per metre", 18, 47.6, 856.8, 2),
            ("Downpipe, zinc, per metre", 14, 36.9, 516.6, 2),
            ("Roofer, per hour", 9, 94.0, 846.0, 2),
        ),
        (
            (
                "Zelgli Dachbau GmbH",
                "Rebmattstrasse 5, 5610 Wohlen",
                "Tel. +41 56 555 83 49",
                "Hedwig Marti",
                "Lindenrain 14",
                "5620 Bremgarten",
                "Switzerland",
                "Quote no.:",
                "ZD/26/0318",
                "Site visit:",
                "2026-11-08",
                "Quote",
                "Flashing and a gutter for the garage roof, both replaced.",
                "The work and the total are on page 2.",
                "Page 1 of 3",
            ),
            (QUOTE_B, *HEAD_REPAIR, "Total", "CHF 2,442.60", "Page 2 of 3"),
            (
                QUOTE_B,
                "Notes",
                "1. The work starts six weeks after the order.",
                "2. Ladders and fall protection are included in the prices.",
                "3. Frost or heavy wind can move the date.",
                "Quote date:",
                "2026-11-19",
                "Page 3 of 3",
            ),
        ),
        {2: (QUOTE_B, True), 3: (QUOTE_B, True)},
        "neutral",
        (
            "scan",
            "repeated_header",
            "value_on_three_pages",
            "value_on_last_page_only",
            "misleading_candidate",
        ),
        {},
    ),
)
BY_STEM = {s.stem: s for s in SPEC}
STEMS = [s.stem for s in SPEC]
TEXT = BY_STEM[A]
SCAN = BY_STEM[B]
EXPECTED_COUNTS = {
    "documents": 2,
    "pages": 5,
    "text_documents": 1,
    "scan_documents": 1,
    "text_pages": 2,
    "scan_pages": 3,
    "fields_scored": 10,
    "fields_present": 9,
    "fields_absent": 1,
    "fields_repeated": 2,
    "line_items": 9,
    "line_items_text": 5,
    "line_items_scan": 4,
}
PAGE_SUMS = {A: {1: "459.50", 2: "423.50"}, B: {2: "2442.60"}}
TOTALS = {A: "883.00", B: "2442.60"}
IMAGE_TOKENS = math.ceil(1241 / 32) * math.ceil(1754 / 32)  # an A4 page at 150 DPI


# ===================================================================== the files
Made = tuple[Path, dict[str, list[list[str]]]]


def pilot_module() -> Any:
    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    return importlib.import_module("mockdocs.pilot")


@pytest.fixture(scope="module")
def made(tmp_path_factory: pytest.TempPathFactory) -> Made:
    """One run of the generator: its folder and what it printed on each page."""
    folder = tmp_path_factory.mktemp("pilot") / "out"
    printed: dict[str, list[list[str]]] = pilot_module().build(folder)
    return folder, printed


def pdf_of(made: Made, stem: str) -> Path:
    return made[0] / f"{stem}.pdf"


def key_in(folder: Path, stem: str) -> dict[str, Any]:
    key: dict[str, Any] = json.loads((folder / f"{stem}.expected.json").read_text())
    return key


def manifest_in(folder: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((folder / "manifest.json").read_text("utf-8"))
    return data


# ================================================================ 1 the contents


def test_the_folder_holds_the_two_documents_their_keys_and_the_manifest_only(
    made: Made,
) -> None:
    assert sorted(p.name for p in made[0].iterdir()) == sorted(
        [f"{s}.pdf" for s in STEMS]
        + [f"{s}.expected.json" for s in STEMS]
        + ["manifest.json"]
    )
    assert sorted(made[1]) == sorted(f"{s}.pdf" for s in STEMS)


def test_the_counts_of_the_table_are_the_expected_counts() -> None:
    keys = {s.stem: expected_key(s) for s in SPEC}
    kinds = {s.stem: s.kind for s in SPEC}
    assert counts_of(keys, kinds) == EXPECTED_COUNTS


def test_the_counts_are_computed_from_the_generated_keys_and_the_manifest(
    made: Made,
) -> None:
    keys = {s: key_in(made[0], s) for s in STEMS}
    kinds = {
        d["file"].removesuffix(".pdf"): d["kind"]
        for d in manifest_in(made[0])["documents"]
    }
    assert counts_of(keys, kinds) == EXPECTED_COUNTS
    assert manifest_in(made[0])["counts"] == EXPECTED_COUNTS


def test_the_manifest_tells_what_the_pilot_is(made: Made) -> None:
    m = manifest_in(made[0])
    assert set(m) == {
        "set",
        "version",
        "issue",
        "purpose",
        "language",
        "documents",
        "counts",
    }
    assert (m["set"], m["version"], m["issue"], m["language"]) == (
        "pilot",
        1,
        126,
        "en",
    )
    assert isinstance(m["purpose"], str) and "pilot" in m["purpose"].lower()
    assert [d["file"] for d in m["documents"]] == [f"{s}.pdf" for s in STEMS]


@pytest.mark.parametrize("stem", STEMS)
def test_the_manifest_lists_each_document_as_the_table_says(
    made: Made, stem: str
) -> None:
    spec = BY_STEM[stem]
    [entry] = [
        d for d in manifest_in(made[0])["documents"] if d["file"] == f"{stem}.pdf"
    ]
    present = [f for f in spec.fields if f.value is not None]
    wanted: dict[str, Any] = {
        "file": f"{stem}.pdf",
        "document_type": spec.type,
        "kind": spec.kind,
        "pages": spec.pages,
        "fields_present": len(present),
        "fields_absent": len(spec.fields) - len(present),
        "line_items": len(spec.lines),
        "repeated": {
            f.name: [a.page for a in f.accepted] for f in present if f.accepted
        },
        "absent": spec.absent,
        "scenarios": list(spec.scenarios),
    }
    if spec.kind == "scan":
        hashes = entry.pop("source_text_sha256")
        assert len(hashes) == spec.pages
        assert all(re.fullmatch(r"[0-9a-f]{64}", h) for h in hashes)
        assert len(set(hashes)) == spec.pages  # no two pages share a text
    assert entry == wanted


def test_the_documents_are_one_text_pdf_of_two_pages_and_one_scan_of_three(
    made: Made,
) -> None:
    assert [len(PdfReader(pdf_of(made, s)).pages) for s in STEMS] == [2, 3]
    assert [key_in(made[0], s)["pages"] for s in STEMS] == [2, 3]
    assert [BY_STEM[s].kind for s in STEMS] == ["text", "scan"]
    # a type of schemas.py that a reader reads: not a photo, not unknown
    types = {s.type for s in SPEC}
    assert types == {"medical_invoice", "repair_quote"}
    assert not types & {"damage_photo", "unknown"}


# ============================================================ 2 the answer keys


@pytest.mark.parametrize("stem", STEMS)
def test_each_answer_key_equals_the_table(made: Made, stem: str) -> None:
    key, want = key_in(made[0], stem), expected_key(BY_STEM[stem])
    assert set(key) == set(want)
    for name in ("document_type", "pages", "line_pages", "line_items"):
        assert key[name] == want[name], name
    assert key.get("variant") == want.get("variant")
    got = {f["name"]: f for f in key["fields"]}
    assert len(got) == len(key["fields"]) == len(want["fields"])
    assert got == {f["name"]: f for f in want["fields"]}


@pytest.mark.parametrize("stem", STEMS)
def test_the_types_of_the_values_of_a_key_are_the_ones_of_the_kit(
    made: Made, stem: str
) -> None:
    key = key_in(made[0], stem)
    for line in key["line_items"]:
        assert type(line["quantity"]) is int, line
        assert type(line["unit_price"]) in (int, float), line
        assert type(line["amount"]) in (int, float), line
    for f in key["fields"]:
        assert type(f["page"]) is int and f["confidence"] == 1.0
        assert f["value"] is None or type(f["value"]) in (str, float), f
        if f["value"] is None:
            assert f["page"] == 1 and "snippet" not in f  # the form of N1
    assert all(type(p) is int for p in key["line_pages"])


@pytest.mark.parametrize("stem", STEMS)
def test_every_key_passes_the_check_of_the_harness(made: Made, stem: str) -> None:
    evaluate._check_key(key_in(made[0], stem))
    key, error = evaluate._answer_key(pdf_of(made, stem))
    assert error is None and key == key_in(made[0], stem)


def test_the_one_absent_field_is_a_lure_and_has_no_snippet(made: Made) -> None:
    absent = {
        (s, f["name"])
        for s in STEMS
        for f in key_in(made[0], s)["fields"]
        if f["value"] is None
    }
    assert absent == {(A, "invoice_date")}
    docs = manifest_in(made[0])["documents"]
    assert {n: v for d in docs for n, v in d["absent"].items()} == {
        "invoice_date": "lure"
    }


def test_the_documents_have_the_features_that_the_pilot_needs(made: Made) -> None:
    """Between them: a value on the last page only, a value repeated with accepted pages
    (a running header with its label), a table over a page break in the text PDF, at
    most six lines per document, an absent field."""
    keys = {s: key_in(made[0], s) for s in STEMS}
    last_only = [
        (s, f["name"])
        for s, k in keys.items()
        for f in k["fields"]
        if f["value"] is not None
        and f["page"] == k["pages"]
        and "accepted_pages" not in f
    ]
    assert last_only == [(A, "total"), (B, "quote_date")]
    repeated = {
        (s, f["name"]): f["accepted_pages"]
        for s, k in keys.items()
        for f in k["fields"]
        if "accepted_pages" in f
    }
    assert repeated == {(A, "invoice_number"): [1, 2], (B, "quote_number"): [1, 2, 3]}
    assert sorted(set(keys[A]["line_pages"])) == [1, 2]  # the table over the break
    assert all(len(k["line_items"]) <= 6 for k in keys.values())


# ============================================================== 3 the arithmetic


def test_each_priced_line_is_quantity_times_unit_price() -> None:
    n = 0
    for spec in SPEC:
        for _description, quantity, unit, amount, _ in spec.lines:
            assert quantity is not None and unit is not None
            n += 1
            assert Decimal(quantity) * Decimal(str(unit)) == Decimal(str(amount))
    assert n == 9


def test_the_sums_per_page_and_the_totals_are_those_of_the_design(made: Made) -> None:
    for stem in STEMS:
        key = key_in(made[0], stem)
        by_page: dict[int, Decimal] = {}
        for line, page in zip(key["line_items"], key["line_pages"], strict=True):
            by_page[page] = by_page.get(page, Decimal(0)) + Decimal(str(line["amount"]))
        assert by_page == {p: Decimal(v) for p, v in PAGE_SUMS[stem].items()}
        total = {f["name"]: f["value"] for f in key["fields"]}["total"]
        assert Decimal(str(total)) == Decimal(TOTALS[stem]) == sum(by_page.values())
        assert arithmetic_problems(key) == []


# ======================================================= 4 the text PDF, as read


def test_the_text_pdf_passes_every_check_against_its_key(made: Made) -> None:
    assert problems(pdf_of(made, A), key_in(made[0], A)) == []
    assert problems(pdf_of(made, A), expected_key(TEXT)) == []


def test_every_page_of_the_text_pdf_has_a_text_layer_with_the_texts_of_the_design(
    made: Made,
) -> None:
    texts = page_texts(pdf_of(made, A))
    assert len(texts) == 2 and all(t.strip() for t in texts)
    missing = [
        (n, s)
        for n, (strings, text) in enumerate(zip(TEXT.printed, texts, strict=True), 1)
        for s in strings
        if not has(text, s)
    ]
    assert missing == []


def test_the_foot_line_is_the_last_text_of_each_page_and_the_header_the_first(
    made: Made,
) -> None:
    texts = page_texts(pdf_of(made, A))
    for n, text in enumerate(texts, 1):
        assert flat(text).endswith(flat(FOOT.format(n, 2))), n
    assert flat(texts[1]).startswith(flat(INVOICE_A))
    strings = made[1][f"{A}.pdf"]
    assert strings[1][0] == INVOICE_A  # drawn first on page 2, one string
    assert [p[-1] for p in strings] == [FOOT.format(1, 2), FOOT.format(2, 2)]


def test_the_letterhead_is_on_page_1_only(made: Made) -> None:
    texts = page_texts(pdf_of(made, A))
    assert "Tel." in texts[0] and "Tel." not in texts[1]
    assert "Sonnrain" in texts[0] and "Sonnrain" not in texts[1]


def test_the_table_runs_over_the_page_break_each_row_whole_on_one_page(
    made: Made,
) -> None:
    texts = page_texts(pdf_of(made, A))
    key = key_in(made[0], A)
    assert key["line_pages"] == [1, 1, 1, 2, 2]
    for line, page in zip(key["line_items"], key["line_pages"], strict=True):
        cells = [
            line["description"],
            str(line["quantity"]),
            f"{line['unit_price']:,.2f}",
            f"{line['amount']:,.2f}",
        ]
        assert all(has(texts[page - 1], c) for c in cells), line
        other = texts[2 - page]
        assert not has(other, line["description"]), line
    # the column head is printed again on page 2, so that page 2 is a table page
    assert all(has(t, h) for t in texts for h in HEAD_MEDICAL)


@pytest.mark.parametrize("spec", [TEXT])
def test_the_value_of_a_field_is_on_the_accepted_pages_and_on_no_other_page(
    made: Made, spec: Spec
) -> None:
    texts = page_texts(pdf_of(made, spec.stem))
    for f in spec.fields:
        if f.value is None:
            continue
        pages = [a.page for a in f.accepted] or [f.page]
        printing = [n for n, t in enumerate(texts, 1) if omni._holds(t, f.value)]
        assert printing == pages, f.name


def test_the_total_is_on_the_last_page_only_and_the_number_is_in_the_running_header(
    made: Made,
) -> None:
    p1, p2 = page_texts(pdf_of(made, A))
    assert has(p2, "CHF 883.00") and not has(p1, "883.00")
    assert has(p1, "Invoice no.:") and has(p1, "SRC-61942")
    assert has(p2, INVOICE_A)


def test_the_label_of_a_repeated_value_stands_before_it_on_each_accepted_page(
    made: Made,
) -> None:
    texts = page_texts(pdf_of(made, A))
    for f in TEXT.fields:
        for a in f.accepted:
            assert has(texts[a.page - 1], a.context), (f.name, a.page)
            assert a.evidence in a.context
            assert labels.verified(
                TEXT.type, f.name, typed(f.name, f.value), a.evidence, texts[a.page - 1]
            ), (f.name, a.page)


def test_the_lures_of_the_absent_invoice_date_do_not_verify_as_the_invoice_date(
    made: Made,
) -> None:
    """The only dates of the invoice are the days of the treatment: a reader that
    gives one of them as the invoice date is stored below the limit, not at 0.9."""
    text = page_texts(pdf_of(made, A))[0]
    assert has(text, "2026-09-29 to 2026-10-19")
    for lure in ("2026-09-29", "2026-10-19"):
        assert omni._shown(lure, text), lure
        assert not labels.verified(
            "medical_invoice", "invoice_date", typed("invoice_date", lure), lure, text
        ), lure


def test_every_phone_number_has_the_kit_pattern_and_no_placeholder_is_printed(
    made: Made,
) -> None:
    seen = 0
    for strings in made[1].values():
        flat_text = " ".join(s for page in strings for s in page)
        assert flat_text.count("+41") == len(PHONE.findall(flat_text))
        seen += flat_text.count("+41")
        for bad in ("IBAN", "UID", "CHE-000", "CH00", UID, IBAN):
            assert bad not in flat_text, bad
    assert seen == 2
    for text in page_texts(pdf_of(made, A)):
        assert "IBAN" not in text and "UID" not in text


# ====================================================================== 5 the scan


def test_the_scan_has_no_text_layer_one_image_per_page_and_no_exif(made: Made) -> None:
    pdf = pdf_of(made, B)
    assert scan_problems(pdf, key_in(made[0], B)) == []
    reader = PdfReader(pdf)
    assert len(reader.pages) == 3
    assert all(not (p.extract_text() or "").strip() for p in reader.pages)
    assert [len(p.images) for p in reader.pages] == [1, 1, 1]
    assert b"Exif" not in pdf.read_bytes()
    assert key_in(made[0], B)["variant"] == "scan"
    assert "variant" not in key_in(made[0], A)


def test_the_source_of_the_scan_prints_the_texts_of_the_design_on_each_page(
    made: Made,
) -> None:
    pages = made[1][f"{B}.pdf"]
    missing = [
        (n, s)
        for n, (strings, source) in enumerate(zip(SCAN.printed, pages, strict=True), 1)
        for s in strings
        if not has("\n".join(source), s)
    ]
    assert missing == []
    assert [p[-1] for p in pages] == [FOOT.format(n, 3) for n in (1, 2, 3)]
    assert pages[1][0] == QUOTE_B and pages[2][0] == QUOTE_B


@pytest.mark.parametrize("stem", STEMS)
def test_the_key_is_true_of_the_printed_strings_of_each_document(
    made: Made, stem: str
) -> None:
    assert source_problems(key_in(made[0], stem), made[1][f"{stem}.pdf"]) == []


def test_the_scan_pages_hold_every_value_of_the_key_in_their_source(
    made: Made,
) -> None:
    """Ground truth of a scan: the value, its key page and its accepted pages."""
    pages = made[1][f"{B}.pdf"]
    for f in key_in(made[0], B)["fields"]:
        accepted = f.get("accepted_pages") or [f["page"]]
        holding = [
            n
            for n, strings in enumerate(pages, 1)
            if any(f["snippet"] in s for s in strings)
        ]
        assert holding == accepted, f["name"]


def test_every_printed_string_of_the_text_pdf_is_in_its_text_layer(
    made: Made,
) -> None:
    texts = page_texts(pdf_of(made, A))
    for n, (strings, text) in enumerate(
        zip(made[1][f"{A}.pdf"], texts, strict=True), 1
    ):
        assert [s for s in strings if not has(text, s)] == [], n


def hash_problems(pages: list[list[str]], hashes: list[str]) -> list[str]:
    """The SHA-256 of the UTF-8 text of each source page (its strings joined with a
    new line, in drawing order) against the hashes of the manifest."""
    if len(pages) != len(hashes):
        return [f"{len(pages)} source pages, {len(hashes)} hashes"]
    return [
        f"page {n}: the text hashes to another value"
        for n, (strings, h) in enumerate(zip(pages, hashes, strict=True), 1)
        if hashlib.sha256("\n".join(strings).encode("utf-8")).hexdigest() != h
    ]


def test_the_hash_of_the_source_text_of_each_scan_page_is_in_the_manifest(
    made: Made,
) -> None:
    by_file = {d["file"]: d for d in manifest_in(made[0])["documents"]}
    scan = by_file[f"{B}.pdf"]
    assert hash_problems(made[1][f"{B}.pdf"], scan["source_text_sha256"]) == []
    assert "source_text_sha256" not in by_file[f"{A}.pdf"]
    # the check can fail: a page with another text, or in another order
    pages = copy.deepcopy(made[1][f"{B}.pdf"])
    pages[2][4] = "3. Frost or heavy wind can move the work."
    assert hash_problems(pages, scan["source_text_sha256"]) != []
    assert hash_problems(pages[:2], scan["source_text_sha256"]) != []
    swapped = [pages[1], pages[0], pages[2]]
    assert hash_problems(swapped, scan["source_text_sha256"]) != []


# ================================================================= 6 the bytes


def test_two_runs_give_the_same_files_with_the_same_bytes(
    made: Made, tmp_path: Path
) -> None:
    pilot_module().generate_pilot(tmp_path / "b")
    again: dict[str, list[list[str]]] = pilot_module().build(tmp_path / "c")
    names = sorted(p.name for p in made[0].iterdir())
    assert names == sorted(p.name for p in (tmp_path / "b").iterdir())
    for name in names:
        assert (made[0] / name).read_bytes() == (tmp_path / "b" / name).read_bytes()
        assert (made[0] / name).read_bytes() == (tmp_path / "c" / name).read_bytes()
    assert again == made[1]


def test_the_json_files_have_unix_line_ends_and_hold_only_ascii(made: Made) -> None:
    paths = sorted(made[0].glob("*.json"))
    assert len(paths) == 3
    for path in paths:
        data = path.read_bytes()
        assert b"\r" not in data and data.isascii(), path.name
        assert json.loads(data)


# ========================================================= 7 the right answer is small


def document_answer(key: dict[str, Any]) -> dict[str, Any]:
    return {f"page_{n}": a for n, a in enumerate(page_answers(key), 1)}


def test_the_perfect_document_answer_is_small_and_far_from_the_limit(
    made: Made,
) -> None:
    """Printed with an indent of 2, as the model prints JSON. 2,048 tokens at 2.42
    characters per token (the lowest seen) are about 4,950 characters."""
    sizes = {
        s: len(json.dumps(document_answer(key_in(made[0], s)), indent=2)) for s in STEMS
    }
    assert all(n < MP1_LARGEST_ANSWER for n in sizes.values()), sizes
    assert all(n < 0.4 * omni.MAX_TOKENS * 2.42 for n in sizes.values()), sizes
    assert all(n > 1_000 for n in sizes.values()), sizes  # the answer is not empty


# ============================================================= 8 independence
# The same rules as test_mp1_dataset.py, with a corpus that also holds MP1 (the pilot
# must share no name, number or run of three words with the kit, N1, MP1 or the prompt
# example). The corpus leaves out the generator of the pilot itself.


def corpus() -> tuple[frozenset[str], frozenset[tuple[str, str, str]]]:
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    texts: list[str] = []
    for p in sorted(MOCK.rglob("*")):  # the kit, N1 and MP1
        if p.suffix == ".json":
            texts.append(p.read_text("utf-8"))
        elif p.suffix == ".pdf":
            texts += [pg.extract_text() or "" for pg in PdfReader(p).pages]
    for p in sorted((ROOT / "scripts" / "mockdocs").glob("*.py")):
        if p.name != "pilot.py":
            texts.append(p.read_text("utf-8"))
    texts.append((ROOT / "docs" / "MOCK-DATA.md").read_text("utf-8"))
    texts.append(omni.EXAMPLE)  # the example of the prompt, with its answer
    texts += list(omni.DESCRIPTIONS.values())
    words: set[str] = set()
    runs: set[tuple[str, str, str]] = set()
    for t in texts:
        tokens = WORD.findall(t.casefold())
        words.update(tokens)
        runs.update(zip(tokens, tokens[1:], tokens[2:], strict=False))
    return frozenset(words), frozenset(runs)


@pytest.fixture(scope="module")
def known() -> tuple[frozenset[str], frozenset[tuple[str, str, str]]]:
    return corpus()


def name_problems(
    strings: list[str], known: tuple[frozenset[str], frozenset[Any]]
) -> list[str]:
    found = {
        n
        for s in strings
        for n in re.findall(r"\b[A-ZÄÖÜ][\w-]*", s)
        if n not in FORM_WORDS and n.casefold() in known[0]
    }
    return [f"name {n!r} is in the kit, in N1, in MP1 or in the prompt" for n in found]


def number_problems(
    strings: list[str], known: tuple[frozenset[str], frozenset[Any]]
) -> list[str]:
    found = {
        w
        for s in strings
        for w in WORD.findall(s.casefold())
        if len(w) >= 4 and any(c.isdigit() for c in w) and not YEAR.fullmatch(w)
        if w in known[0]
    }
    return [
        f"number {w!r} is in the kit, in N1, in MP1 or in the prompt" for w in found
    ]


def run_problems(
    strings: list[str], known: tuple[frozenset[str], frozenset[Any]]
) -> list[str]:
    found = set()
    for s in strings:
        tokens = WORD.findall(s.casefold())
        for run in zip(tokens, tokens[1:], tokens[2:], strict=False):
            generic = all(w in GENERIC or SMALL_NUMBER.fullmatch(w) for w in run)
            if run in known[1] and not generic:
                found.add(" ".join(run))
    return [
        f"the run {r!r} is in the kit, in N1, in MP1 or in the prompt" for r in found
    ]


def independence_problems(
    strings: list[str], known: tuple[frozenset[str], frozenset[Any]]
) -> list[str]:
    return (
        name_problems(strings, known)
        + number_problems(strings, known)
        + run_problems(strings, known)
    )


def all_strings(printed: dict[str, list[list[str]]]) -> list[str]:
    return [s for pages in printed.values() for page in pages for s in page]


def spec_strings() -> list[str]:
    strings = [s for spec in SPEC for page in spec.printed for s in page]
    for spec in SPEC:
        for description, quantity, unit, amount, _ in spec.lines:
            assert unit is not None
            strings += [description, str(quantity), f"{unit:,.2f}", f"{amount:,.2f}"]
    return strings


def test_the_check_of_independence_can_find_a_name_a_number_and_a_run_of_each_set(
    known: tuple[frozenset[str], frozenset[Any]],
) -> None:
    assert name_problems(["Jonas Meier"], known)  # the kit
    assert name_problems(["Wydler Badbau AG"], known)  # MP1
    assert name_problems(["Tobler"], known)  # N1
    assert number_problems(["2026-11-04"], known)  # the kit
    assert number_problems(["WB-26-0714"], known)  # MP1
    assert number_problems([omni.EXAMPLE_ANSWER["values"]["invoice_number"]], known)
    assert run_problems(["Panoramic image 2 x 23.45 46.90"], known)  # the kit
    assert run_problems(["Subtotal page 1, carried forward"], known)  # MP1
    assert independence_problems(["Sonnrain Rehabilitation AG"], known) == []
    assert independence_problems(["Page 2 of 3"], known) == []  # generic


def test_the_pilot_shares_no_name_number_or_run_with_the_kit_n1_mp1_or_the_prompt(
    made: Made, known: tuple[frozenset[str], frozenset[Any]]
) -> None:
    assert independence_problems(all_strings(made[1]), known) == []
    assert independence_problems(spec_strings(), known) == []


def test_the_invented_names_and_numbers_of_the_pilot_are_in_no_file_of_data_mock() -> (
    None
):
    """A plain check, apart from the words: the names, the numbers and the amounts
    that identify the pilot documents are in no key and no text layer of any set."""
    mine = [
        r"\bSonnrain\b",
        r"\bSigrist\b",
        r"\bZelgli\b",
        r"\bMarti\b",
        r"SRC-61942",
        r"ZD/26/0318",
        r"\b883\.00\b",
        r"\b2,442\.60\b",
    ]
    for p in sorted(MOCK.rglob("*.json")):
        text = p.read_text("utf-8")
        assert not [n for n in mine if re.search(n, text)], p
    for p in sorted(MOCK.rglob("*.pdf")):
        text = " ".join(page_texts(p))
        assert not [n for n in mine if re.search(n, text)], p


# ========================================================= 9 isolation from the sets


def test_nothing_named_like_a_pilot_document_exists_under_data_mock() -> None:
    names = {f"{s}.pdf" for s in STEMS} | {f"{s}.expected.json" for s in STEMS}
    found = [
        p
        for p in MOCK.rglob("*")
        if p.name in names or "pilot" in p.name.lower() or "pilot" in p.parent.name
    ]
    assert found == []
    assert "pilot" not in demo.NOT_KIT and "pilot" not in evaluate.SETS


def test_the_kit_n1_and_mp1_do_not_see_the_pilot_documents() -> None:
    found = demo._kit()
    assert len(found) == 35
    assert all(folder not in {"n1", "mp1", "pilot"} for folder, _ in found)
    assert not [n for _, n in found if "pilot" in n]
    kit = evaluate.kit()
    assert len(kit) == 35 and not [d for d in kit if "pilot" in d.id]
    mp1 = evaluate.kit("mp1")
    assert len(mp1) == 8 and not [d for d in mp1 if "pilot" in d.id]
    n1 = evaluate.kit("n1")
    assert len(n1) == 9 and not [d for d in n1 if "pilot" in d.id]
    with pytest.raises(ValueError):
        evaluate.kit("pilot")


@pytest.mark.parametrize("stem", STEMS)
def test_the_fixture_reader_does_not_know_a_pilot_document(
    made: Made, stem: str
) -> None:
    pdf = pdf_of(made, stem)
    document = Document(id="D-pilot", file=pdf.name, pages=BY_STEM[stem].pages)
    reader = FixtureReader()
    assert reader.classify(document, pdf.read_bytes()) == "unknown"
    out = reader.extract(document, pdf.read_bytes(), "unknown")
    assert out.document_type == "unknown" and out.fields == [] and out.line_items == []


def test_the_generator_is_not_called_by_the_script_of_the_kit() -> None:
    script = (ROOT / "scripts" / "generate_mock_documents.py").read_text("utf-8")
    assert "pilot" not in script.lower()


# ================================================= 10 a perfect model, both scopes


def read(
    pdf: Path, key: dict[str, Any], scope: str, answers: list[dict[str, Any]]
) -> tuple[Extraction, Calls]:
    """The document read in this scope by the real `OmniReader`, from one answer per
    page (a perfect model, a mock transport: no network)."""
    whole = {f"page_{n}": a for n, a in enumerate(answers, 1)}
    calls = Calls(
        {"document_type": key["document_type"]},
        *([whole] if scope == "document" else answers),
    )
    document = Document(id="D-pilot", file=pdf.name, pages=key["pages"])
    return calls.reader(scope).extract(document, pdf.read_bytes(), "travel"), calls


def by_name(out: Extraction) -> dict[str, Any]:
    return {f.name: f for f in out.fields}


@pytest.mark.parametrize("scope", omni.SCOPES)
def test_a_perfect_model_gives_every_present_field_of_the_text_pdf_verified(
    made: Made, scope: str
) -> None:
    key = key_in(made[0], A)
    out, calls = read(pdf_of(made, A), key, scope, page_answers(key))
    present = {f["name"]: f for f in key["fields"] if f["value"] is not None}
    assert set(by_name(out)) == set(present) and len(present) == 4
    for name, field in by_name(out).items():
        assert field.value == present[name]["value"], name
        assert field.source.page == present[name]["page"], name
        assert field.source.snippet == present[name]["snippet"], name
        assert field.confidence == omni.VERIFIED and field.source.box is None, name
        assert field.source.document_id == "D-pilot"
    assert "invoice_date" not in by_name(out)  # absent: nothing is stored
    assert out.line_items == key["line_items"]  # the lines, in order
    assert len(calls.requests) == 1 + (1 if scope == "document" else 2)


@pytest.mark.parametrize("scope", omni.SCOPES)
def test_a_perfect_model_gives_every_field_of_the_scan_for_a_person(
    made: Made, scope: str
) -> None:
    key = key_in(made[0], B)
    out, calls = read(pdf_of(made, B), key, scope, page_answers(key))
    present = {f["name"]: f for f in key["fields"]}
    assert set(by_name(out)) == set(present) and len(present) == 5
    for name, field in by_name(out).items():
        assert field.value == present[name]["value"], name
        assert field.source.page == present[name]["page"], name
        assert field.confidence == omni.UNVERIFIED, name
        assert field.source.snippet is None and field.source.box is not None, name
    assert out.line_items == []  # nothing on a scan confirms a line
    assert len(calls.requests) == 1 + (1 if scope == "document" else 3)


@pytest.mark.parametrize("stem", STEMS)
@pytest.mark.parametrize("repeat", [False, True], ids=["once", "on every page"])
def test_both_scopes_give_the_same_extraction_for_the_same_answers(
    made: Made, stem: str, repeat: bool
) -> None:
    key = key_in(made[0], stem)
    answers = page_answers(key, repeat)  # also a repeated value on every page
    page, _ = read(pdf_of(made, stem), key, "page", answers)
    document, _ = read(pdf_of(made, stem), key, "document", answers)
    assert document.model_dump() == page.model_dump()
    first = {f["name"]: f["page"] for f in key["fields"]}
    assert all(f.source.page == first[f.name] for f in document.fields)


# ================================================= 11 what the request will hold


def request_of(made: Made, stem: str) -> tuple[list[dict[str, Any]], list[int]]:
    """The requests that one reader sends for one document in the document scope (a
    perfect model answers): the bodies and their sizes in bytes."""
    key = key_in(made[0], stem)
    bodies: list[dict[str, Any]] = []
    sizes: list[int] = []

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        sizes.append(len(request.content))
        asks_type = body["messages"][0]["content"][0]["text"] == omni.CLASSIFY
        content = {"document_type": key["document_type"]} if asks_type else None
        content = document_answer(key) if content is None else content
        choice = {"message": {"content": json.dumps(content)}, "finish_reason": "stop"}
        return httpx.Response(200, json={"choices": [choice]})

    client = httpx.Client(transport=httpx.MockTransport(handle))
    reader = omni.OmniReader(
        client=client, settings=omni.OmniSettings(scope="document")
    )
    pdf = pdf_of(made, stem)
    document = Document(id="D-pilot", file=pdf.name, pages=BY_STEM[stem].pages)
    reader.classify(document, pdf.read_bytes())
    reader.extract(document, pdf.read_bytes(), "travel")
    return bodies, sizes


@pytest.mark.parametrize(("stem", "images"), [(A, 2), (B, 3)])
def test_the_document_request_holds_every_page_image_in_order_with_its_page_mark(
    made: Made, stem: str, images: int
) -> None:
    bodies, sizes = request_of(made, stem)
    assert len(bodies) == 2  # classify, then the document call (the type is cached)
    classify, document = (b["messages"][0]["content"] for b in bodies)
    assert [p["type"] for p in classify] == ["text", "image_url"]
    assert classify[0]["text"] == omni.CLASSIFY
    kinds = [p["type"] for p in document]
    assert kinds == ["text"] + ["text", "image_url"] * images
    marks = [p["text"] for p in document[1::2]]
    assert (
        marks
        == [omni.PAGE_MARK.format(page=n, pages=images) for n in range(1, 4)][:images]
    )
    assert document[0]["text"].startswith("Read every image.")
    assert (
        f"They are the {images} pages of one {BY_STEM[stem].type}"
        in (document[0]["text"])
    )
    schema = bodies[1]["response_format"]["json_schema"]
    assert schema["strict"] is True
    assert list(schema["schema"]["properties"]) == [
        f"page_{n}" for n in range(1, images + 1)
    ]
    assert bodies[1]["max_tokens"] == 2048 and bodies[1]["temperature"] == 0.2
    assert sizes[1] > sizes[0]


@pytest.mark.parametrize(("stem", "images"), [(A, 2), (B, 3)])
def test_the_images_are_the_pages_of_the_document_at_150_dpi(
    made: Made, stem: str, images: int
) -> None:
    pdf = pdf_of(made, stem)
    pages = omni.pages(pdf.read_bytes())
    assert len(pages) == images
    assert [p.size for p in pages] == [(1241, 1754)] * images
    assert IMAGE_TOKENS == 2145  # one token per 32 x 32 px block (#20, section 6)
    bodies, _ = request_of(made, stem)
    sent = [
        hashlib.sha256(
            base64.b64decode(p["image_url"]["url"].split(",", 1)[1])
        ).hexdigest()
        for p in bodies[1]["messages"][0]["content"]
        if p["type"] == "image_url"
    ]
    assert sent == [hashlib.sha256(p.image).hexdigest() for p in pages]
    assert sum(len(p.image) for p in pages) < omni.MAX_DOCUMENT_BYTES


def test_the_size_of_the_request_of_each_document(made: Made) -> None:
    """A: two PNG pages of a text PDF, about 0.2 MB. B: three PNG pages of a scan, about
    4.8 MB, the size of the largest request of the main run (the scan of 3 pages of
    MP1). The bounds are wide: a JPEG codec of another machine changes the bytes."""
    (_, size_a), (_, size_b) = request_of(made, A), request_of(made, B)
    assert 100_000 < size_a[1] < 400_000
    assert 3_000_000 < size_b[1] < 6_500_000
    assert 1_000_000 < size_b[0] < 2_500_000  # the classify call: one scan page


# ================================= 12 the logical calls of the pilot (reader code)


def counted_reader(
    scope: str, *answers: Any
) -> tuple[omni.OmniReader, list[dict[str, Any]], list[float]]:
    """A reader whose transport answers in order (a dict is a 200 answer, an int is a
    status) and keeps the requests; the waits go to a list."""
    sent: list[dict[str, Any]] = []
    waits: list[float] = []
    queue = iter(answers)

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        nxt = next(queue)
        if isinstance(nxt, int):
            return httpx.Response(nxt, json={})
        choice = {"message": {"content": json.dumps(nxt)}, "finish_reason": "stop"}
        return httpx.Response(200, json={"choices": [choice]})

    client = httpx.Client(transport=httpx.MockTransport(handle))
    reader = omni.OmniReader(
        client=client, sleep=waits.append, settings=omni.OmniSettings(scope=scope)
    )
    return reader, sent, waits


def harness_order(
    reader: omni.OmniReader, made: Made, stem: str
) -> tuple[str, Extraction]:
    """What the harness does for a document: classify, then extract."""
    pdf = pdf_of(made, stem)
    document = Document(id="D-pilot", file=pdf.name, pages=BY_STEM[stem].pages)
    kind = reader.classify(document, pdf.read_bytes())
    return kind, reader.extract(document, pdf.read_bytes(), "travel")


def images_of(body: dict[str, Any]) -> int:
    return sum(p["type"] == "image_url" for p in body["messages"][0]["content"])


def test_one_reader_sends_four_logical_calls_for_the_two_documents(made: Made) -> None:
    ka, kb = key_in(made[0], A), key_in(made[0], B)
    reader, sent, waits = counted_reader(
        "document",
        {"document_type": "medical_invoice"},
        document_answer(ka),
        {"document_type": "repair_quote"},
        document_answer(kb),
    )
    harness_order(reader, made, A)
    harness_order(reader, made, B)
    assert [images_of(b) for b in sent] == [1, 2, 1, 3]  # classify A, doc A, B, B
    assert [b["messages"][0]["content"][0]["text"] == omni.CLASSIFY for b in sent] == [
        True,
        False,
        True,
        False,
    ]
    assert waits == []  # no retry: 4 requests, 4 attempts


def test_the_page_scope_on_the_same_two_documents_needs_seven_calls(made: Made) -> None:
    ka, kb = key_in(made[0], A), key_in(made[0], B)
    reader, sent, _ = counted_reader(
        "page",
        {"document_type": "medical_invoice"},
        *page_answers(ka),
        {"document_type": "repair_quote"},
        *page_answers(kb),
    )
    harness_order(reader, made, A)
    harness_order(reader, made, B)
    assert len(sent) == 2 + 2 + 3  # two classify calls, five page calls
    assert [images_of(b) for b in sent] == [1] * 7


def test_a_classify_that_gives_no_type_is_asked_again_by_extract(made: Made) -> None:
    """A classify that fails or gives text that is no type is not cached: the harness
    asks (1 call), `extract` asks again (1 call), then one document call."""
    ka = key_in(made[0], A)
    reader, sent, _ = counted_reader(
        "document",
        {"document_type": "banana"},  # no type: not cached
        {"document_type": "medical_invoice"},
        document_answer(ka),
    )
    kind, out = harness_order(reader, made, A)
    assert kind == "unknown" and len(out.fields) == 4  # the harness saw "unknown"
    assert [images_of(b) for b in sent] == [1, 1, 2]


def test_a_type_that_is_unknown_or_a_photo_sends_no_document_call(made: Made) -> None:
    for kind in ("unknown", "damage_photo"):
        reader, sent, _ = counted_reader("document", {"document_type": kind})
        harness_order(reader, made, A)
        assert [images_of(b) for b in sent] == [1], kind  # cached: no second classify


def test_a_failing_endpoint_costs_three_attempts_a_call_and_six_for_one_document(
    made: Made,
) -> None:
    """The rule of the reader: 2 retries after a 5xx (3 attempts). A classify that never
    answers is asked twice (by the harness, then by `extract`): 6 attempts, no document
    call. A document call that never answers is not asked again."""
    reader, sent, waits = counted_reader("document", *([500] * 6))
    harness_order(reader, made, A)
    assert len(sent) == 6 and waits == [2.0, 8.0, 2.0, 8.0]
    ka = key_in(made[0], A)
    reader, sent, _ = counted_reader(
        "document", {"document_type": "medical_invoice"}, 429, 429, 500
    )
    kind, out = harness_order(reader, made, A)
    assert kind == "medical_invoice" and out.fields == []
    assert [images_of(b) for b in sent] == [1, 2, 2, 2]  # 1 + 3 attempts
    assert document_answer(ka)


def test_the_largest_pilot_would_be_nine_attempts_a_document_and_eighteen_in_all(
    made: Made,
) -> None:
    """Worst case: the first classify fails (3 attempts), the second works at the third
    attempt, and the document call needs 3 attempts: 3 logical calls, 9 attempts, for
    each of the two documents. The cap of 8 stops this long before."""
    ka = key_in(made[0], A)
    reader, sent, _ = counted_reader(
        "document",
        *([500] * 3),
        500,
        500,
        {"document_type": "medical_invoice"},
        500,
        500,
        document_answer(ka),
    )
    harness_order(reader, made, A)
    assert len(sent) == 9
    assert [images_of(b) for b in sent] == [1] * 6 + [2] * 3


# ================================================================ 13 negatives
# Each negative test holds a copy of the key of the table against a generated PDF.


def bad(stem: str, change: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    key = copy.deepcopy(expected_key(BY_STEM[stem]))
    change(key)
    return key


def field(key: dict[str, Any], name: str) -> dict[str, Any]:
    [found] = [f for f in key["fields"] if f["name"] == name]
    assert isinstance(found, dict)
    return found


def move_total(key: dict[str, Any]) -> None:
    field(key, "total")["page"] = 1  # CHF 883.00 is on page 2


def wrong_evidence(key: dict[str, Any]) -> None:
    field(key, "invoice_number")["page_snippets"]["2"] = "SRC-61949"


def accept_wrong_page(key: dict[str, Any]) -> None:
    f = field(key, "provider")  # the clinic is printed on page 1 only
    f["accepted_pages"] = [1, 2]
    f["page_snippets"] = {"1": f["snippet"], "2": f["snippet"]}


def wrong_line_page(key: dict[str, Any]) -> None:
    key["line_pages"][0] = 2


def swap_lines(key: dict[str, Any]) -> None:
    lines = key["line_items"]
    lines[0], lines[1] = lines[1], lines[0]


def break_the_sum(key: dict[str, Any]) -> None:
    key["line_items"][2]["unit_price"] = key["line_items"][2]["amount"] = 69.8


def break_a_product(key: dict[str, Any]) -> None:
    key["line_items"][3]["amount"] = 206.0


def wrong_pages(key: dict[str, Any]) -> None:
    key["pages"] = 3


def absent_with_snippet(key: dict[str, Any]) -> None:
    field(key, "invoice_date")["snippet"] = "2026-09-29"


def a_lure_as_key(key: dict[str, Any]) -> None:
    f = field(key, "invoice_number")
    f["value"] = f["snippet"] = "2026-09-29"  # a date of the treatment, no number
    del f["accepted_pages"], f["page_snippets"]


def wrong_first_page(key: dict[str, Any]) -> None:
    field(key, "invoice_number")["page"] = 2


NEGATIVES: list[tuple[Callable[[dict[str, Any]], None], str]] = [
    (move_total, "is not shown on page 1"),
    (wrong_evidence, "is not shown on page 2"),
    (accept_wrong_page, "is not shown on page 2"),
    (wrong_line_page, "line 1: not on the text of page 2"),
    (swap_lines, "not in the order of the lines of page 1"),
    (break_the_sum, "sum: the lines add up"),
    (break_a_product, "quantity times unit price"),
    (wrong_pages, "page count"),
    (absent_with_snippet, "an absent field has"),
    (a_lure_as_key, "the label check does not verify"),
    (wrong_first_page, "the first page that prints the value is 1"),
]


@pytest.mark.parametrize(
    ("change", "problem"), NEGATIVES, ids=[c.__name__ for c, _ in NEGATIVES]
)
def test_the_check_of_the_text_pdf_names_each_mistake_of_a_key(
    made: Made, change: Callable[[dict[str, Any]], None], problem: str
) -> None:
    pdf = pdf_of(made, A)
    assert problems(pdf, expected_key(TEXT)) == []
    found = problems(pdf, bad(A, change))
    assert any(problem in p for p in found), found


def test_the_check_of_the_scan_names_a_text_pdf_a_page_count_and_a_missing_image(
    made: Made,
) -> None:
    key = expected_key(SCAN)
    assert scan_problems(pdf_of(made, B), key) == []
    found = scan_problems(pdf_of(made, A), key)  # the invoice: 2 pages, with a text
    assert any("has a text layer" in p for p in found), found
    assert any("page count" in p for p in found), found
    wrong = bad(B, lambda k: k.update(pages=2))
    assert any("page count" in p for p in scan_problems(pdf_of(made, B), wrong))


def test_the_check_of_the_source_names_a_wrong_page_of_a_scan_value(made: Made) -> None:
    pages = made[1][f"{B}.pdf"]
    assert source_problems(expected_key(SCAN), pages) == []

    def quote_date_on_page_2(key: dict[str, Any]) -> None:
        field(key, "quote_date")["page"] = 2
        field(key, "quote_date").pop("page_snippets", None)

    found = source_problems(bad(B, quote_date_on_page_2), pages)
    assert any("is not in one string of page 2" in p for p in found), found

    def wrong_cell(key: dict[str, Any]) -> None:
        key["line_items"][0]["amount"] = 223.0

    found = source_problems(bad(B, wrong_cell), pages)
    assert any("not a cell of page 2" in p for p in found), found
    assert any(
        "page count" in p for p in source_problems(expected_key(SCAN), pages[:2])
    )


def test_the_pilot_keys_are_refused_by_the_harness_when_they_are_broken() -> None:
    key = expected_key(TEXT)
    evaluate._check_key(key)
    field(key, "invoice_number")["accepted_pages"] = [1]  # one page: a bad key
    with pytest.raises(BadAnswerKey):
        evaluate._check_key(key)


def test_the_accepted_class_of_the_table_is_the_one_of_mp1() -> None:
    """The table uses the helpers of the MP1 tests: a page, the evidence, the label."""
    [number] = [f for f in TEXT.fields if f.name == "invoice_number"]
    assert number.accepted == (
        Accepted(1, "SRC-61942", INVOICE_A),
        Accepted(2, "SRC-61942", INVOICE_A),
    )
