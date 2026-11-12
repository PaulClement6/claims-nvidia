"""Tools `run_checks` and `assess_and_route`: deterministic code, no model."""

from typing import Any

from app.checks.rules import run_rules
from app.fraud.routing import route, score
from app.models import Claim
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
        total = score(claim.signals)
        outcome, reasons = route(claim, claim.signals, WORKFLOWS.get(claim.type or ""))
        gateway.store.update_claim(
            claim_id,
            {
                "score": total,
                "outcome": outcome.value,
                "outcome_reasons": reasons,
                "status": customer_status(outcome),
            },
        )
        return {
            "score": total,
            "outcome": outcome.value,
            "reasons": reasons,
            "customer_message": customer_message(outcome),
        }

    # The agent gets nothing from the checks, and only the customer text from the
    # routing. The event keeps the full result for the pipeline panel.
    gateway.register("run_checks", run_checks, visible=())
    gateway.register(
        "assess_and_route", assess_and_route, visible=("customer_message",)
    )
