"""Non-negotiable 1: the system never rejects a claim."""

import ast
import random
import re
from pathlib import Path

import pytest

import app
from app.agent import phrases
from app.checks.rules import SIGNALS
from app.fraud.routing import route
from app.models import Outcome, Signal
from app.workflows import WORKFLOWS
from tests.builders import document, travel_claim, travel_documents

OUTCOMES = {"straight_through", "handler_review", "investigation"}


def test_outcome_has_exactly_three_values() -> None:
    assert {o.value for o in Outcome} == OUTCOMES


def random_signals(rng: random.Random) -> list[Signal]:
    out = []
    for code in rng.sample(sorted(SIGNALS), rng.randint(0, len(SIGNALS))):
        s = SIGNALS[code]
        out.append(
            Signal(
                code=code,
                category=s.category,
                severity=s.severity,
                points=rng.choice([s.points, 0, 1000, rng.randint(0, 200)]),
                title=s.title,
                detail="x",
            )
        )
    return out


def test_whatever_the_signals_and_the_amount_the_outcome_is_one_of_three() -> None:
    rng = random.Random(20261112)
    seen: set[Outcome] = set()
    for _ in range(2000):
        docs = travel_documents()
        if rng.random() < 0.2:
            docs[0] = document(
                "D-book", "booking_confirmation", {"trip_price": 1.0}, rng.random()
            )
        claim = travel_claim(
            claimed_amount=rng.choice(
                [None, 0, 1, 2500, 2501, 1e9, rng.random() * 1e5]
            ),
            documents=docs,
        )
        workflow = rng.choice([WORKFLOWS["travel"], None])
        outcome, reasons = route(claim, random_signals(rng), workflow)
        assert outcome in Outcome
        assert outcome.value in OUTCOMES
        assert isinstance(reasons, list)
        seen.add(outcome)
    assert seen == set(Outcome)  # the generator reaches all three


FORBIDDEN = re.compile(r"\b(reject\w*|den(y|ied|ial)|declin\w*)\b", re.I)
# The sandbox modules say "denied" of a network request that the policy of a sandbox
# refused (issue #15: `Denied: GET example.com:443`). That is not a claim outcome: they
# still may not say reject or decline.
SANDBOX_FILES_NAMES = {"sandbox.py", "sandbox_panel.py"}
SANDBOX_FILES = re.compile(r"\b(reject\w*|declin\w*)\b", re.I)


def string_literals(tree: ast.AST) -> list[str]:
    """String constants of a module, docstrings left out."""
    docstrings = {
        id(n.body[0].value)
        for n in ast.walk(tree)
        if isinstance(
            n, ast.Module | ast.FunctionDef | ast.ClassDef | ast.AsyncFunctionDef
        )
        and n.body
        and isinstance(n.body[0], ast.Expr)
        and isinstance(n.body[0].value, ast.Constant)
    }
    return [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, str)
        and id(n) not in docstrings
    ]


@pytest.mark.parametrize(
    "path", sorted(Path(app.__file__).parent.rglob("*.py")), ids=lambda p: p.name
)
def test_the_backend_source_has_no_rejection_outcome(path: Path) -> None:
    # the output filter of the agent lists these words to block them
    forbidden = SANDBOX_FILES if path.name in SANDBOX_FILES_NAMES else FORBIDDEN
    found = [
        s
        for s in string_literals(ast.parse(path.read_text()))
        if forbidden.search(s) and s != phrases.FORBIDDEN.pattern
    ]
    assert found == []
