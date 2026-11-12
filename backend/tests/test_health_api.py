"""Claim file 3 (health) end to end through the HTTP API, and the medical-log rule."""

import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.reader_contract import MOCK

CLAIM3 = MOCK / "claim-3-health"
FILES = ["medical-invoice.pdf", "medical-report.pdf"]
STORY = "I sprained my ankle in an accident."
REVIEW = "A claims specialist will review your case and contact you."


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CLAIMS_DB", str(tmp_path / "claims.db"))
    monkeypatch.setenv("CLAIMS_UPLOADS", str(tmp_path / "uploads"))
    with TestClient(create_app()) as c:
        yield c


def run_claim(
    client: TestClient,
    files: list[str] = FILES,
    date: str = "25 October 2026",
    amount: str = "CHF 4,450",
    done: bool = True,
) -> str:
    cid = str(client.post("/claims", json={"customer_id": "C-1003"}).json()["claim_id"])
    client.put(f"/claims/{cid}/policy", json={"policy_number": "ACC-2026-2210"})
    for text in (STORY, date, amount):
        res = client.post(f"/claims/{cid}/messages", json={"text": text})
        assert res.status_code == 200
    for name in files:
        data = (CLAIM3 / name).read_bytes()
        res = client.post(f"/claims/{cid}/documents", files={"file": (name, data)})
        assert res.status_code == 201
    if done:
        client.post(f"/claims/{cid}/messages", json={"text": "done"})
    return cid


def claim(client: TestClient, cid: str) -> dict[str, Any]:
    out: dict[str, Any] = client.get(f"/claims/{cid}").json()
    return out


def test_claim_file_3_gives_seven_signals_and_a_score_of_130(
    client: TestClient,
) -> None:
    c = claim(client, run_claim(client))
    assert c["type"] == "health" and c["cause"] == "accident"
    assert sorted(s["code"] for s in c["signals"]) == [
        "C001", "R003", "R004", "R005", "T001", "T002", "T003",
    ]  # fmt: skip
    assert c["score"] == 130 and c["outcome"] == "investigation"
    assert c["agent_state"] == "closed" and c["status"] == "in_review"


def test_every_signal_of_claim_file_3_has_a_document_and_page_as_evidence(
    client: TestClient,
) -> None:
    c = claim(client, run_claim(client))
    names = {d["id"]: d["file_metadata"]["original_name"] for d in c["documents"]}
    by_code = {s["code"]: s for s in c["signals"]}
    for code in ("R004", "R005", "T001", "T002", "T003", "C001"):
        [e] = by_code[code]["evidence"]
        assert names[e["document_id"]] == "medical-invoice.pdf" and e["page"] == 1
    # R003: the report holds the incident date; R004: the invoice holds the total
    [e] = by_code["R003"]["evidence"]
    assert names[e["document_id"]] == "medical-report.pdf" and e["page"] == 1


def test_the_customer_gets_the_review_text_and_nothing_else(client: TestClient) -> None:
    cid = run_claim(client)
    assert client.get(f"/claims/{cid}/customer").json()["message"] == REVIEW
    assert client.get(f"/claims/{cid}/messages").json()[-1]["text"].endswith(REVIEW)


def test_the_two_new_checks_are_two_events_between_the_checks_and_the_routing(
    client: TestClient,
) -> None:
    cid = run_claim(client)
    events = client.app.state.store.events_since(cid, 0)  # type: ignore[attr-defined]
    stages = [e.stage for e in events if e.actor == "tool"]
    tail = stages[stages.index("run_checks") :]
    assert tail[:5] == [
        "run_checks",
        "check_tampering",
        "check_consistency",
        "write_fraud_report",  # nested in the routing: its event is written first
        "assess_and_route",
    ]
    for name in ("check_tampering", "check_consistency"):
        [e] = [e for e in events if e.stage == name]
        assert e.model is None and e.duration is not None
        assert e.inputs == {"claim_id": cid}
    [t] = [e for e in events if e.stage == "check_tampering"]
    assert [s["code"] for s in t.outputs["signals"]] == ["T001", "T002", "T003"]
    [c] = [e for e in events if e.stage == "check_consistency"]
    assert [s["code"] for s in c.outputs["signals"]] == ["C001"]


def test_the_medical_invoice_alone_is_not_enough(client: TestClient) -> None:
    cid = run_claim(client, FILES[:1], done=True)
    c = claim(client, cid)
    assert "R001" in [s["code"] for s in c["signals"]]
    assert c["outcome"] == "investigation"  # 155 points


def test_a_treatment_before_the_declared_incident_gives_c002(
    client: TestClient,
) -> None:
    cid = run_claim(client, FILES[1:], date="1 December 2026", amount="CHF 900")
    c = claim(client, cid)
    assert sorted(s["code"] for s in c["signals"]) == ["C002", "R001"]
    assert c["outcome"] == "handler_review"  # 40 points, a document is missing


def test_medical_text_never_reaches_the_application_logs(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    cid = run_claim(client)
    assert caplog.records, "the capture works"
    logged = " ".join(r.getMessage() for r in caplog.records).lower()
    for word in ("sprained", "jonas", "ankle", "lukas", "physiotherapy"):
        assert word not in logged
    # ... but the pipeline events hold the fields: that is their job
    events = client.app.state.store.events_since(cid, 0)  # type: ignore[attr-defined]
    assert "Jonas Meier" in " ".join(str(e.outputs) for e in events)
