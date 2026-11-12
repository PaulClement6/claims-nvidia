"""Workflow C: travel (docs/WORKFLOWS.md)."""

from app.checks.rules import (
    Rule,
    as_date,
    as_number,
    find_field,
    signal,
    sources,
)
from app.models import Claim, Signal
from app.workflows.engine import RequiredDocument, Workflow


def required_documents(cause: str | None) -> list[RequiredDocument]:
    docs: list[RequiredDocument] = [
        {"label": "a booking confirmation", "any_of": ["booking_confirmation"]},
        {
            "label": "a cancellation confirmation or a receipt",
            "any_of": ["cancellation_confirmation", "receipt"],
        },
    ]
    if cause == "illness":
        docs.append(
            {"label": "a medical certificate", "any_of": ["medical_certificate"]}
        )
    return docs


def cancellation_between_incident_and_departure(claim: Claim) -> Signal | None:
    cancelled = find_field(claim, "cancellation_date")
    departure = find_field(claim, "departure_date")
    c, d = as_date(cancelled), as_date(departure)
    if claim.incident_date is None or c is None or d is None:
        return None
    if claim.incident_date <= c <= d:
        return None
    return signal(
        "C002",
        f"Cancelled on {c}; it must be between the incident ({claim.incident_date}) "
        f"and the departure ({d}).",
        sources(cancelled, departure),
    )


def amount_is_price_minus_refund(claim: Claim) -> Signal | None:
    price_f = find_field(claim, "trip_price")
    refund_f = find_field(claim, "refund_received")
    price, refund = as_number(price_f), as_number(refund_f)
    if claim.claimed_amount is None or price is None or refund is None:
        return None
    if abs(claim.claimed_amount - (price - refund)) < 0.005:
        return None
    return signal(
        "C003",
        f"Claimed CHF {claim.claimed_amount:g}; the documents give CHF {price:g} "
        f"- CHF {refund:g} = CHF {price - refund:g}.",
        sources(price_f, refund_f),
    )


WORKFLOW = Workflow(
    claim_type="travel",
    required_documents=required_documents,
    straight_through_limit=2500,
    causes=(
        ("illness", r"\b(ill|sick|illness)\b"),
        ("luggage", r"\b(luggage|baggage)\b"),
    ),
    default_cause="cancellation",
    rules=[
        Rule("C002", cancellation_between_incident_and_departure),
        Rule("C003", amount_is_price_minus_refund),
    ],
)
