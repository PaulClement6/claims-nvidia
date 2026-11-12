"""Rules R001 to R005 and the travel rules, on claims built in memory."""

from datetime import date

import pytest

from app.checks.rules import SHARED_RULES, SIGNALS, run_rules
from app.models import Claim
from app.workflows import WORKFLOWS
from tests.builders import document, travel_claim, travel_documents

TRAVEL = WORKFLOWS["travel"]


def codes(claim: Claim) -> list[str]:
    return [s.code for s in run_rules(claim, TRAVEL)]


def test_claim_file_1_has_no_signal() -> None:
    assert run_rules(travel_claim(), TRAVEL) == []


def test_signal_table_has_the_twelve_signals_of_the_fraud_doc() -> None:
    assert sorted(SIGNALS) == [
        "C001", "C002", "C003", "C004", "R001", "R002", "R003", "R004", "R005",
        "T001", "T002", "T003",
    ]  # fmt: skip
    assert [SIGNALS[c].points for c in ("R001", "R002", "R003", "R004", "R005")] == [
        25, 40, 20, 10, 20,
    ]  # fmt: skip


def test_every_rule_returns_its_own_code() -> None:
    assert [r.code for r in SHARED_RULES] == ["R001", "R002", "R003", "R004", "R005"]
    for rule in (*SHARED_RULES, *TRAVEL.rules):
        found = rule.check(travel_claim(incident_date=date(2025, 1, 1)))
        assert found is None or found.code == rule.code


def test_r001_names_the_missing_document_and_has_no_evidence() -> None:
    claim = travel_claim(documents=travel_documents()[:2])
    [s] = run_rules(claim, TRAVEL)
    assert s.code == "R001" and s.points == 25 and s.severity == "medium"
    assert "medical certificate" in s.detail
    assert s.evidence == []


def test_r001_when_nothing_is_uploaded_lists_all_three() -> None:
    [s] = run_rules(travel_claim(documents=[]), TRAVEL)
    for word in ("booking confirmation", "cancellation", "medical certificate"):
        assert word in s.detail


def test_medical_certificate_is_not_required_for_luggage() -> None:
    claim = travel_claim(cause="luggage", documents=travel_documents()[:2])
    assert codes(claim) == []


@pytest.mark.parametrize(
    ("incident", "expected"),
    [
        (date(2025, 12, 31), ["R002"]),
        (date(2027, 1, 1), ["R002"]),
        (date(2026, 1, 1), ["R003"]),
        (date(2026, 1, 15), ["R003"]),
        (date(2026, 1, 16), []),
        (date(2026, 12, 31), []),
    ],
)
def test_policy_period_rules(incident: date, expected: list[str]) -> None:
    claim = travel_claim(incident_date=incident)
    # the travel date rules need dates that fit; look at R002 and R003 only
    assert [c for c in codes(claim) if c in ("R002", "R003")] == expected


def test_r002_and_r003_points() -> None:
    [r2] = [s for s in run_rules(travel_claim(incident_date=date(2025, 6, 1)), TRAVEL)
            if s.code == "R002"]  # fmt: skip
    [r3] = [s for s in run_rules(travel_claim(incident_date=date(2026, 1, 5)), TRAVEL)
            if s.code == "R003"]  # fmt: skip
    assert (r2.points, r2.severity, r3.points, r3.severity) == (
        40,
        "high",
        20,
        "medium",
    )


@pytest.mark.parametrize(
    ("amount", "expected"),
    [(4749.99, []), (4750, ["R004"]), (5000, ["R004"]), (6000, ["R004"])],
)
def test_r004_at_95_percent_of_the_limit(amount: float, expected: list[str]) -> None:
    # refund 340 and price 1840 do not fit these amounts: keep R004 only
    got = [c for c in codes(travel_claim(claimed_amount=amount)) if c == "R004"]
    assert got == expected


def test_r005_invoice_before_the_incident_has_the_field_as_evidence() -> None:
    invoice = document(
        "D-inv", "medical_invoice", {"invoice_date": "2026-07-30", "total": 100.0}
    )
    claim = travel_claim(documents=[*travel_documents(), invoice])
    [s] = [s for s in run_rules(claim, TRAVEL) if s.code == "R005"]
    assert s.points == 20
    assert [(e.document_id, e.page) for e in s.evidence] == [("D-inv", 1)]
    ok = document("D-inv", "medical_invoice", {"invoice_date": "2026-08-03"})
    assert "R005" not in codes(travel_claim(documents=[*travel_documents(), ok]))


def test_cancellation_date_must_be_between_incident_and_departure() -> None:
    def with_cancel(day: str) -> Claim:
        docs = travel_documents()
        docs[1] = document(
            "D-cancel",
            "cancellation_confirmation",
            {"cancellation_date": day, "refund_received": 340.0},
        )
        return travel_claim(documents=docs)

    for ok in ("2026-08-03", "2026-08-04", "2026-08-10"):
        assert codes(with_cancel(ok)) == []
    for bad in ("2026-08-02", "2026-08-11"):
        [s] = run_rules(with_cancel(bad), TRAVEL)
        assert s.code == "C002" and s.category == "consistency" and s.points == 15
        assert {(e.document_id, e.page) for e in s.evidence} == {
            ("D-cancel", 1),
            ("D-book", 1),
        }


def test_amount_must_equal_price_minus_refund() -> None:
    [s] = run_rules(travel_claim(claimed_amount=1800.0), TRAVEL)
    assert s.code == "C003" and s.points == 15 and s.category == "consistency"
    assert "1800" in s.detail and "1500" in s.detail
    assert {(e.document_id, e.page) for e in s.evidence} == {
        ("D-book", 1),
        ("D-cancel", 1),
    }


def test_a_rule_with_a_missing_field_gives_no_signal() -> None:
    # no cancellation document: R001 only (the missing document is its job)
    claim = travel_claim(documents=[travel_documents()[0], travel_documents()[2]])
    assert codes(claim) == ["R001"]
    # no incident date or amount yet: no date or amount rule can fire
    bare = travel_claim(incident_date=None, claimed_amount=None)
    assert run_rules(bare, TRAVEL) == []


def test_every_signal_with_a_field_carries_evidence() -> None:
    claim = travel_claim(claimed_amount=1800.0)
    assert all(s.evidence for s in run_rules(claim, TRAVEL))


def test_claim_type_with_no_workflow_runs_the_shared_rules_only() -> None:
    claim = travel_claim(type="health", incident_date=date(2025, 1, 1))
    assert [s.code for s in run_rules(claim, None)] == ["R002"]
