"""Rules: pure functions `(claim) -> Signal | None`, plus the table of signals.

A rule reads the claim and the extracted fields of its documents. When a field it
needs is absent it gives no signal: the missing document is R001's job. Every
signal carries the sources of the fields it used.

To tune the points, edit SIGNALS. A workflow adds its own rules in its own file.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Literal

from app.models import Claim, ExtractedField, Signal, Source

if TYPE_CHECKING:
    from app.workflows.engine import Workflow

Category = Literal["rule", "tampering", "consistency"]
Severity = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class SignalKind:
    category: Category
    severity: Severity
    points: int
    title: str
    expected: str  # what a good claim shows; the fraud report prints it


# docs/FRAUD.md. One table: code -> category, severity, points, title, expected.
SIGNALS: dict[str, SignalKind] = {
    "R001": SignalKind(
        "rule",
        "medium",
        25,
        "A required document is missing",
        "Every document the workflow requires is uploaded.",
    ),
    "R002": SignalKind(
        "rule",
        "high",
        40,
        "Incident date outside the policy period",
        "The incident date is inside the policy period.",
    ),
    "R003": SignalKind(
        "rule",
        "medium",
        20,
        "Incident within 14 days of the policy start",
        "An incident more than 14 days after the policy start.",
    ),
    "R004": SignalKind(
        "rule",
        "low",
        10,
        "Claimed amount at 95% or more of the coverage limit",
        "A claimed amount below 95% of the coverage limit.",
    ),
    "R005": SignalKind(
        "rule",
        "medium",
        20,
        "Invoice dated before the incident",
        "An invoice dated on or after the incident.",
    ),
    "T001": SignalKind(
        "tampering",
        "medium",
        20,
        "PDF last written by an editing tool",
        "A PDF written by the tool of its issuer, not by a PDF editor.",
    ),
    "T002": SignalKind(
        "tampering",
        "low",
        10,
        "PDF modified more than one day after its creation",
        "A modification date within one day of the creation date.",
    ),
    "T003": SignalKind(
        "tampering",
        "high",
        30,
        "Line items do not add up to the total",
        "Line items that add up to the total (tolerance CHF 0.05).",
    ),
    "C001": SignalKind(
        "consistency",
        "medium",
        20,
        "Name on a document is not the insured person",
        "Every name on the documents is the insured person.",
    ),
    "C002": SignalKind(
        "consistency",
        "medium",
        15,
        "Dates of the documents do not fit the declared incident",
        "Document dates that fit the declared incident date.",
    ),
    "C003": SignalKind(
        "consistency",
        "medium",
        15,
        "Claimed amount differs from the documented amount",
        "A claimed amount equal to the documented amount.",
    ),
    "C004": SignalKind(
        "consistency",
        "medium",
        20,
        "Photo or report does not fit the declared damage (VLM)",
        "A photo or report that fits the declared damage.",
    ),
}


def signal(code: str, detail: str, evidence: list[Source] | None = None) -> Signal:
    kind = SIGNALS[code]
    return Signal(
        code=code,
        category=kind.category,
        severity=kind.severity,
        points=kind.points,
        title=kind.title,
        detail=detail,
        expected=kind.expected,
        evidence=evidence or [],
    )


@dataclass(frozen=True)
class Rule:
    code: str
    check: Callable[[Claim], Signal | None]


def find_field(
    claim: Claim, name: str, document_type: str | None = None
) -> ExtractedField | None:
    """The first field with this name, in the documents of the claim."""
    for doc in claim.documents:
        if document_type in (None, doc.document_type):
            for f in doc.fields:
                if f.name == name:
                    return f
    return None


def as_date(f: ExtractedField | None) -> date | None:
    if f is None:
        return None
    try:
        return date.fromisoformat(str(f.value))
    except ValueError:
        return None


def as_number(f: ExtractedField | None) -> float | None:
    if f is None:
        return None
    try:
        return float(f.value)
    except (TypeError, ValueError):
        return None


def sources(*fields: ExtractedField | None) -> list[Source]:
    return [f.source for f in fields if f is not None]


def _workflow(claim: Claim) -> "Workflow | None":
    from app.workflows import WORKFLOWS  # late: the workflows import this module

    return WORKFLOWS.get(claim.type or "")


# Fields that carry the date of the incident itself (a report, a certificate).
INCIDENT_DATE_FIELDS = ("incident_date", "treatment_date", "unfit_from")


def _documents_that_say(
    claim: Claim, names: tuple[str, ...], value: object
) -> list[Source]:
    """The sources of the fields that hold the same value as the claim says: the
    documents that back a fact the customer declared in the chat. May be empty."""
    found = []
    for d in claim.documents:
        for f in d.fields:
            if f.name in names and _same(f.value, value):
                found.append(f.source)
    return found


def _same(a: object, b: object) -> bool:
    try:
        return abs(float(a) - float(b)) < 0.005  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return str(a) == str(b)


def r001(claim: Claim) -> Signal | None:
    from app.workflows.engine import missing_documents

    workflow = _workflow(claim)
    missing = missing_documents(workflow, claim) if workflow else []
    if not missing:
        return None
    return signal("R001", f"Missing: {', '.join(missing)}.")


def r002(claim: Claim) -> Signal | None:
    if claim.policy is None or claim.incident_date is None:
        return None
    p = claim.policy
    if p.start <= claim.incident_date <= p.end:
        return None
    return signal(
        "R002",
        f"Incident on {claim.incident_date}, policy {p.number} runs from "
        f"{p.start} to {p.end}.",
    )


def r003(claim: Claim) -> Signal | None:
    if claim.policy is None or claim.incident_date is None:
        return None
    days = (claim.incident_date - claim.policy.start).days
    if not 0 <= days <= 14:
        return None
    return signal(
        "R003",
        f"Incident {days} days after the start of policy {claim.policy.number} "
        f"({claim.policy.start}).",
        _documents_that_say(
            claim, INCIDENT_DATE_FIELDS, claim.incident_date.isoformat()
        ),
    )


def r004(claim: Claim) -> Signal | None:
    if claim.policy is None or claim.claimed_amount is None:
        return None
    limit = claim.policy.coverage_limit
    if claim.claimed_amount * 100 < limit * 95:
        return None
    return signal(
        "R004",
        f"Claimed CHF {claim.claimed_amount:g} against a limit of CHF {limit:g}.",
        _documents_that_say(claim, ("total",), claim.claimed_amount),
    )


def r005(claim: Claim) -> Signal | None:
    if claim.incident_date is None:
        return None
    for doc in claim.documents:
        f = next((f for f in doc.fields if f.name == "invoice_date"), None)
        d = as_date(f)
        if d is not None and d < claim.incident_date:
            return signal(
                "R005",
                f"Invoice dated {d}, before the incident on {claim.incident_date}.",
                sources(f),
            )
    return None


SHARED_RULES = [
    Rule("R001", r001),
    Rule("R002", r002),
    Rule("R003", r003),
    Rule("R004", r004),
    Rule("R005", r005),
]


def run_rules(claim: Claim, workflow: "Workflow | None") -> list[Signal]:
    """Shared rules, then the rules of the workflow (none when there is no workflow)."""
    rules = [*SHARED_RULES, *(workflow.rules if workflow else [])]
    return [s for r in rules if (s := r.check(claim)) is not None]
