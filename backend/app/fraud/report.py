"""The fraud report of docs/FRAUD.md, as a template.

Built from the signals only: it adds no fact. Every text comes from a signal (what
it saw, its sources, what it expected) or from a fixed table of this file. This
template runs in every mode. Issue #26 may have an LLM write the same fields from
the same signals.

The handler must read it in one minute: one sentence that says why, the findings
(most points first), what was not checked, and the next steps.
"""

from app.checks.rules import SIGNALS
from app.models import Claim, FraudFinding, FraudReport, Outcome, Signal

# What this version does not do (docs/FRAUD.md, "Later"). Fixed and honest.
NOT_CHECKED = [
    "The content of the photos against the declared damage.",
    "Duplicate invoices across claims.",
    "Handwriting and stamps.",
    "The identity of the provider.",
]

# One clause per code for the summary. {doc} is the kind of the first document that
# holds the evidence ("medical invoice"), or "document" when no document does. A
# clause with a second form uses it when the clause before spoke of the same kind.
WHY: dict[str, str | tuple[str, str]] = {
    "R001": "a required document is missing",
    "R002": "the incident is outside the policy period",
    "R003": "the incident is within 14 days of the policy start",
    "R004": "the claimed amount is almost the whole coverage limit",
    "R005": "an invoice is dated before the incident",
    "T001": "the {doc} was edited after it was issued",
    "T002": (
        "the {doc} was changed long after it was created",
        "it was changed long after it was created",
    ),
    "T003": (
        "the total of the {doc} does not match its lines",
        "its total does not match its lines",
    ),
    "C001": (
        "a name on the {doc} is not the insured person",
        "a name on it is not the insured person",
    ),
    "C002": "the dates of the documents do not fit the incident",
    "C003": "the claimed amount differs from the documented amount",
    "C004": "a photo or report does not fit the declared damage",
}

# Next steps by category, in the order a handler works through them.
NEXT_STEPS = {
    "tampering": "Ask the provider for the original invoice.",
    "consistency": "Compare the names, dates and amounts on the documents with "
    "the story of the customer.",
    "rule": "Check the policy period and the coverage limit against the claim.",
}
CATEGORY_ORDER = list(NEXT_STEPS)


def _rank(s: Signal) -> tuple[int, str]:
    return CATEGORY_ORDER.index(s.category), s.code


def _doc_name(claim: Claim, document_id: str) -> str:
    doc = next((d for d in claim.documents if d.id == document_id), None)
    if doc is None:
        return document_id
    return str(doc.file_metadata.get("original_name") or doc.file)


def _where(claim: Claim, s: Signal) -> list[str]:
    if not s.evidence:
        return ["No document holds it: it comes from the claim details and the policy."]
    return [f"{_doc_name(claim, e.document_id)}, page {e.page}" for e in s.evidence]


def _kind(claim: Claim, s: Signal) -> str:
    if s.evidence:
        doc = next(
            (d for d in claim.documents if d.id == s.evidence[0].document_id), None
        )
        if doc is not None and doc.document_type != "unknown":
            return doc.document_type.replace("_", " ")
    return "document"


def _summary(claim: Claim, signals: list[Signal]) -> str:
    """The three highest-point signals; a tie goes to tampering, then consistency."""
    top = sorted(signals, key=lambda s: (-s.points, *_rank(s)))[:3]
    clauses: list[str] = []
    last = ""
    for s in sorted(top, key=_rank):
        kind = _kind(claim, s)
        why = WHY[s.code]
        first, again = (why, why) if isinstance(why, str) else why
        clauses.append((again if kind == last else first).format(doc=kind))
        last = kind
    if not clauses:
        return "No signal."
    text = (
        clauses[0]
        if len(clauses) == 1
        else " and ".join(clauses)
        if len(clauses) == 2
        else f"{clauses[0]}, {clauses[1]}, and {clauses[2]}"
    )
    return text[0].upper() + text[1:] + "."


def write_report(
    claim: Claim, signals: list[Signal], outcome: Outcome, score: int
) -> FraudReport:
    ordered = sorted(signals, key=lambda s: (-s.points, *_rank(s)))
    return FraudReport(
        outcome=outcome,
        score=score,
        summary=_summary(claim, signals),
        findings=[
            FraudFinding(
                code=s.code,
                title=s.title,
                saw=s.detail,
                where=_where(claim, s),
                expected=s.expected or SIGNALS[s.code].expected,
            )
            for s in ordered
        ],
        not_checked=list(NOT_CHECKED),
        next_steps=[
            NEXT_STEPS[c]
            for c in CATEGORY_ORDER
            if any(s.category == c for s in signals)
        ],
    )
