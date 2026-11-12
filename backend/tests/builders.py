"""Claims built in memory for the rule, routing and view tests."""

from datetime import date
from typing import Any

from app.models import (
    Claim,
    Customer,
    Document,
    ExtractedField,
    Policy,
    Signal,
    Source,
)

TRAVEL_POLICY = Policy(
    number="TRV-2026-0417",
    customer_id="C-1001",
    line_of_business="Travel",
    start=date(2026, 1, 1),
    end=date(2026, 12, 31),
    coverage_limit=5000,
)


def document(
    doc_id: str, doc_type: str, fields: dict[str, Any], confidence: float = 1.0
) -> Document:
    return Document(
        id=doc_id,
        file=f"{doc_id}.pdf",
        document_type=doc_type,
        fields=[
            ExtractedField(
                name=name,
                value=value,
                confidence=confidence,
                source=Source(document_id=doc_id, page=1, snippet=str(value)),
            )
            for name, value in fields.items()
        ],
    )


def travel_documents() -> list[Document]:
    """Claim file 1."""
    return [
        document(
            "D-book",
            "booking_confirmation",
            {"departure_date": "2026-08-10", "trip_price": 1840.0},
        ),
        document(
            "D-cancel",
            "cancellation_confirmation",
            {"cancellation_date": "2026-08-04", "refund_received": 340.0},
        ),
        document("D-med", "medical_certificate", {"patient_name": "Anna Keller"}),
    ]


def travel_claim(**changes: Any) -> Claim:
    base: dict[str, Any] = {
        "id": "CL-test",
        "customer": Customer(id="C-1001", name="Anna Keller"),
        "policy": TRAVEL_POLICY,
        "type": "travel",
        "incident_date": date(2026, 8, 3),
        "claimed_amount": 1500.0,
        "cause": "illness",
        "documents": travel_documents(),
    }
    return Claim.model_validate({**base, **changes})


def signal(code: str, points: int = 10) -> Signal:
    return Signal(
        code=code,
        category="rule",
        severity="low",
        points=points,
        title=code,
        detail=code,
    )
