import json
from pathlib import Path

import pytest

from app.docintel.fixture import FixtureReader
from app.docintel.reader import get_reader
from app.models import Document
from tests.reader_contract import MOCK, check_reader_contract


def test_fixture_reader_passes_the_contract() -> None:
    check_reader_contract(FixtureReader())


def test_known_file_gives_expected_fields_with_the_uploaded_document_id() -> None:
    pdf = MOCK / "claim-1-travel" / "booking-confirmation.pdf"
    expected = json.loads(pdf.with_suffix(".expected.json").read_text())
    doc = Document(id="D-up1", file="x.pdf")
    reader = FixtureReader()

    assert reader.classify(doc, pdf.read_bytes()) == "booking_confirmation"
    out = reader.extract(doc, pdf.read_bytes(), "travel")

    assert [f.name for f in out.fields] == [f["name"] for f in expected["fields"]]
    assert {f.source.document_id for f in out.fields} == {"D-up1"}
    price = next(f for f in out.fields if f.name == "trip_price")
    assert price.value == 1840.0 and price.source.page == 1
    assert len(out.line_items) == 3


def test_a_photo_gives_its_box_as_source() -> None:
    jpg = MOCK / "claim-2-property" / "damage-photo-1.jpg"
    doc = Document(id="D-ph", file="x.jpg")
    reader = FixtureReader()

    assert reader.classify(doc, jpg.read_bytes()) == "damage_photo"
    out = reader.extract(doc, jpg.read_bytes(), "property")
    [f] = out.fields
    assert f.name == "damage_description" and f.source.page == 1
    assert f.source.snippet is None and f.source.box is not None
    assert f.source.document_id == "D-ph"


def test_unknown_bytes_give_unknown_and_no_field() -> None:
    reader = FixtureReader()
    doc = Document(id="D-x", file="x")
    assert reader.classify(doc, b"hello") == "unknown"
    out = reader.extract(doc, b"hello", "travel")
    assert out.document_type == "unknown" and out.fields == []


def test_get_reader_default_and_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLAIMS_READER", raising=False)
    assert get_reader().name == "fixture"
    monkeypatch.setenv("CLAIMS_READER", "omni")
    with pytest.raises(ValueError, match="unknown CLAIMS_READER 'omni'"):
        get_reader()


def test_data_folder_comes_from_claims_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAIMS_DATA", str(tmp_path))
    assert FixtureReader().index == {}
