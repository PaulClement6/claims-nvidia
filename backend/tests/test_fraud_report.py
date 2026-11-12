"""Non-negotiable 4: a claim routed to `investigation` always has a fraud report."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.checks.rules import SIGNALS, signal
from app.fraud.report import NOT_CHECKED, write_report
from app.main import create_app
from app.models import FraudReport, Outcome, Source
from app.store import InvestigationWithoutReport, Store
from tests.builders import travel_claim
from tests.test_health_api import FILES, run_claim


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(str(tmp_path / "claims.db"))


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CLAIMS_DB", str(tmp_path / "claims.db"))
    monkeypatch.setenv("CLAIMS_UPLOADS", str(tmp_path / "uploads"))
    with TestClient(create_app()) as c:
        yield c


def a_report() -> FraudReport:
    return FraudReport(
        outcome=Outcome.investigation, score=80, summary="Why.", findings=[]
    )


# --- the save path ---------------------------------------------------------


def test_the_store_refuses_investigation_with_no_report(store: Store) -> None:
    claim = store.create_claim("C-1003")
    assert claim is not None
    with pytest.raises(InvestigationWithoutReport, match="fraud report"):
        store.update_claim(claim.id, {"outcome": "investigation", "score": 80})
    saved = store.get_claim(claim.id)
    assert saved is not None and saved.outcome is None  # nothing was written


def test_the_store_refuses_it_through_every_save_call(store: Store) -> None:
    claim = store.create_claim("C-1003")
    assert claim is not None
    with pytest.raises(InvestigationWithoutReport):
        store.save_routing(claim.id, 80, Outcome.investigation, ["r"], "in_review")
    with pytest.raises(InvestigationWithoutReport):
        store._save_claim(travel_claim(outcome=Outcome.investigation))


def test_outcome_and_report_are_saved_together(store: Store) -> None:
    claim = store.create_claim("C-1003")
    assert claim is not None
    store.save_routing(
        claim.id, 80, Outcome.investigation, ["r"], "in_review", a_report()
    )
    saved = store.get_claim(claim.id)
    assert saved is not None and saved.outcome == Outcome.investigation
    assert saved.fraud_report == a_report() and saved.score == 80


@pytest.mark.parametrize("outcome", [Outcome.straight_through, Outcome.handler_review])
def test_the_other_outcomes_need_no_report(store: Store, outcome: Outcome) -> None:
    claim = store.create_claim("C-1003")
    assert claim is not None
    store.save_routing(claim.id, 20, outcome, [], "in_review")
    saved = store.get_claim(claim.id)
    assert saved is not None and saved.outcome == outcome
    assert saved.fraud_report is None


# --- the template ----------------------------------------------------------


def three_signals() -> list[Any]:
    src = Source(document_id="D-1", page=1, snippet="x")
    return [
        signal("R003", "Incident 5 days after the start."),
        signal("T001", "inv.pdf was last written by 'PDF Edit Studio 4.2'.", [src]),
        signal("T003", "The lines add up to CHF 1450; total CHF 4450.", [src]),
        signal("C001", "A document names Jonas Meier.", [src]),
    ]


def test_the_report_is_built_from_the_signals_only() -> None:
    claim = travel_claim(documents=[])
    signals = three_signals()
    report = write_report(claim, signals, Outcome.investigation, 90)
    assert (report.outcome, report.score) == (Outcome.investigation, 90)
    assert [f.code for f in report.findings] == ["T003", "T001", "C001", "R003"]
    for f in report.findings:
        s = next(s for s in signals if s.code == f.code)
        assert (f.saw, f.expected) == (s.detail, SIGNALS[f.code].expected)
        assert f.expected and f.where
    assert report.not_checked == NOT_CHECKED and len(NOT_CHECKED) == 4


def test_the_summary_is_one_sentence_from_the_three_highest_signals() -> None:
    report = write_report(
        travel_claim(documents=[]), three_signals(), Outcome.investigation, 90
    )
    # T003 30 points, then T001 and C001 (20): tampering first on a tie, R003 left out
    assert report.summary == (
        "The document was edited after it was issued, its total does not match its "
        "lines, and a name on it is not the insured person."
    )


def test_next_steps_come_from_the_signal_categories() -> None:
    report = write_report(
        travel_claim(documents=[]), three_signals(), Outcome.investigation, 90
    )
    assert report.next_steps[0] == "Ask the provider for the original invoice."
    assert len(report.next_steps) == 3  # tampering, consistency, rule
    only_rule = write_report(
        travel_claim(documents=[]), [signal("R002", "x")], Outcome.investigation, 40
    )
    assert len(only_rule.next_steps) == 1


# --- claim file 3 end to end -----------------------------------------------


def test_claim_file_3_has_a_report_that_names_all_seven_signals(
    client: TestClient,
) -> None:
    cid = run_claim(client)
    c: dict[str, Any] = client.get(f"/claims/{cid}").json()
    assert c["outcome"] == "investigation" and c["score"] == 130
    r = c["fraud_report"]
    assert (r["outcome"], r["score"]) == ("investigation", 130)
    assert sorted(f["code"] for f in r["findings"]) == [
        "C001", "R003", "R004", "R005", "T001", "T002", "T003",
    ]  # fmt: skip
    assert r["summary"] == (
        "The medical invoice was edited after it was issued, its total does not "
        "match its lines, and a name on it is not the insured person."
    )
    for f in r["findings"]:
        assert f["saw"] and f["expected"]
        [where] = f["where"]
        assert where in {"medical-invoice.pdf, page 1", "medical-report.pdf, page 1"}
    assert {s["code"] for s in c["signals"]} == {f["code"] for f in r["findings"]}
    assert len(r["not_checked"]) == 4 and r["next_steps"]


def test_the_report_is_written_in_the_same_turn_before_the_customer_message(
    client: TestClient,
) -> None:
    cid = run_claim(client)
    events = client.app.state.store.events_since(cid, 0)  # type: ignore[attr-defined]
    tools = [e.stage for e in events if e.actor == "tool"]
    assert tools.index("assess_and_route") + 1 == tools.index("write_fraud_report")
    [write] = [e for e in events if e.stage == "write_fraud_report"]
    assert write.model is None and "template" in write.title
    assert write.outputs["outcome"] == "investigation"
    last = [e for e in events if e.actor == "agent"][-1]
    assert write.sequence < last.sequence  # before the customer message


def test_a_claim_with_no_investigation_has_no_report_and_no_report_event(
    client: TestClient,
) -> None:
    cid = run_claim(client, FILES[1:], date="1 December 2026", amount="CHF 900")
    c = client.get(f"/claims/{cid}").json()
    assert c["outcome"] == "handler_review" and c["fraud_report"] is None
    events = client.app.state.store.events_since(cid, 0)  # type: ignore[attr-defined]
    assert "write_fraud_report" not in [e.stage for e in events]


def test_the_tool_refuses_a_wrong_outcome(client: TestClient) -> None:
    cid = run_claim(client)
    gateway = client.app.state.gateway  # type: ignore[attr-defined]
    c = client.get(f"/claims/{cid}").json()
    for bad in (
        {"outcome": "handler_review", "score": 130},
        {"outcome": "investigation", "score": 5},
    ):
        with pytest.raises(ValueError):
            gateway.call(
                cid, "write_fraud_report", {"claim_id": cid, "reasons": [], **bad}
            )
    assert client.get(f"/claims/{cid}").json() == c  # nothing changed


def test_a_failing_report_leaves_the_claim_open_not_investigated(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.tools.routing as routing

    def boom(*a: object, **k: object) -> FraudReport:
        raise RuntimeError("template failed")

    monkeypatch.setattr(routing, "write_report", boom)
    with pytest.raises(RuntimeError):
        run_claim(client)
    rows = client.app.state.store.db.execute("SELECT id FROM claims").fetchall()  # type: ignore[attr-defined]
    [(cid,)] = rows
    c = client.get(f"/claims/{cid}").json()
    assert c["outcome"] is None and c["fraud_report"] is None
