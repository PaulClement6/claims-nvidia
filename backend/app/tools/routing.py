"""Tools `run_checks`, `assess_and_route` and `write_fraud_report`: no model.

`assess_and_route` is the only place that decides the outcome, and the agent gets
the customer message from it, nothing else. For `investigation` it calls
`write_fraud_report` itself (trusted code, not the agent), which computes the same
outcome from the claim and saves outcome and report together, in one store call
(non-negotiable 4). If the report fails, `assess_and_route` fails and nothing is
saved. The report tool is called inside `assess_and_route`, so its event is written
first (it ends first): "Write fraud report" comes right before "Assess and route".
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
        if outcome == Outcome.investigation:
            # its own event (rule 5); it saves the outcome and the report together
            gateway.call(claim_id, "write_fraud_report", {"claim_id": claim_id})
        else:
            gateway.store.save_routing(
                claim_id, total, outcome, reasons, customer_status(outcome)
            )
        return {
            "score": total,
            "outcome": outcome.value,
            "reasons": reasons,
            "customer_message": customer_message(outcome),
        }

    def write_fraud_report(claim_id: str) -> dict[str, Any]:
        """Writes the report and saves it with the outcome, score and reasons that
        the routing gives for the signals of the claim. Nothing comes from a caller."""
        claim = load(claim_id)
        outcome, reasons = route(claim, claim.signals, WORKFLOWS.get(claim.type or ""))
        if outcome != Outcome.investigation:
            raise ValueError(
                "a fraud report is only for a claim routed to investigation"
            )
        total = sum_points(claim.signals)
        report = write_report(claim, claim.signals, outcome, total)
        gateway.store.save_routing(
            claim_id, total, outcome, reasons, customer_status(outcome), report
        )
        return report.model_dump(mode="json")

    # The agent gets nothing from the checks, and only the customer text from the
    # routing. The event keeps the full result for the pipeline panel.
    gateway.register(
        "run_checks",
        run_checks,
        visible=(),
        description=(
            "Check step 1 of 3. Run it only when the customer says they are done "
            "and have no more documents."
        ),
    )
    gateway.register(
        "assess_and_route",
        assess_and_route,
        visible=("customer_message",),
        description=(
            "The last step. Call it after run_checks, check_tampering and "
            "check_consistency. Returns the message for the customer."
        ),
    )
    gateway.register(
        "write_fraud_report",
        write_fraud_report,
        visible=(),
        title="Write fraud report (template)",
    )
