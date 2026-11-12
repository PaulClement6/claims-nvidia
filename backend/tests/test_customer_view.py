"""Non-negotiable 2: the customer never sees a score, a flag or the word "fraud"."""

from app.models import Claim, FraudReport, Outcome
from app.views import CustomerView, customer_view
from tests.builders import signal, travel_claim

VALIDATED = "Your claim is validated. You will receive a confirmation by e-mail."
REVIEW = "A claims specialist will review your case and contact you."


def routed(outcome: Outcome | None) -> Claim:
    signals = [signal("R002", 40), signal("T001", 20), signal("C001", 20)]
    return travel_claim(
        signals=signals,
        score=80,
        outcome=outcome,
        outcome_reasons=["Score 80 is 60 or more."],
        fraud_report=FraudReport(summary="fraud: edited invoice", signals=signals),
        status="in_review",
    )


def test_the_view_holds_four_things_and_nothing_else() -> None:
    view = customer_view(routed(Outcome.investigation))
    assert set(CustomerView.model_fields) == {
        "claim_id",
        "policy_number",
        "status",
        "message",
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
