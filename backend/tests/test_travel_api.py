"""Claim file 1 and its variants, end to end through the HTTP API."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.reader_contract import MOCK

CLAIM1 = MOCK / "claim-1-travel"
PDFS = ["booking-confirmation", "cancellation-confirmation", "medical-certificate"]
STORY = "My trip was cancelled because I was ill."
VALIDATED = "Your claim is validated. You will receive a confirmation by e-mail."
REVIEW = "A claims specialist will review your case and contact you."


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CLAIMS_DB", str(tmp_path / "claims.db"))
    monkeypatch.setenv("CLAIMS_UPLOADS", str(tmp_path / "uploads"))
    with TestClient(create_app()) as c:
        yield c


def run_claim(
    client: TestClient,
    pdfs: list[str] = PDFS,
    amount: str = "CHF 1,500",
    date: str = "3 August 2026",
    done: bool = False,
) -> str:
    cid = str(client.post("/claims", json={"customer_id": "C-1001"}).json()["id"])
    client.put(f"/claims/{cid}/policy", json={"policy_number": "TRV-2026-0417"})
    for text in (STORY, date, amount):
        assert (
            client.post(f"/claims/{cid}/messages", json={"text": text}).status_code
            == 200
        )
    for name in pdfs:
        data = (CLAIM1 / f"{name}.pdf").read_bytes()
        res = client.post(f"/claims/{cid}/documents", files={"file": (name, data)})
        assert res.status_code == 201
    if done:
        client.post(f"/claims/{cid}/messages", json={"text": "done"})
    return cid


def claim(client: TestClient, cid: str) -> dict[str, Any]:
    out: dict[str, Any] = client.get(f"/claims/{cid}").json()
    return out


def test_claim_file_1_gives_no_signal_score_0_and_straight_through(
    client: TestClient,
) -> None:
    cid = run_claim(client)
    c = claim(client, cid)
    assert c["signals"] == [] and c["score"] == 0
    assert c["outcome"] == "straight_through" and c["outcome_reasons"] == []
    assert c["agent_state"] == "closed" and c["status"] == "validated"
    last = client.get(f"/claims/{cid}/messages").json()[-1]
    assert last["role"] == "agent" and VALIDATED in last["text"]
    view = client.get(f"/claims/{cid}/customer").json()
    assert view == {
        "claim_id": cid,
        "policy_number": "TRV-2026-0417",
        "status": "validated",
        "message": VALIDATED,
    }


def test_checks_and_routing_are_two_tool_events_without_a_model(
    client: TestClient,
) -> None:
    cid = run_claim(client)
    events = client.app.state.store.events_since(cid, 0)  # type: ignore[attr-defined]
    stages = [e.stage for e in events if e.actor == "tool"]
    tail = stages[stages.index("run_checks") :]
    assert tail == ["run_checks", "assess_and_route", "update_claim"]
    for name in ("run_checks", "assess_and_route"):
        [e] = [e for e in events if e.stage == name]
        assert e.model is None and e.duration is not None
        assert e.inputs == {"claim_id": cid}
    [routed] = [e for e in events if e.stage == "assess_and_route"]
    assert routed.outputs["score"] == 0
    assert routed.outputs["outcome"] == "straight_through"
    assert routed.outputs["customer_message"] == VALIDATED


def test_the_agent_gets_the_customer_message_and_nothing_else(
    client: TestClient,
) -> None:
    cid = run_claim(client)
    gateway = client.app.state.gateway  # type: ignore[attr-defined]
    assert gateway.call(cid, "assess_and_route", {"claim_id": cid}) == {
        "customer_message": VALIDATED
    }
    assert gateway.call(cid, "run_checks", {"claim_id": cid}) == {}


def test_missing_medical_certificate_and_done_gives_r001_and_handler_review(
    client: TestClient,
) -> None:
    cid = run_claim(client, PDFS[:2], done=True)
    c = claim(client, cid)
    assert [s["code"] for s in c["signals"]] == ["R001"]
    assert c["score"] == 25 and c["outcome"] == "handler_review"
    assert any("missing" in r for r in c["outcome_reasons"])
    assert c["agent_state"] == "closed" and c["status"] == "in_review"
    [signal] = c["signals"]
    assert signal["evidence"] == [] and "medical certificate" in signal["detail"]
    last = client.get(f"/claims/{cid}/messages").json()[-1]["text"]
    assert REVIEW in last


def test_a_claimed_amount_of_1800_gives_c003(client: TestClient) -> None:
    cid = run_claim(client, amount="CHF 1,800")
    c = claim(client, cid)
    [s] = c["signals"]
    assert s["code"] == "C003" and s["points"] == 15
    docs = {d["id"]: d["file_metadata"]["original_name"] for d in c["documents"]}
    assert {docs[e["document_id"]] for e in s["evidence"]} == {
        "booking-confirmation",
        "cancellation-confirmation",
    }
    assert c["score"] == 15 and c["outcome"] == "straight_through"


def test_investigation_gives_the_same_customer_text_as_handler_review(
    client: TestClient,
) -> None:
    # incident before the policy (R002 40) and no certificate (R001 25): 65
    cid = run_claim(client, PDFS[:2], date="1 December 2025", done=True)
    c = claim(client, cid)
    assert c["outcome"] == "investigation" and c["score"] == 65
    texts = [m["text"] for m in client.get(f"/claims/{cid}/messages").json()]
    assert REVIEW in texts[-1]
    assert client.get(f"/claims/{cid}/customer").json()["message"] == REVIEW
    assert c["status"] == "in_review"


def test_the_customer_endpoint_leaks_nothing(client: TestClient) -> None:
    cid = run_claim(client, PDFS[:2], date="1 December 2025", done=True)
    body = (
        client.get(f"/claims/{cid}/customer").text
        + client.get(f"/claims/{cid}/messages").text
    ).lower()
    for word in ("fraud", "score", "signal", "r001", "r002", "investigation",
                 "handler_review", "straight_through", "evidence"):  # fmt: skip
        assert word not in body


def test_a_closed_claim_takes_no_more_documents(client: TestClient) -> None:
    cid = run_claim(client)
    data = (CLAIM1 / "booking-confirmation.pdf").read_bytes()
    res = client.post(f"/claims/{cid}/documents", files={"file": ("b.pdf", data)})
    assert res.status_code == 409
    assert len(claim(client, cid)["documents"]) == 3
    say = client.post(f"/claims/{cid}/messages", json={"text": "hello"}).json()
    assert "fraud" not in say[0]["text"].lower()
    assert claim(client, cid)["outcome"] == "straight_through"


def test_a_claim_type_with_no_workflow_goes_to_a_person(client: TestClient) -> None:
    cid = str(client.post("/claims", json={"customer_id": "C-1002"}).json()["id"])
    client.put(f"/claims/{cid}/policy", json={"policy_number": "HH-2024-0933"})
    client.post(
        f"/claims/{cid}/messages",
        json={"text": "Water damage on 14 September 2026, CHF 3,200"},
    )
    c = claim(client, cid)
    assert c["outcome"] == "handler_review" and c["agent_state"] == "closed"
    assert c["signals"] == []


def test_unknown_claim_has_no_customer_view(client: TestClient) -> None:
    assert client.get("/claims/CL-nope/customer").status_code == 404
