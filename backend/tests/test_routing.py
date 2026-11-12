"""Score and routing: the four rules of docs/FRAUD.md."""

import pytest

from app.fraud.routing import LOW_CONFIDENCE, route, score
from app.models import Outcome
from app.workflows import WORKFLOWS
from tests.builders import document, signal, travel_claim, travel_documents

TRAVEL = WORKFLOWS["travel"]


def test_score_is_the_sum_of_the_points() -> None:
    assert score([]) == 0
    assert score([signal("R001", 25), signal("R004", 10)]) == 35


def test_no_signal_is_straight_through() -> None:
    assert route(travel_claim(), [], TRAVEL) == (Outcome.straight_through, [])


def test_score_below_20_stays_straight_through() -> None:
    out, reasons = route(travel_claim(), [signal("C003", 15)], TRAVEL)
    assert (out, reasons) == (Outcome.straight_through, [])


@pytest.mark.parametrize(
    ("points", "expected"),
    [(19, Outcome.straight_through), (20, Outcome.handler_review),
     (59, Outcome.handler_review), (60, Outcome.investigation)],
)  # fmt: skip
def test_thresholds(points: int, expected: Outcome) -> None:
    out, reasons = route(travel_claim(), [signal("C001", points)], TRAVEL)
    assert out == expected
    assert (reasons != []) == (expected != Outcome.straight_through)


def test_investigation_wins_over_the_review_reasons() -> None:
    out, reasons = route(
        travel_claim(claimed_amount=9000),
        [signal("R002", 40), signal("R001", 25)],
        TRAVEL,
    )
    assert out == Outcome.investigation
    assert any("65" in r for r in reasons)


def test_missing_document_is_handler_review_even_with_a_low_score() -> None:
    out, reasons = route(travel_claim(), [signal("R001", 5)], TRAVEL)
    assert out == Outcome.handler_review
    assert any("missing" in r for r in reasons)


def test_amount_above_the_limit_is_handler_review() -> None:
    assert TRAVEL.straight_through_limit == 2500
    assert route(travel_claim(claimed_amount=2500), [], TRAVEL)[0] == (
        Outcome.straight_through
    )
    out, reasons = route(travel_claim(claimed_amount=2500.01), [], TRAVEL)
    assert out == Outcome.handler_review
    assert any("2,500" in r for r in reasons)


def test_low_confidence_is_handler_review() -> None:
    assert LOW_CONFIDENCE == 0.8
    docs = travel_documents()
    docs[0] = document("D-book", "booking_confirmation", {"trip_price": 1840.0}, 0.79)
    out, reasons = route(travel_claim(documents=docs), [], TRAVEL)
    assert out == Outcome.handler_review
    assert any("confidence" in r for r in reasons)
    docs[0] = document("D-book", "booking_confirmation", {"trip_price": 1840.0}, 0.8)
    assert route(travel_claim(documents=docs), [], TRAVEL)[0] == (
        Outcome.straight_through
    )


def test_no_workflow_goes_to_a_person() -> None:
    out, reasons = route(travel_claim(type="property"), [], None)
    assert out == Outcome.handler_review and reasons
