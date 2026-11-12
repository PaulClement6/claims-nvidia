import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.reader_contract import MOCK

BOOKING = MOCK / "claim-1-travel" / "booking-confirmation.pdf"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CLAIMS_DB", str(tmp_path / "claims.db"))
    monkeypatch.setenv("CLAIMS_UPLOADS", str(tmp_path / "uploads"))
    with TestClient(create_app()) as c:
        yield c


def claim_id(client: TestClient) -> str:
    return str(client.post("/claims", json={"customer_id": "C-1001"}).json()["id"])


def upload(client: TestClient, cid: str, name: str, data: bytes, mime: str = "x/y"):  # type: ignore[no-untyped-def]
    return client.post(f"/claims/{cid}/documents", files={"file": (name, data, mime)})


def events(client: TestClient, cid: str):  # type: ignore[no-untyped-def]
    return client.app.state.store.events_since(cid, 0)  # type: ignore[attr-defined]


def test_booking_confirmation_gives_two_events_and_fields_with_sources(
    client: TestClient, tmp_path: Path
) -> None:
    cid = claim_id(client)
    res = upload(client, cid, "../../evil.pdf", BOOKING.read_bytes(), "text/plain")

    assert res.status_code == 201
    doc = res.json()
    assert doc["document_type"] == "booking_confirmation"
    assert doc["pages"] == 1
    assert doc["file"] == f"{cid}/{doc['id']}.pdf"
    assert (tmp_path / "uploads" / doc["file"]).read_bytes() == BOOKING.read_bytes()
    names = {f["name"]: f for f in doc["fields"]}
    assert names["booking_reference"]["value"] == "WPR-482913"
    for f in doc["fields"]:
        assert f["source"]["document_id"] == doc["id"]
        assert f["source"]["page"] == 1 and f["source"]["snippet"]

    first, second = events(client, cid)
    assert [first.stage, second.stage] == ["classify_document", "extract_document"]
    assert first.inputs == second.inputs == {"document_id": doc["id"]}
    assert first.model == second.model == "fixture"
    assert second.outputs["fields"][0]["source"]["document_id"] == doc["id"]
    assert client.get(f"/claims/{cid}").json()["documents"] == [doc]


def test_unknown_pdf_gives_unknown_and_no_field_and_no_error(
    client: TestClient,
) -> None:
    cid = claim_id(client)
    other = BOOKING.read_bytes().replace(
        b"Booking confirmation", b"Something else!!!!!!"
    )
    assert (
        hashlib.sha256(other).digest() != hashlib.sha256(BOOKING.read_bytes()).digest()
    )
    res = upload(client, cid, "other.pdf", other)
    assert res.status_code == 201
    assert res.json()["document_type"] == "unknown" and res.json()["fields"] == []
    assert len(events(client, cid)) == 2


def test_png_is_accepted_by_magic_bytes(client: TestClient) -> None:
    res = upload(client, claim_id(client), "photo.pdf", PNG)
    assert res.status_code == 201 and res.json()["file"].endswith(".png")


@pytest.mark.parametrize("data", [b"", b"just text", b"PK\x03\x04zip", b"%PDFx"])
def test_other_file_is_415(client: TestClient, data: bytes) -> None:
    cid = claim_id(client)
    res = upload(client, cid, "a.pdf", data, "application/pdf")
    assert res.status_code == 415
    assert events(client, cid) == []


def test_unreadable_pdf_is_415(client: TestClient) -> None:
    assert (
        upload(client, claim_id(client), "a.pdf", b"%PDF-1.4 broken").status_code == 415
    )


def test_file_over_10_mb_is_413(client: TestClient) -> None:
    big = b"%PDF-" + b"0" * (10 * 1024 * 1024)
    assert upload(client, claim_id(client), "big.pdf", big).status_code == 413


def test_unknown_claim_is_404(client: TestClient) -> None:
    assert upload(client, "CL-nope", "a.pdf", BOOKING.read_bytes()).status_code == 404


def test_unknown_reader_stops_the_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAIMS_DB", str(tmp_path / "claims.db"))
    monkeypatch.setenv("CLAIMS_READER", "nope")
    with pytest.raises(ValueError, match="CLAIMS_READER"), TestClient(create_app()):
        pass
