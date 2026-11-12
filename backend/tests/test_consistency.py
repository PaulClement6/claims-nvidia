"""C001, C002, C003 from the extracted fields. Travel keeps its own C002 and C003."""

from datetime import date

import pytest

from app.checks.consistency import check_consistency
from app.checks.rules import run_rules
from app.models import Claim
from app.workflows import WORKFLOWS
from tests.builders import document, travel_claim, travel_documents

HEALTH = WORKFLOWS["health"]


def health_claim(**changes: object) -> Claim:
    docs = [
        document(
            "D-inv",
            "medical_invoice",
            {
                "patient_name": "Lukas Meier",
                "invoice_date": "2026-10-26",
                "total": 4450.0,
            },
        ),
        document(
            "D-rep",
            "medical_report",
            {"patient_name": "Lukas Meier", "treatment_date": "2026-10-25"},
        ),
    ]
    base: dict[str, object] = {
        "customer": {"id": "C-1003", "name": "Lukas Meier"},
        "type": "health",
        "incident_date": date(2026, 10, 25),
        "claimed_amount": 4450.0,
        "cause": "accident",
        "documents": docs,
    }
    return travel_claim(**{**base, **changes})


def codes(claim: Claim) -> list[str]:
    return [s.code for s in check_consistency(claim, WORKFLOWS[claim.type or ""])]


def test_a_consistent_health_claim_has_no_signal() -> None:
    assert codes(health_claim()) == []


def test_c001_another_name_on_any_document_with_its_source() -> None:
    inv = document("D-inv", "medical_invoice", {"patient_name": "Jonas Meier"})
    claim = health_claim(documents=[inv, health_claim().documents[1]])
    [s] = check_consistency(claim, HEALTH)
    assert (s.code, s.points) == ("C001", 20)
    assert [(e.document_id, e.page) for e in s.evidence] == [("D-inv", 1)]
    assert "Jonas Meier" in s.detail


@pytest.mark.parametrize("name", ["  lukas   MEIER ", "LUKAS MEIER", "Lukas\tMeier"])
def test_c001_ignores_case_and_spaces(name: str) -> None:
    inv = document("D-inv", "medical_invoice", {"patient_name": name})
    assert codes(health_claim(documents=[inv])) == []


@pytest.mark.parametrize("field", ["patient_name", "customer_name", "traveller_name"])
def test_c001_reads_the_three_name_fields(field: str) -> None:
    doc = document("D-x", "receipt", {field: "Someone Else"})
    assert codes(health_claim(documents=[doc])) == ["C001"]


def test_c001_two_wrong_names_are_one_signal_with_two_sources() -> None:
    a = document("D-a", "medical_invoice", {"patient_name": "Jonas Meier"})
    b = document("D-b", "medical_report", {"customer_name": "Eva Meier"})
    [s] = check_consistency(health_claim(documents=[a, b]), HEALTH)
    assert [e.document_id for e in s.evidence] == ["D-a", "D-b"]


def test_c002_a_treatment_before_the_incident() -> None:
    rep = document("D-rep", "medical_report", {"treatment_date": "2026-10-24"})
    [s] = check_consistency(health_claim(documents=[rep]), HEALTH)
    assert (s.code, s.points) == ("C002", 15)
    assert [e.document_id for e in s.evidence] == ["D-rep"]


def test_c002_a_treatment_on_the_incident_day_or_later_is_fine() -> None:
    for day in ("2026-10-25", "2026-11-30"):
        rep = document("D-rep", "medical_report", {"treatment_date": day})
        assert codes(health_claim(documents=[rep])) == []


def test_c003_the_claimed_amount_is_not_the_documented_total() -> None:
    [s] = check_consistency(health_claim(claimed_amount=3000.0), HEALTH)
    assert (s.code, s.points) == ("C003", 15)
    assert [e.document_id for e in s.evidence] == ["D-inv"]
    assert "3000" in s.detail and "4450" in s.detail


def test_c003_needs_a_claimed_amount_and_a_total() -> None:
    assert codes(health_claim(claimed_amount=None)) == []
    assert codes(health_claim(documents=[])) == []


def test_the_missing_pieces_give_no_signal_and_no_crash() -> None:
    claim = health_claim(incident_date=None, documents=[document("D", "receipt", {})])
    assert codes(claim) == []
    assert check_consistency(health_claim(type=None), None) == []


def test_travel_keeps_its_own_c002_and_c003_one_place_per_code() -> None:
    """The travel workflow gives C002 and C003; this module skips the codes a
    workflow owns, so a travel claim never gets either signal twice."""
    wrong = travel_documents()
    wrong[1] = document(
        "D-cancel",
        "cancellation_confirmation",
        {"cancellation_date": "2026-08-01", "refund_received": 340.0},
    )
    claim = travel_claim(documents=wrong, claimed_amount=1800.0)
    travel = WORKFLOWS["travel"]
    assert sorted(s.code for s in run_rules(claim, travel) if s.code[0] == "C") == [
        "C002",
        "C003",
    ]
    assert check_consistency(claim, travel) == []
