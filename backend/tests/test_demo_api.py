import json
import re
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
    assert len(kit) == 33  # 24 documents of the scenarios and 9 hard versions
    assert {r["folder"] for r in kit} >= {"claim-3-health", "kit-anna-theft", "hard"}


def test_kit_rows_say_which_scenario_a_document_belongs_to(client: TestClient) -> None:
    kit = client.get("/demo/kit").json()
    row = next(
        r
        for r in kit
        if (r["folder"], r["file"]) == ("kit-anna-theft", "police-report.pdf")
    )
    assert row["scenario"] == "kit-anna-theft" and row["policy"] == "HH-2025-1182"
    assert row["customer_id"] == "C-1001" and row["variant"] is None
    assert row["title"] == "Burglary: forced front door"
    assert "9 October 2026" in row["story"] and "CHF 2,640" in row["story"]
    # a hard version belongs to the scenario of its source document
    hard = next(r for r in kit if r["folder"] == "hard" and r["variant"] == "photo")
    source = hard["source_document"]
    assert hard["scenario"] == source.split("/")[0]
    assert hard["policy"] and hard["story"]
    assert client.get(f"/demo/kit/hard/{hard['file']}").status_code == 200
    # a visitor reads these texts: nothing in them tells what the checks will find
    assert not re.search(r"fraud|tamper|signal|edited", json.dumps(kit), re.I)


def test_kit_without_an_index_still_lists_the_files(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock = tmp_path / "data" / "mock" / "claim-1-travel"
    mock.mkdir(parents=True)
    (mock / "a.pdf").write_bytes(BOOKING.read_bytes())
    monkeypatch.setenv("CLAIMS_DATA", str(tmp_path / "data"))
    [row] = client.get("/demo/kit").json()
    assert row["file"] == "a.pdf" and row["scenario"] is None and row["story"] is None


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


def test_scenarios_of_a_policy_for_the_customer_panel(client: TestClient) -> None:
    rows = client.get("/demo/scenarios", params={"policy": "HH-2024-0933"}).json()
    assert {r["folder"] for r in rows} == {"claim-2-property", "kit-marco-bathroom"}
    row = next(r for r in rows if r["folder"] == "claim-2-property")
    assert set(row) == {"folder", "title", "stories", "documents"}
    assert all(set(s) == {"text"} for s in row["stories"])
    invoice = next(d for d in row["documents"] if d["file"] == "repair-invoice.pdf")
    assert invoice["document_type"] == "repair_invoice"
    assert {h["variant"] for h in invoice["hard"]} == {"scan", "photo"}
    assert all(h["folder"] == "hard" for h in invoice["hard"])
    assert client.get("/demo/scenarios", params={"policy": "NONE"}).json() == []


# non-negotiable 2: the customer panel reads these two endpoints
FORBIDDEN = re.compile(
    r"straight_through|handler_review|investigat|[CRT]\d{3}\b|signal|fraud|tamper|inflated",
    re.IGNORECASE,
)


def test_customer_endpoints_hold_no_expected_result(client: TestClient) -> None:
    index = json.loads((demo._data() / "mock" / "index.json").read_text())
    policies = {s["policy"] for s in index["scenarios"]}
    bodies = [client.get("/demo/kit").text] + [
        client.get("/demo/scenarios", params={"policy": p}).text for p in policies
    ]
    assert len(policies) >= 6
    for body in bodies:
        assert FORBIDDEN.search(body) is None
