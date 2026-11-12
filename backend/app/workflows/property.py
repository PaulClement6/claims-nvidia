"""Workflow A: property / household (docs/WORKFLOWS.md).

The rule "police report if the cause is theft" is a required document, so R001
covers it. The rule "photo shows the declared damage" (C004) needs a vision model:
it is out of scope.
"""

from app.workflows.engine import RequiredDocument, Workflow


def required_documents(cause: str | None) -> list[RequiredDocument]:
    docs: list[RequiredDocument] = [
        {
            "label": "a repair invoice or a repair quote",
            "any_of": ["repair_invoice", "repair_quote"],
        },
        {"label": "a damage photo", "any_of": ["damage_photo"]},
    ]
    if cause == "theft":
        docs.append({"label": "a police report", "any_of": ["police_report"]})
    return docs


WORKFLOW = Workflow(
    claim_type="property",
    required_documents=required_documents,
    straight_through_limit=2000,
    causes=(
        ("water", r"\b(water|leak\w*|flood\w*)\b"),
        ("fire", r"\b(fire|smoke)\b"),
        ("theft", r"\b(theft|stolen|burglary)\b"),
        ("breakage", r"\b(broke|broken|cracked)\b"),
    ),
)
