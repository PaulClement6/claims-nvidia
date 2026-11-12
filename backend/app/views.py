"""What the customer sees (non-negotiable 2, decision D18).

The customer view drops signals, score, reasons and the report. The two outcomes
that go to a person give the same exact text: a different text would tell a
fraudster that the system flagged the claim.
"""

from pydantic import BaseModel

from app.models import Claim, Outcome

VALIDATED = "Your claim is validated. You will receive a confirmation by e-mail."
TO_A_PERSON = "A claims specialist will review your case and contact you."
MESSAGES = {
    Outcome.straight_through: VALIDATED,
    Outcome.handler_review: TO_A_PERSON,
    Outcome.investigation: TO_A_PERSON,
}
# The status is as blunt as the message: both outcomes that go to a person match.
STATUS = {
    Outcome.straight_through: "validated",
    Outcome.handler_review: "in_review",
    Outcome.investigation: "in_review",
}


class CustomerView(BaseModel):
    claim_id: str
    policy_number: str | None
    status: str
    message: str | None


def customer_message(outcome: Outcome) -> str:
    return MESSAGES[outcome]


def customer_status(outcome: Outcome | None) -> str:
    return STATUS[outcome] if outcome else "open"


def customer_view(claim: Claim) -> CustomerView:
    return CustomerView(
        claim_id=claim.id,
        policy_number=claim.policy.number if claim.policy else None,
        status=customer_status(claim.outcome),
        message=customer_message(claim.outcome) if claim.outcome else None,
    )
