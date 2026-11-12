"""A workflow is declarative data. The engine reads it; it holds no claim logic."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypedDict

from app.checks.rules import Rule
from app.models import Claim


class RequiredDocument(TypedDict):
    label: str
    any_of: list[str]  # any one of these document types satisfies the entry


@dataclass(frozen=True)
class Workflow:
    claim_type: str
    required_documents: Callable[[str | None], list[RequiredDocument]]  # by cause
    straight_through_limit: float
    rules: list[Rule] = field(default_factory=list)  # its own rules, after the shared


def missing_documents(workflow: Workflow, claim: Claim) -> list[str]:
    have = {d.document_type for d in claim.documents}
    return [
        r["label"]
        for r in workflow.required_documents(claim.cause)
        if not have & set(r["any_of"])
    ]
