"""The rules that judge the comparison of issue #126: one call per page (the control)
against one call per document (the candidate), 3 runs each, on the set MP1.

The official rules are those of issue #20 (`docs/EVALUATION.md`, "How a variant was
judged"), as the analysis of #20 computed them. Nothing here changes a rule. Where
a rule names a number of the 35-document kit, the mapping to MP1 is said in the
rule (`mapping`), and it waits for Paul's confirmation.

Three things are kept apart in the result:

- `procedure`: was the experiment valid? If not, there is no verdict (the rule of
  #20: a run that is not complete, a call with no answer after the retries, a
  check of integrity that fails).
- `official`: does the candidate meet every rule of #20? Only this gives the
  `decision`. A candidate that fails one rule does not replace the control.
- `supplementary` and `proposed_gates`: the gain rule with a threshold of 1 and of
  2 fields, and safety gates from issue #125 that nobody approved yet. They are
  reported. They never change the `decision`.

This module decides nothing about the product: it changes no default. It is a pure
function of the run reports of the harness and of the request counts.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any

from app.docintel import labels
from app.fraud.routing import LOW_CONFIDENCE

RUNS = 3  # per scope
GAIN = 3  # rule 1 of #20: right fields per run, on average
SENSITIVITY = (1, 2)  # other thresholds of rule 1: supplementary, never the verdict
SECONDS_FACTOR = Fraction(3, 2)  # the seconds per document against the control
READING_LOSS = 1  # right fields of the reading group, per run
CLASSIFICATION_LOSS = 1  # documents, per run
GUARDED = ("amount", "date", "identifier")  # "no wrong amount, date or identifier"
# The groups of #20, by type and field. The other fields are the reading group.
BUSINESS = frozenset(
    {
        ("medical_certificate", "cause"),
        ("police_report", "offence"),
        ("medical_report", "diagnosis"),
        ("medical_report", "provider"),
        ("medical_invoice", "provider"),
    }
)
NOT_READ = frozenset({("damage_photo", "damage_description")})
TEXT_KIND = "clean"  # the kind of a PDF with a text layer in a report
NO_VERDICT = "no verdict"
CONTROL_STAYS = "the control stays"
CANDIDATE_MEETS = "the candidate meets every official rule"


@dataclass(frozen=True)
class Run:
    """The facts of one run that the rules read. `calls_without_answer` and
    `unusable_answers` come from the record of the requests; the rest from the run
    report of the harness."""

    complete: bool  # the report is complete and holds every planned document
    right: int
    wrong: int
    missing: int
    guarded_wrong: int  # wrong amounts, dates and identifiers
    reading_right: int
    classified_right: int
    text_lines_right: int
    text_lines_expected: int
    reader_errors: int
    calls_without_answer: int  # logical calls with no 200 after the retries
    unusable_answers: int  # extraction answers (200) that are cut or not in the form
    seconds: float | None  # mean per document
    right_by_document: Mapping[str, int] = field(default_factory=dict)
    # what the proposed gates read; None: a stored value had no confidence
    incorrect_trusted: int | None = 0
    incorrect_trusted_absent: int | None = 0
    unaccepted_page_trusted: int | None = 0
    placeholders: int = 0
    pages: Mapping[str, tuple[int, int]] = field(default_factory=dict)
    trusted: frozenset[tuple[str, str]] = frozenset()  # right at LOW_CONFIDENCE or more


def facts(
    run: dict[str, Any],
    calls_without_answer: int = 0,
    unusable_answers: int = 0,
    documents: int | None = None,
) -> Run:
    """The facts of one run of a harness report (`run_details[i]`). `documents` is
    the number of documents of the plan: a run with another number is not
    complete."""
    docs = run["documents"]
    rows = [(d, r) for d in docs for r in d["fields"]]
    stored = [(d, r) for d, r in rows if r["read"] is not None]
    extras = [x for d in docs for x in d["extra"]]
    known = all(_is_number(r["confidence"]) for _, r in stored) and all(
        _is_number(x["confidence"]) for x in extras
    )

    def trusted(row: dict[str, Any]) -> bool:
        return bool(row["confidence"] >= LOW_CONFIDENCE)

    def accepted(row: dict[str, Any]) -> bool:
        return bool(row.get("page_accepted", row["page_right"]))

    wrong = [(d, r) for d, r in stored if r["status"] == "wrong"]
    pages: dict[str, tuple[int, int]] = {}
    for d, r in stored:
        on, of = pages.get(d["kind"], (0, 0))
        pages[d["kind"]] = (on + accepted(r), of + 1)
    text = [d["line_items"] for d in docs if d["kind"] == TEXT_KIND]
    overall = run["overall"]
    return Run(
        complete=bool(docs) and documents in (None, len(docs)),
        right=overall["fields"]["right"],
        wrong=overall["fields"]["wrong"],
        missing=overall["fields"]["missing"],
        guarded_wrong=sum(
            r["status"] == "wrong" and r["kind"] in GUARDED for _, r in rows
        ),
        reading_right=sum(
            r["status"] == "right"
            and (d["document_type"], r["name"]) not in BUSINESS | NOT_READ
            for d, r in rows
        ),
        classified_right=overall["documents"]["classified_right"],
        text_lines_right=sum(x["right"] for x in text),
        text_lines_expected=sum(x["expected"] for x in text),
        reader_errors=overall["documents"]["errors"],
        calls_without_answer=calls_without_answer,
        unusable_answers=unusable_answers,
        seconds=overall["seconds"]["mean"],
        right_by_document={d["id"]: d["counts"]["right"] for d in docs},
        incorrect_trusted=sum(trusted(r) for _, r in wrong) if known else None,
        incorrect_trusted_absent=(
            sum(trusted(r) and r["expected"] is None for _, r in wrong)
            if known
            else None
        ),
        unaccepted_page_trusted=(
            sum(trusted(r) and not accepted(r) for _, r in stored) if known else None
        ),
        placeholders=sum(labels.is_placeholder(r["read"]) for _, r in stored)
        + sum(labels.is_placeholder(x["value"]) for x in extras),
        pages=pages,
        trusted=frozenset(
            (d["id"], r["name"])
            for d, r in stored
            if known and r["status"] == "right" and trusted(r)
        ),
    )


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _mean(values: Sequence[int]) -> Fraction:
    return Fraction(sum(values), len(values))


def _show(value: Fraction | float | None) -> float | None:
    return None if value is None else round(float(value), 4)


def _rule(
    number: str, text: str, met: bool, control: Any, candidate: Any, mapping: str = ""
) -> dict[str, Any]:
    return {
        "rule": number,
        "text": text,
        "met": bool(met),
        "control": control,
        "candidate": candidate,
        "mapping": mapping or "as written",
    }


def judge(
    control: Sequence[Run], candidate: Sequence[Run], integrity: Sequence[str] = ()
) -> dict[str, Any]:
    """The verdict on the candidate against the control. `integrity` lists the
    checks of integrity that failed (code, set, settings, model, request count,
    replay): one entry is enough for "no verdict"."""
    problems = list(integrity)
    for name, runs in (("control", control), ("candidate", candidate)):
        if len(runs) != RUNS:
            problems.append(f"the {name} has {len(runs)} runs, not {RUNS}")
        for n, run in enumerate(runs, 1):
            if not run.complete:
                problems.append(f"run {n} of the {name} is not complete")
            if run.calls_without_answer:
                problems.append(
                    f"run {n} of the {name}: {run.calls_without_answer} call(s) got "
                    "no answer after the retries"
                )
    if problems or not control or not candidate:
        return {
            "procedure": {"valid": False, "problems": problems},
            "official": {"met": None, "rules": []},
            "supplementary": {},
            "proposed_gates": [],
            "decision": NO_VERDICT,
        }

    gain = _mean([r.right for r in candidate]) - _mean([r.right for r in control])
    others = _other_rules(control, candidate, gain)
    first = _rule(
        "1",
        f"at least {GAIN} more right fields per run (means of the {RUNS} runs)",
        gain >= GAIN,
        _show(_mean([r.right for r in control])),
        _show(_mean([r.right for r in candidate])),
    )
    rules = [first, *others]
    met = all(r["met"] for r in rules)
    return {
        "procedure": {"valid": True, "problems": []},
        "official": {"met": met, "gain": _show(gain), "rules": rules},
        "supplementary": {
            "note": "Other thresholds of rule 1. They are not the official rule and "
            "are never a reason to change the default.",
            "thresholds": {
                str(t): {
                    "rule_1_met": bool(gain >= t),
                    "every_rule_met": bool(gain >= t and all(r["met"] for r in others)),
                }
                for t in SENSITIVITY
            },
        },
        "proposed_gates": _gates(control, candidate),
        "decision": CANDIDATE_MEETS if met else CONTROL_STAYS,
    }


def _other_rules(
    control: Sequence[Run], candidate: Sequence[Run], gain: Fraction
) -> list[dict[str, Any]]:
    """Rules 2 to 4 of #20, in the order of the text."""

    def mean(runs: Sequence[Run], name: str) -> Fraction:
        return _mean([getattr(r, name) for r in runs])

    lowest, highest = min(r.right for r in candidate), max(r.right for r in control)
    guarded = sum(r.guarded_wrong for r in candidate)
    lines = [(r.text_lines_right, r.text_lines_expected) for r in candidate]
    seconds = [[r.seconds for r in runs] for runs in (control, candidate)]
    timed = all(s is not None for s in seconds[0] + seconds[1])
    slow = fast = None
    if timed:
        fast = sum(Fraction(str(s)) for s in seconds[0]) / len(seconds[0])
        slow = sum(Fraction(str(s)) for s in seconds[1]) / len(seconds[1])
    contribution = {
        doc: _mean([r.right_by_document.get(doc, 0) for r in candidate])
        - _mean([r.right_by_document.get(doc, 0) for r in control])
        for doc in sorted(
            {d for r in (*control, *candidate) for d in r.right_by_document}
        )
    }
    left_out = {doc: gain - part for doc, part in contribution.items()}
    unreliable = [
        (r.reader_errors, r.calls_without_answer, r.unusable_answers) for r in candidate
    ]
    return [
        _rule(
            "2",
            "its lowest run is higher than the highest run of the control",
            lowest > highest,
            highest,
            lowest,
        ),
        _rule(
            "3a",
            "no wrong amount, date or identifier",
            guarded == 0,
            sum(r.guarded_wrong for r in control),
            guarded,
        ),
        _rule(
            "3b",
            "no more wrong values than the control",
            mean(candidate, "wrong") <= mean(control, "wrong"),
            _show(mean(control, "wrong")),
            _show(mean(candidate, "wrong")),
        ),
        _rule(
            "3c",
            f"the reading group loses {READING_LOSS} right field per run at most",
            mean(candidate, "reading_right")
            >= mean(control, "reading_right") - READING_LOSS,
            _show(mean(control, "reading_right")),
            _show(mean(candidate, "reading_right")),
            "MP1: the groups of #20 by type and field; 31 reading fields, 4 "
            "business-category fields, no damage photo",
        ),
        _rule(
            "3d",
            f"classification loses {CLASSIFICATION_LOSS} document per run at most",
            mean(candidate, "classified_right")
            >= mean(control, "classified_right") - CLASSIFICATION_LOSS,
            _show(mean(control, "classified_right")),
            _show(mean(candidate, "classified_right")),
        ),
        _rule(
            "3e",
            "every line item of the clean PDFs stays right",
            all(right == expected for right, expected in lines),
            [r.text_lines_right for r in control],
            [right for right, _ in lines],
            "MP1: the 35 line items of the 6 text PDFs (the kit had 53)",
        ),
        _rule(
            "3f",
            "no reader error, and no call left without an answer",
            all(x == (0, 0, 0) for x in unreliable),
            [
                (r.reader_errors, r.calls_without_answer, r.unusable_answers)
                for r in control
            ],
            unreliable,
            "an extraction answer that is cut or not in the asked form counts as "
            "a call left without an answer (the strict reading)",
        ),
        _rule(
            "3g",
            "at most 1.5 times the seconds per document of the control, or Paul "
            "accepts the cost",
            bool(
                timed
                and slow is not None
                and fast is not None
                and slow <= SECONDS_FACTOR * fast
            ),
            _show(fast),
            _show(slow),
        ),
        _rule(
            "4",
            "the gain stays above 0 when any one scenario is left out",
            bool(left_out) and all(g > 0 for g in left_out.values()),
            None,
            {doc: _show(g) for doc, g in left_out.items()},
            "MP1 has no scenario: any one document is left out",
        ),
    ]


def _gates(control: Sequence[Run], candidate: Sequence[Run]) -> list[dict[str, Any]]:
    """Safety gates from issue #125, proposed for this comparison and not approved.
    `met` is None when a count is not available."""

    def gate(
        number: str, text: str, met: bool | None, a: Any, b: Any
    ) -> dict[str, Any]:
        return {"gate": number, "text": text, "met": met, "control": a, "candidate": b}

    def counts(runs: Sequence[Run], name: str) -> list[int | None]:
        return [getattr(r, name) for r in runs]

    def whole(values: list[int | None]) -> list[int] | None:
        return (
            None
            if any(v is None for v in values)
            else [v for v in values if v is not None]
        )

    wrong_c, wrong_d = (
        counts(control, "incorrect_trusted"),
        counts(candidate, "incorrect_trusted"),
    )
    absent = counts(candidate, "incorrect_trusted_absent")
    page = counts(candidate, "unaccepted_page_trusted")
    shares = {}
    for kind in sorted({k for r in (*control, *candidate) for k in r.pages}):
        pair = []
        for runs in (control, candidate):
            on = sum(r.pages.get(kind, (0, 0))[0] for r in runs)
            of = sum(r.pages.get(kind, (0, 0))[1] for r in runs)
            pair.append(Fraction(on, of) if of else None)
        shares[kind] = pair
    kept = frozenset.intersection(*(r.trusted for r in control))
    lost = sorted(kept - frozenset.intersection(*(r.trusted for r in candidate)))
    a, b, c, d = whole(wrong_c), whole(wrong_d), whole(absent), whole(page)
    return [
        gate(
            "1",
            "no run of the candidate has more wrong values at 0.8 or more than the "
            "highest run of the control",
            None if a is None or b is None else max(b) <= max(a),
            wrong_c,
            wrong_d,
        ),
        gate(
            "2",
            "no value at 0.8 or more for a field that the document does not hold",
            None if c is None else max(c) == 0,
            counts(control, "incorrect_trusted_absent"),
            absent,
        ),
        gate(
            "3",
            "no value at 0.8 or more on a page that is not an accepted page",
            None if d is None else max(d) == 0,
            counts(control, "unaccepted_page_trusted"),
            page,
        ),
        gate(
            "4",
            "the share of stored values on an accepted page is not below the "
            "control, for the text PDFs and for the scans apart",
            all(
                c_share is None or (d_share is not None and d_share >= c_share)
                for c_share, d_share in shares.values()
            ),
            {k: _show(v[0]) for k, v in shares.items()},
            {k: _show(v[1]) for k, v in shares.items()},
        ),
        gate(
            "5",
            "no placeholder is stored",
            all(r.placeholders == 0 for r in candidate),
            [r.placeholders for r in control],
            [r.placeholders for r in candidate],
        ),
        gate(
            "reported",
            "fields that are right at 0.8 or more in every run of the control and "
            "not in every run of the candidate",
            None,
            len(kept),
            [f"{doc}: {name}" for doc, name in lost],
        ),
    ]


def text(verdict: dict[str, Any]) -> str:
    """The verdict as a few lines of markdown."""
    procedure, official = verdict["procedure"], verdict["official"]
    lines = [f"**Decision: {verdict['decision']}.**", ""]
    if not procedure["valid"]:
        lines += [
            "The procedure is not valid:",
            *[f"- {p}" for p in procedure["problems"]],
        ]
        return "\n".join(lines) + "\n"
    lines += [
        "The procedure is valid. Official rules of #20 "
        f"(gain {official['gain']:+.2f} right fields per run):",
        "",
        "| Rule | Text | Met | Control | Candidate | Mapping |",
        "|---|---|---|---|---|---|",
    ]
    for r in official["rules"]:
        met = "yes" if r["met"] else "**no**"
        lines.append(
            f"| {r['rule']} | {r['text']} | {met} | {r['control']} | {r['candidate']} "
            f"| {r['mapping']} |"
        )
    lines += [
        "",
        "Supplementary (never the verdict): " + verdict["supplementary"]["note"],
        "",
    ]
    for threshold, result in verdict["supplementary"]["thresholds"].items():
        lines.append(
            f"- threshold {threshold}: rule 1 met {result['rule_1_met']}, "
            f"every rule met {result['every_rule_met']}"
        )
    lines += ["", "Proposed gates (not approved, never the verdict):", ""]
    for g in verdict["proposed_gates"]:
        met = "not available" if g["met"] is None else "yes" if g["met"] else "no"
        if g["gate"] == "reported":
            met = "reported"
        lines.append(
            f"- {g['gate']}: {g['text']}: {met} (control {g['control']}, "
            f"candidate {g['candidate']})"
        )
    return "\n".join(lines) + "\n"
