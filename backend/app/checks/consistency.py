"""Signals C001, C002 and C003: do the documents fit the claim?

One place per signal code. This module gives C001, C002 and C003 for every claim,
except the codes the workflow of the claim gives itself: the travel workflow owns
C002 and C003 (its own rules, `workflows/travel.py`), so this module skips them for
travel. The health rules of docs/WORKFLOWS.md B live here: "patient is the insured
person" is C001 and "treatment dates are on or after the incident date" is C002.

Deterministic code in `local` mode; C004 (photo against the damage) needs a model.
Each signal carries the sources of the fields it used.
"""

import re

from app.checks.rules import as_date, as_number, signal, sources
from app.models import Claim, ExtractedField, Signal
from app.workflows.engine import Workflow

NAME_FIELDS = ("patient_name", "customer_name", "traveller_name")
TREATMENT_FIELD = "treatment_date"
AMOUNT_TOLERANCE = 0.005  # CHF


def _norm(name: object) -> str:
    return re.sub(r"\s+", " ", str(name)).strip().casefold()


def _fields(claim: Claim, *names: str) -> list[ExtractedField]:
    return [f for d in claim.documents for f in d.fields if f.name in names]


def c001(claim: Claim) -> Signal | None:
    wrong = [
        f
        for f in _fields(claim, *NAME_FIELDS)
        if _norm(f.value) != _norm(claim.customer.name)
    ]
    if not wrong:
        return None
    names = ", ".join(sorted({str(f.value) for f in wrong}))
    return signal(
        "C001",
        f"A document names {names}; the insured person is {claim.customer.name}.",
        sources(*wrong),
    )


def c002(claim: Claim) -> Signal | None:
    if claim.incident_date is None:
        return None
    early = [
        f
        for f in _fields(claim, TREATMENT_FIELD)
        if (d := as_date(f)) is not None and d < claim.incident_date
    ]
    if not early:
        return None
    dates = ", ".join(sorted(str(f.value) for f in early))
    return signal(
        "C002",
        f"Treatment on {dates}, before the incident on {claim.incident_date}.",
        sources(*early),
    )


def c003(claim: Claim) -> Signal | None:
    """The claimed amount against the first document total. ponytail: the first one,
    not a sum, so a quote and its invoice do not count twice."""
    total = next(iter(_fields(claim, "total")), None)
    documented = as_number(total)
    if claim.claimed_amount is None or documented is None:
        return None
    if abs(claim.claimed_amount - documented) < AMOUNT_TOLERANCE:
        return None
    return signal(
        "C003",
        f"Claimed CHF {claim.claimed_amount:g}; the documents give CHF {documented:g}.",
        sources(total),
    )


CHECKS = {"C001": c001, "C002": c002, "C003": c003}


def check_consistency(claim: Claim, workflow: Workflow | None) -> list[Signal]:
    owned = {r.code for r in workflow.rules} if workflow else set()
    found = (check(claim) for code, check in CHECKS.items() if code not in owned)
    return [s for s in found if s is not None]
