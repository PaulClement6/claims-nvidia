"""Tools `check_tampering` and `check_consistency`: deterministic code, no model.

Each adds its signals to the ones `run_checks` put on the claim: a code it gives
replaces the claim's signal of the same code, the others stay.
"""

from pathlib import Path
from typing import Any

from app.checks.consistency import check_consistency
from app.checks.tampering import check_tampering
from app.models import Claim, Signal
from app.tools.gateway import Gateway
from app.workflows import WORKFLOWS


def register(gateway: Gateway, uploads: Path) -> None:
    def load(claim_id: str) -> Claim:
        claim = gateway.store.get_claim(claim_id)
        if claim is None:
            raise KeyError(f"unknown claim: {claim_id}")
        return claim

    def add(claim: Claim, found: list[Signal]) -> dict[str, Any]:
        new = {s.code for s in found}
        kept = [s for s in claim.signals if s.code not in new]
        signals = [s.model_dump(mode="json") for s in [*kept, *found]]
        gateway.store.update_claim(claim.id, {"signals": signals})
        return {"signals": [s.model_dump(mode="json") for s in found]}

    def tampering(claim_id: str) -> dict[str, Any]:
        claim = load(claim_id)
        return add(claim, check_tampering(claim, uploads))

    def consistency(claim_id: str) -> dict[str, Any]:
        claim = load(claim_id)
        workflow = WORKFLOWS.get(claim.type or "")
        return add(claim, check_consistency(claim, workflow))

    # like run_checks: the agent gets nothing back, the event keeps the signals
    gateway.register(
        "check_tampering",
        tampering,
        visible=(),
        description=(
            "Check step 2 of 3. Run it when the customer is done, after run_checks."
        ),
    )
    gateway.register(
        "check_consistency",
        consistency,
        visible=(),
        description=(
            "Check step 3 of 3. Run it when the customer is done, after run_checks."
        ),
    )
