"""Workflow B: health / accident (docs/WORKFLOWS.md).

The own rules are signals of `checks/consistency.py`, not rules here, so that each
code has one place: "patient is the insured person" is C001 and "treatment dates are
on or after the incident date" is C002 (the consistency module gives both). Medical
text must never reach the application logs: this package logs nothing.
"""

from app.workflows.engine import RequiredDocument, Workflow


def required_documents(cause: str | None) -> list[RequiredDocument]:
    return [
        {"label": "a medical invoice", "any_of": ["medical_invoice"]},
        {
            "label": "a medical report or a medical certificate",
            "any_of": ["medical_report", "medical_certificate"],
        },
    ]


WORKFLOW = Workflow(
    claim_type="health",
    required_documents=required_documents,
    straight_through_limit=1000,
    causes=(
        (
            "accident",
            r"\b(accident|fell|fall|slipped|sprain\w*|twisted|injur\w*|crash\w*)\b",
        ),
        ("illness", r"\b(ill|sick|illness|disease|flu|infection)\b"),
    ),
)
