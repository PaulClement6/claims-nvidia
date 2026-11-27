"""The N1 safety set (issue #125): nine documents in which some fields are absent.

It lives in `data/mock/n1/`, flat, with an answer key per document and a manifest.
It is not part of the kit: the demo, the bench and the harness of the kit see 35
documents. It is in every test that reads all of `data/mock` (the reader contract,
the schemas). An absent field is a row of the key with `value: null`; a perfect
reader stores nothing for it.
"""

import importlib.util
import json
import logging
import re
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfReader

from app import demo
from app.docintel import evaluate, omni
from app.docintel.fixture import FixtureReader
from app.models import Document
from tests.reader_contract import MOCK

ROOT = MOCK.parents[1]
N1 = MOCK / "n1"
spec = importlib.util.spec_from_file_location(
    "generate_mock_documents", ROOT / "scripts" / "generate_mock_documents.py"
)
assert spec and spec.loader
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)

MANIFEST: dict[str, Any] = json.loads((N1 / "manifest.json").read_text("utf-8"))
DOCS: list[dict[str, Any]] = MANIFEST["documents"]
FILES = [d["file"] for d in DOCS]
KITS = {"text": 6, "scan": 2, "photo": 1}


def key_of(file: str) -> dict[str, Any]:
    key: dict[str, Any] = json.loads(
        (N1 / file).with_suffix(".expected.json").read_text("utf-8")
    )
    return key


def test_the_folder_holds_the_nine_documents_their_keys_the_manifest_only() -> None:
    assert sorted(p.name for p in N1.iterdir()) == sorted(
        [*FILES, *(Path(f).with_suffix(".expected.json").name for f in FILES)]
        + ["manifest.json"]
    )
    assert len(FILES) == len(set(FILES)) == 9
    assert all((N1 / f).is_file() for f in FILES)


def test_the_manifest_tells_what_the_set_is() -> None:
    assert (MANIFEST["set"], MANIFEST["version"], MANIFEST["issue"]) == ("n1", 1, 125)
    assert MANIFEST["purpose"]
    assert (MANIFEST["absent_fields"], MANIFEST["present_fields"]) == (16, 23)


def test_the_manifest_matches_the_documents_and_the_keys() -> None:
    kinds = [d["kind"] for d in DOCS]
    assert {k: kinds.count(k) for k in set(kinds)} == KITS
    for d in DOCS:
        key = key_of(d["file"])
        assert key["document_type"] == d["document_type"], d["file"]
        absent = {f["name"] for f in key["fields"] if f["value"] is None}
        assert absent == set(d["absent"]), d["file"]
        assert set(d["absent"].values()) <= {"plain", "lure"}
        # an absent field has no snippet; a present one has a source of its own
        for f in key["fields"]:
            assert f["page"] == 1 and f["confidence"] == 1.0
            assert ("snippet" in f) == (f["value"] is not None), (d["file"], f["name"])
    assert sum(len(d["absent"]) for d in DOCS) == MANIFEST["absent_fields"] == 16
    present = sum(
        f["value"] is not None for d in DOCS for f in key_of(d["file"])["fields"]
    )
    assert present == MANIFEST["present_fields"] == 23


def test_six_documents_have_a_text_layer_and_three_have_none() -> None:
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    for d in DOCS:
        path = N1 / d["file"]
        if path.suffix == ".jpg":
            assert d["kind"] == "photo"
            continue
        text = "".join(p.extract_text() or "" for p in PdfReader(path).pages)
        assert bool(text.strip()) == (d["kind"] == "text"), d["file"]


def test_the_kit_is_still_35_documents_and_holds_no_file_of_n1() -> None:
    assert demo.NOT_KIT == {"n1", "mp1"}
    found = demo._kit()
    assert len(found) == 35
    assert all(folder not in demo.NOT_KIT for folder, _ in found)
    harness = evaluate.kit()
    assert len(harness) == 35
    assert not any(d.id.startswith("n1/") for d in harness)
    assert not any(f in {name for _, name in found} for f in FILES)


# ------------------------------------------------------------ the content is new

WORD = re.compile(r"[0-9a-zà-ÿ]+(?:[.,:/'-][0-9a-zà-ÿ]+)*")
YEAR = re.compile(r"20[23]\d")
UID = "UID CHE-000.000.000"  # the placeholders that the kit generator prints
IBAN = "IBAN CH00 0000 0000 0000 0000 0"
PLACEHOLDERS = {"che-000.000.000", "0000"}
# Capitalised words of the form of a document. They may be in the kit; every other
# capitalised word (a person, a company, a street, a town) may not.
FORM_WORDS = {
    "AG", "Advice", "Amount", "Billing", "Blood", "Booking", "CHF", "Cancellation",
    "Cancelled", "Collected", "Customer", "Date", "Departure", "Descale", "Details",
    "Diagnosis", "Doctor", "Dr", "Electrician", "Final", "Findings", "GmbH",
    "General", "Have", "Hiking", "IBAN", "Insured", "Invoice", "Item", "Job", "MWST",
    "Medical", "Norway", "Patient", "Payable", "Payment", "Price", "Quote", "Qty",
    "Refund", "Repair", "Replace", "Scaffold", "Service", "Statement", "Switzerland",
    "Tel", "Thank", "Total", "Traveller", "Trip", "UID", "Unit", "VAT", "Valid", "We",
    "Work", "Your", "Article", "Pick-up",
}  # fmt: skip


def kit_words() -> set[str]:
    """The words of the kit (never of N1): the JSON and PDF text of data/mock outside
    NOT_KIT, the generators of the kit, docs/MOCK-DATA.md, and the two texts that go
    to the model (the worked example and the descriptions of the fields)."""
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    outside = [
        p for p in MOCK.rglob("*") if p.relative_to(MOCK).parts[0] not in demo.NOT_KIT
    ]
    texts = [p.read_text("utf-8") for p in sorted(outside) if p.suffix == ".json"]
    for p in sorted(outside):
        if p.suffix == ".pdf":
            texts += [pg.extract_text() or "" for pg in PdfReader(p).pages]
    mockdocs = sorted((ROOT / "scripts" / "mockdocs").glob("*.py"))
    texts += [p.read_text("utf-8") for p in mockdocs if p.name != "n1.py"]
    texts.append((ROOT / "docs" / "MOCK-DATA.md").read_text("utf-8"))
    texts.append(omni.EXAMPLE_TEXT)
    texts += list(omni.DESCRIPTIONS.values())
    return {w for t in texts for w in WORD.findall(t.casefold())}


def test_no_name_and_no_number_of_n1_is_in_the_kit_or_in_the_texts_for_the_model(
    tmp_path: Path,
) -> None:
    printed = gen.build_n1(tmp_path)  # what the generator prints on each document
    assert sorted(printed) == sorted(FILES)
    words = kit_words()
    assert {"lindenhof", "brunner", "jonas"} <= words  # the check can find a name
    problems = []
    for file, lines in printed.items():
        text = "\n".join(lines).replace(UID, "").replace(IBAN, "")
        names = set(re.findall(r"\b[A-ZÄÖÜ][\w-]*", text)) - FORM_WORDS
        problems += [
            f"{file}: name {n!r} is in the kit"
            for n in sorted(names)
            if n.casefold() in words
        ]
        for w in WORD.findall(text.casefold()):
            long_number = len(w) >= 4 and any(c.isdigit() for c in w)
            if long_number and not (YEAR.fullmatch(w) or w in PLACEHOLDERS):
                if w in words:
                    problems.append(f"{file}: number {w!r} is in the kit")
    assert problems == []


@pytest.mark.parametrize("file", FILES)
def test_the_fixture_reader_stores_exactly_the_present_fields_each_with_a_source(
    file: str,
) -> None:
    key = key_of(file)
    out = FixtureReader().extract(
        Document(id="D-n1", file=file), (N1 / file).read_bytes(), "travel"
    )
    present = {f["name"]: f["value"] for f in key["fields"] if f["value"] is not None}
    absent = {f["name"] for f in key["fields"] if f["value"] is None}
    assert {f.name: f.value for f in out.fields} == present
    assert absent and not absent & {f.name for f in out.fields}
    assert out.document_type == key["document_type"]
    for f in out.fields:
        assert f.source.document_id == "D-n1" and f.source.page == 1
        assert f.source.snippet or f.source.box
