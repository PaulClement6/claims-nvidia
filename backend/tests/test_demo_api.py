from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import demo
from app.main import create_app
from tests.reader_contract import MOCK

BOOKING = MOCK / "claim-1-travel" / "booking-confirmation.pdf"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CLAIMS_DB", str(tmp_path / "claims.db"))
    monkeypatch.setenv("CLAIMS_UPLOADS", str(tmp_path / "uploads"))
    with TestClient(create_app()) as c:
        yield c


def test_reset_deletes_claims_and_files_and_keeps_customers(
    client: TestClient, tmp_path: Path
) -> None:
    cid = client.post("/claims", json={"customer_id": "C-1003"}).json()["claim_id"]
    client.post(
        f"/claims/{cid}/documents", files={"file": ("b.pdf", BOOKING.read_bytes())}
    )
    assert any((tmp_path / "uploads").rglob("*.pdf"))
    out = client.post("/demo/reset").json()
    assert out["claims"] == 1 and out["files"] == 1 and out["events"] >= 1
    assert client.get(f"/claims/{cid}").status_code == 404
    assert not any(p.is_file() for p in (tmp_path / "uploads").rglob("*"))
    assert len(client.get("/customers").json()) >= 3


@pytest.mark.parametrize("where", ["root", "home", "repo"])
def test_reset_refuses_dangerous_folders(client: TestClient, where: str) -> None:
    repo = Path(demo.__file__).resolve().parents[2]
    client.app.state.uploads = {  # type: ignore[attr-defined]
        "root": Path("/"),
        "home": Path.home(),
        "repo": repo,
    }[where]
    assert client.post("/demo/reset").status_code == 409
    assert (repo / "AGENTS.md").exists()


def test_kit_lists_the_mock_documents(client: TestClient) -> None:
    kit = client.get("/demo/kit").json()
    row = next(r for r in kit if r["file"] == "booking-confirmation.pdf")
    assert row["folder"] == "claim-1-travel"
    assert row["document_type"] == "booking_confirmation"
    assert row["size"] == BOOKING.stat().st_size
    assert len(kit) == 8


def test_kit_serves_listed_files_only(client: TestClient) -> None:
    ok = client.get("/demo/kit/claim-1-travel/booking-confirmation.pdf")
    assert ok.status_code == 200 and ok.content == BOOKING.read_bytes()
    for bad in (
        "/demo/kit/claim-1-travel/booking-confirmation.expected.json",
        "/demo/kit/claim-1-travel/..%2Fpolicies.json",
        "/demo/kit/..%2F/policies.json",
        "/demo/kit/claim-1-travel/nope.pdf",
    ):
        assert client.get(bad).status_code == 404
