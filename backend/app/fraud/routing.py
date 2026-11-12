"""Score and routing: the four rules of docs/FRAUD.md. Pure and deterministic.

There is no code path to a rejection: `Outcome` has three values and `route`
returns one of them.
"""

from app.models import Claim, Outcome, Signal
from app.workflows.engine import Workflow

LOW_CONFIDENCE = 0.8  # below this, a field needs a person
INVESTIGATION_SCORE = 60
REVIEW_SCORE = 20


def score(signals: list[Signal]) -> int:
    return sum(s.points for s in signals)


def route(
    claim: Claim, signals: list[Signal], workflow: Workflow | None
) -> tuple[Outcome, list[str]]:
    total = score(signals)
    if total >= INVESTIGATION_SCORE:
        return Outcome.investigation, [
            f"Score {total} is {INVESTIGATION_SCORE} or more."
        ]
    reasons: list[str] = []
    if total >= REVIEW_SCORE:
        reasons.append(f"Score {total} is {REVIEW_SCORE} or more.")
    if any(s.code == "R001" for s in signals):
        reasons.append("A required document is missing.")
    if workflow is None:
        reasons.append("No workflow for this claim type: a person must read it.")
    elif (
        claim.claimed_amount is not None
        and claim.claimed_amount > workflow.straight_through_limit
    ):
        reasons.append(
            f"Claimed amount CHF {claim.claimed_amount:,.2f} is above the "
            f"straight-through limit of CHF {workflow.straight_through_limit:,.0f}."
        )
    low = [
        f"{f.name} ({f.confidence:.2f})"
        for d in claim.documents
        for f in d.fields
        if f.confidence < LOW_CONFIDENCE
    ]
    if low:
        reasons.append(f"A field has low confidence: {', '.join(low)}.")
    return (Outcome.handler_review if reasons else Outcome.straight_through), reasons
