"""Non-negotiable 2: the customer never sees a score, a flag or the word "fraud"."""

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import Claim, FraudReport, Outcome
from app.views import CustomerView, customer_view
from tests import test_health_api, test_property_api
from tests.builders import signal, travel_claim
from tests.fake_chat import use_fake_model

VALIDATED = "Your claim is validated. You will receive a confirmation by e-mail."
REVIEW = "A claims specialist will review your case and contact you."


def routed(outcome: Outcome | None) -> Claim:
    signals = [signal("R002", 40), signal("T001", 20), signal("C001", 20)]
    return travel_claim(
        signals=signals,
        score=80,
        outcome=outcome,
        outcome_reasons=["Score 80 is 60 or more."],
        fraud_report=FraudReport(
            outcome=Outcome.investigation, score=80, summary="fraud: edited invoice"
        ),
        status="in_review",
    )


def test_the_view_holds_five_things_and_nothing_else() -> None:
    view = customer_view(routed(Outcome.investigation))
    assert set(CustomerView.model_fields) == {
        "claim_id",
        "policy_number",
        "status",
        "message",
        "step",
    }
    assert view.claim_id == "CL-test" and view.policy_number == "TRV-2026-0417"


def test_view_of_a_flagged_claim_has_no_score_code_outcome_or_fraud() -> None:
    for outcome in Outcome:
        text = customer_view(routed(outcome)).model_dump_json().lower()
        for word in ("fraud", "score", "80", "r002", "t001", "c001", "signal",
                     "evidence", "straight_through", "handler_review",
                     "investigation", "reason"):  # fmt: skip
            assert word not in text, (outcome, word)


def test_messages() -> None:
    assert customer_view(routed(Outcome.straight_through)).message == VALIDATED
    assert customer_view(routed(Outcome.handler_review)).message == REVIEW
    assert customer_view(routed(Outcome.investigation)).message == REVIEW


def test_handler_review_and_investigation_give_the_same_text() -> None:
    a = customer_view(routed(Outcome.handler_review))
    b = customer_view(routed(Outcome.investigation))
    assert a.message == b.message
    assert a.status == b.status


def test_no_message_before_the_result() -> None:
    view = customer_view(travel_claim())
    assert view.message is None and view.status == "open"


@pytest.fixture(params=["local", "nim"])
def client(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """Both agents: the scripted one, and the LLM agent with a fake model."""
    if request.param == "nim":
        use_fake_model(monkeypatch)
    monkeypatch.setenv("CLAIMS_DB", str(tmp_path / "claims.db"))
    monkeypatch.setenv("CLAIMS_UPLOADS", str(tmp_path / "uploads"))
    with TestClient(create_app()) as c:
        yield c


def customer_texts(client: TestClient, cid: str) -> list[str]:
    """Every answer the customer panel can get for this claim."""
    gateway_policies = client.get(f"/claims/{cid}/policies")
    return [
        client.get(f"/claims/{cid}/customer").text,
        client.get(f"/claims/{cid}/messages").text,
        client.put(
            f"/claims/{cid}/policy", json={"policy_number": "ACC-2026-2210"}
        ).text,
        client.post(f"/claims/{cid}/messages", json={"text": "hello"}).text,
        gateway_policies.text,
    ]


def test_the_customer_endpoints_of_claim_file_3_hold_nothing_of_the_report(
    client: TestClient,
) -> None:
    cid = test_health_api.run_claim(client)
    full: dict[str, Any] = client.get(f"/claims/{cid}").json()
    assert full["fraud_report"] is not None  # the pipeline view has it
    report = full["fraud_report"]
    secrets = [
        report["summary"],
        *(f["saw"] for f in report["findings"]),
        *(f["expected"] for f in report["findings"]),
        *report["not_checked"],
        *report["next_steps"],
    ]
    body = "\n".join(customer_texts(client, cid))
    low = body.lower()
    for text in secrets:
        assert text not in body
    # the score is a whole number: "130" inside a time stamp or an id is not a leak
    assert not re.search(r"\b130\b", body)
    for word in ("fraud", "score", "signal", "finding", "evidence", "tamper",
                 "investigation", "handler_review", "straight_through",
                 "r003", "r004", "r005", "t001", "t002", "t003", "c001",
                 "not_checked", "next_steps", "edited", "jonas"):  # fmt: skip
        assert word not in low, word


def test_the_customer_view_of_claim_file_3_equals_the_one_of_claim_file_2(
    client: TestClient,
) -> None:
    two = test_property_api.run_claim(client)
    three = test_health_api.run_claim(client)
    a = client.get(f"/claims/{two}/customer").json()
    b = client.get(f"/claims/{three}/customer").json()
    assert a["message"] == REVIEW and a["status"] == "in_review"
    ids = {"claim_id", "policy_number"}
    assert {k: v for k, v in a.items() if k not in ids} == {
        k: v for k, v in b.items() if k not in ids
    }
    assert (
        a["policy_number"] == "HH-2024-0933" and b["policy_number"] == "ACC-2026-2210"
    )
