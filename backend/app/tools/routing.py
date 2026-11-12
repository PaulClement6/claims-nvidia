"""Tools `run_checks`, `assess_and_route` and `write_fraud_report`: no model.

`assess_and_route` is the only place that decides the outcome. For `investigation`
it saves nothing yet: the claim must never be saved as an investigation with no
report (non-negotiable 4). The agent then calls `write_fraud_report` with that
result, and the tool saves the outcome and the report together, in one store call.
"""

from typing import Any

from app.checks.rules import run_rules
from app.fraud.report import write_report
from app.fraud.routing import route
from app.fraud.routing import score as sum_points
from app.models import Claim, Outcome
from app.tools.gateway import Gateway
from app.views import customer_message, customer_status
from app.workflows import WORKFLOWS


def register(gateway: Gateway) -> None:
    def load(claim_id: str) -> Claim:
        claim = gateway.store.get_claim(claim_id)
        if claim is None:
            raise KeyError(f"unknown claim: {claim_id}")
        return claim

    def run_checks(claim_id: str) -> dict[str, Any]:
        claim = load(claim_id)
        signals = run_rules(claim, WORKFLOWS.get(claim.type or ""))
        dumped = [s.model_dump(mode="json") for s in signals]
        gateway.store.update_claim(claim_id, {"signals": dumped})
        return {"signals": dumped}

    def assess_and_route(claim_id: str) -> dict[str, Any]:
        claim = load(claim_id)
        total = sum_points(claim.signals)
        outcome, reasons = route(claim, claim.signals, WORKFLOWS.get(claim.type or ""))
        if outcome != Outcome.investigation:
            gateway.store.save_routing(
                claim_id, total, outcome, reasons, customer_status(outcome)
            )
        return {
            "score": total,
            "outcome": outcome.value,
            "reasons": reasons,
            "customer_message": customer_message(outcome),
        }

    def write_fraud_report(
        claim_id: str, outcome: str, score: int, reasons: list[str]
    ) -> dict[str, Any]:
        """Writes the report and saves it with the outcome of `assess_and_route`.
        It refuses a result the routing would not give for the signals of the claim."""
        claim = load(claim_id)
        given = Outcome(outcome)
        want, _ = route(claim, claim.signals, WORKFLOWS.get(claim.type or ""))
        if given != Outcome.investigation or want != given:
            raise ValueError(
                "a fraud report is only for a claim routed to investigation"
            )
        if score != sum_points(claim.signals):
            raise ValueError("the score is not the score of the signals")
        report = write_report(claim, claim.signals, given, score)
        gateway.store.save_routing(
            claim_id, score, given, reasons, customer_status(given), report
        )
        return report.model_dump(mode="json")

    # The agent gets nothing from the checks, and only the customer text from the
    # routing. The event keeps the full result for the pipeline panel.
    gateway.register("run_checks", run_checks, visible=())
    # The agent gets the result it needs to go on: the customer text, and the outcome,
    # the score and the reasons to hand to `write_fraud_report`.
    gateway.register(
        "assess_and_route",
        assess_and_route,
        visible=("customer_message", "outcome", "score", "reasons"),
    )
    gateway.register(
        "write_fraud_report",
        write_fraud_report,
        visible=(),
        title="Write fraud report (template)",
    )
