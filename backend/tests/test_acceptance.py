"""The acceptance evaluator of issue #126: every way to pass, to fail and to get no
verdict. No model, no network: the runs are made by hand, or come from the harness
with a stub of the model.

The official rules are those of issue #20 (`docs/EVALUATION.md`). A threshold of 1
or 2 fields and the proposed safety gates are reported and never change the
decision: several tests here pin exactly that.
"""

from dataclasses import replace
from typing import Any

import pytest

from app.docintel import acceptance as acc
from app.docintel.acceptance import CANDIDATE_MEETS, CONTROL_STAYS, NO_VERDICT, Run
from tests.test_mp1_reader import evaluated

DOCS = [f"mp1/d{n}" for n in range(1, 9)]


def run(right: int = 30, **changes: Any) -> Run:
    """A complete, clean run with `right` right fields of 35, spread over 8
    documents, and no wrong value."""
    base, more = divmod(right, len(DOCS))
    by_document = {d: base + (i < more) for i, d in enumerate(DOCS)}
    made = Run(
        complete=True,
        right=right,
        wrong=0,
        missing=35 - right,
        guarded_wrong=0,
        reading_right=right - 4,
        classified_right=8,
        text_lines_right=35,
        text_lines_expected=35,
        reader_errors=0,
        calls_without_answer=0,
        unusable_answers=0,
        seconds=4.0,
        right_by_document=by_document,
        pages={"clean": (26, 26), "scan": (7, 7)},
    )
    return replace(made, **changes)


CONTROL = [run(30), run(30), run(30)]
CANDIDATE = [run(34), run(34), run(34)]


def unmet(verdict: dict[str, Any]) -> list[str]:
    return [r["rule"] for r in verdict["official"]["rules"] if not r["met"]]


def test_a_candidate_that_meets_every_rule_of_issue_20_passes() -> None:
    verdict = acc.judge(CONTROL, CANDIDATE)
    assert verdict["procedure"] == {"valid": True, "problems": []}
    assert verdict["official"]["met"] is True and unmet(verdict) == []
    assert verdict["official"]["gain"] == 4.0
    assert verdict["decision"] == CANDIDATE_MEETS
    rules = [r["rule"] for r in verdict["official"]["rules"]]
    assert rules == ["1", "2", "3a", "3b", "3c", "3d", "3e", "3f", "3g", "4"]


def test_the_same_runs_always_give_the_same_verdict() -> None:
    assert acc.judge(CONTROL, CANDIDATE) == acc.judge(list(CONTROL), list(CANDIDATE))


# ------------------------------------------------------------- rule 1 and rule 2


@pytest.mark.parametrize(
    ("candidate", "met"),
    [
        ([33, 33, 33], True),  # exactly 3 more
        ([32, 33, 34], True),  # 3 more on average
        ([33, 33, 32], False),  # 2.67
        ([32, 32, 32], False),
        ([30, 30, 30], False),
        ([29, 29, 29], False),  # worse than the control
    ],
)
def test_rule_1_needs_three_more_right_fields_per_run_on_average(
    candidate: list[int], met: bool
) -> None:
    verdict = acc.judge(CONTROL, [run(r) for r in candidate])
    assert ("1" not in unmet(verdict)) is met
    assert (verdict["decision"] == CANDIDATE_MEETS) is met


def test_the_mean_of_rule_1_is_exact_at_the_limit() -> None:
    """31, 32, 32 against 34, 35, 35: the gain is exactly 3, with no error of
    floating point."""
    verdict = acc.judge([run(31), run(32), run(32)], [run(34), run(35), run(35)])
    assert "1" not in unmet(verdict) and verdict["official"]["gain"] == 3.0


def test_rule_2_needs_the_lowest_run_above_the_highest_run_of_the_control() -> None:
    control = [run(27), run(27), run(33)]  # mean 29
    verdict = acc.judge(control, [run(33), run(34), run(35)])  # mean 34
    assert unmet(verdict) == ["2"] and verdict["decision"] == CONTROL_STAYS
    above = acc.judge(control, [run(34), run(34), run(34)])
    assert "2" not in unmet(above)


# ---------------------------------------------------------------- the guardrails


def test_rule_3a_one_wrong_amount_date_or_identifier_in_one_run_fails() -> None:
    candidate = [run(34), run(34, guarded_wrong=1, wrong=1, missing=0), run(34)]
    assert "3a" in unmet(acc.judge(CONTROL, candidate))
    # also when the control has more of them: the rule is about the candidate
    control = [run(30, guarded_wrong=2, wrong=2)] * 3
    assert "3a" in unmet(acc.judge(control, candidate))


def test_rule_3b_more_wrong_values_than_the_control_fails() -> None:
    control = [run(30, wrong=1)] * 3
    same = [run(34, wrong=1)] * 3
    more = [run(34, wrong=1), run(34, wrong=1), run(34, wrong=2)]
    assert "3b" not in unmet(acc.judge(control, same))
    assert unmet(acc.judge(control, more)) == ["3b"]


@pytest.mark.parametrize(
    ("reading", "met"), [([25, 25, 25], True), ([25, 25, 24], False)]
)
def test_rule_3c_the_reading_group_loses_one_right_field_per_run_at_most(
    reading: list[int], met: bool
) -> None:
    candidate = [run(34, reading_right=r) for r in reading]  # the control has 26
    assert ("3c" not in unmet(acc.judge(CONTROL, candidate))) is met


@pytest.mark.parametrize(("types", "met"), [([7, 7, 7], True), ([7, 7, 6], False)])
def test_rule_3d_classification_loses_one_document_per_run_at_most(
    types: list[int], met: bool
) -> None:
    candidate = [run(34, classified_right=t) for t in types]
    assert ("3d" not in unmet(acc.judge(CONTROL, candidate))) is met


def test_rule_3e_every_line_item_of_the_text_pdfs_must_be_right_in_every_run() -> None:
    candidate = [run(34), run(34), run(34, text_lines_right=34)]
    assert unmet(acc.judge(CONTROL, candidate)) == ["3e"]
    # the control may have lost lines: the rule is still about the candidate
    control = [run(30, text_lines_right=20)] * 3
    assert "3e" not in unmet(acc.judge(control, CANDIDATE))


def test_rule_3f_a_reader_error_fails() -> None:
    candidate = [run(34), run(34, reader_errors=1), run(34)]
    assert unmet(acc.judge(CONTROL, candidate)) == ["3f"]


def test_rule_3f_a_cut_or_unusable_answer_fails_and_the_procedure_stays_valid() -> None:
    """A document answer cut at the token limit is a 200 answer. The run is valid
    and its numbers are reported. The candidate is not reliable: the control
    stays. It is never "no verdict" and never a pass."""
    candidate = [run(34), run(34), run(34, unusable_answers=1)]
    verdict = acc.judge(CONTROL, candidate)
    assert verdict["procedure"]["valid"] is True
    assert unmet(verdict) == ["3f"] and verdict["decision"] == CONTROL_STAYS


@pytest.mark.parametrize(
    ("seconds", "met"),
    [([6.0, 6.0, 6.0], True), ([6.0, 6.0, 6.03], False), ([3.0, 3.0, 3.0], True)],
)
def test_rule_3g_at_most_one_and_a_half_times_the_seconds_of_the_control(
    seconds: list[float], met: bool
) -> None:
    candidate = [run(34, seconds=s) for s in seconds]  # the control has 4.0
    assert ("3g" not in unmet(acc.judge(CONTROL, candidate))) is met


def test_rule_3g_a_run_with_no_time_does_not_meet_the_rule() -> None:
    candidate = [run(34), run(34), run(34, seconds=None)]
    assert unmet(acc.judge(CONTROL, candidate)) == ["3g"]


def test_rule_4_a_gain_that_one_document_makes_alone_fails() -> None:
    """The control has 3 right fields in every document but the first, which has
    none. The candidate reads the first document too: +5 fields, all from one
    document. With that document left out the gain is 0."""
    lost = dict.fromkeys(DOCS, 4) | {DOCS[0]: 0, DOCS[1]: 5, DOCS[2]: 5}
    full = dict.fromkeys(DOCS, 4) | {DOCS[0]: 5, DOCS[1]: 5, DOCS[2]: 5}
    control = [run(30, right_by_document=lost)] * 3
    candidate = [run(35, right_by_document=full, reading_right=26)] * 3
    verdict = acc.judge(control, candidate)
    assert unmet(verdict) == ["4"] and verdict["decision"] == CONTROL_STAYS
    left_out = verdict["official"]["rules"][-1]["candidate"]
    assert left_out[DOCS[0]] == 0.0 and left_out[DOCS[1]] == 5.0


def test_rule_4_a_gain_spread_over_the_documents_passes() -> None:
    assert "4" not in unmet(acc.judge(CONTROL, CANDIDATE))  # 4 fields in 4 documents


# -------------------------------------------------------------------- no verdict


def no_verdict(verdict: dict[str, Any]) -> bool:
    return (
        verdict["decision"] == NO_VERDICT
        and verdict["procedure"]["valid"] is False
        and verdict["official"] == {"met": None, "rules": []}
        and bool(verdict["procedure"]["problems"])
    )


def test_a_failed_check_of_integrity_gives_no_verdict() -> None:
    verdict = acc.judge(
        CONTROL, CANDIDATE, integrity=["the code is not the frozen code"]
    )
    assert no_verdict(verdict)
    assert verdict["procedure"]["problems"] == ["the code is not the frozen code"]


@pytest.mark.parametrize("scope", ["control", "candidate"])
def test_a_run_that_is_not_complete_gives_no_verdict(scope: str) -> None:
    broken = [run(34), run(34, complete=False), run(34)]
    verdict = (
        acc.judge(broken, CANDIDATE)
        if scope == "control"
        else acc.judge(CONTROL, broken)
    )
    assert no_verdict(verdict) and scope in verdict["procedure"]["problems"][0]


@pytest.mark.parametrize("scope", ["control", "candidate"])
def test_a_call_with_no_answer_after_the_retries_gives_no_verdict(scope: str) -> None:
    """The rule of #20. An infrastructure failure never counts for or against a
    scope."""
    broken = [run(34), run(34), run(34, calls_without_answer=1)]
    verdict = (
        acc.judge(broken, CANDIDATE)
        if scope == "control"
        else acc.judge(CONTROL, broken)
    )
    assert no_verdict(verdict)


@pytest.mark.parametrize("runs", [0, 1, 2, 4])
def test_another_number_of_runs_than_three_gives_no_verdict(runs: int) -> None:
    assert no_verdict(acc.judge(CONTROL, [run(34)] * runs))
    assert no_verdict(acc.judge([run(30)] * runs, CANDIDATE))


def test_no_verdict_is_never_a_pass_even_with_a_large_gain() -> None:
    verdict = acc.judge(
        CONTROL, [run(35)] * 3, integrity=["the request count is not the plan"]
    )
    assert verdict["decision"] == NO_VERDICT and verdict["official"]["met"] is None


# ------------------------------------------------- supplementary, never the verdict


def test_a_gain_of_two_is_reported_and_does_not_change_the_decision() -> None:
    verdict = acc.judge(CONTROL, [run(32)] * 3)
    assert unmet(verdict) == ["1"] and verdict["decision"] == CONTROL_STAYS
    thresholds = verdict["supplementary"]["thresholds"]
    assert thresholds == {
        "1": {"rule_1_met": True, "every_rule_met": True},
        "2": {"rule_1_met": True, "every_rule_met": True},
    }
    assert "never a reason to change the default" in verdict["supplementary"]["note"]


def test_a_gain_of_one_meets_only_the_threshold_of_one() -> None:
    thresholds = acc.judge(CONTROL, [run(31)] * 3)["supplementary"]["thresholds"]
    assert thresholds["1"]["rule_1_met"] and not thresholds["2"]["rule_1_met"]


def test_a_supplementary_threshold_does_not_hide_a_failed_guardrail() -> None:
    candidate = [run(32, guarded_wrong=1, wrong=1)] * 3
    thresholds = acc.judge(CONTROL, candidate)["supplementary"]["thresholds"]
    assert thresholds["2"] == {"rule_1_met": True, "every_rule_met": False}


def gates(verdict: dict[str, Any]) -> dict[str, Any]:
    return {g["gate"]: g["met"] for g in verdict["proposed_gates"]}


def test_the_proposed_gates_are_reported_and_all_hold_for_clean_runs() -> None:
    assert gates(acc.judge(CONTROL, CANDIDATE)) == {
        "1": True,
        "2": True,
        "3": True,
        "4": True,
        "5": True,
        "reported": None,
    }


@pytest.mark.parametrize(
    ("change", "gate"),
    [
        ({"incorrect_trusted": 1}, "1"),
        ({"incorrect_trusted_absent": 1}, "2"),
        ({"unaccepted_page_trusted": 1}, "3"),
        ({"pages": {"clean": (25, 26), "scan": (7, 7)}}, "4"),
        ({"pages": {"clean": (26, 26), "scan": (6, 7)}}, "4"),
        ({"placeholders": 1}, "5"),
    ],
)
def test_a_failed_proposed_gate_is_shown_and_does_not_change_the_decision(
    change: dict[str, Any], gate: str
) -> None:
    """The gates are not approved. A candidate that meets the official rules keeps
    its decision; the report shows the gate that fails, for the person who decides."""
    candidate = [run(34), run(34), run(34, **change)]
    verdict = acc.judge(CONTROL, candidate)
    assert gates(verdict)[gate] is False
    assert verdict["decision"] == CANDIDATE_MEETS


def test_gate_1_compares_with_the_highest_run_of_the_control() -> None:
    control = [run(30, incorrect_trusted=1), run(30), run(30)]
    assert gates(acc.judge(control, [run(34, incorrect_trusted=1)] * 3))["1"] is True
    assert gates(acc.judge(control, [run(34, incorrect_trusted=2)] * 3))["1"] is False


def test_a_gate_with_no_confidence_is_not_available_and_not_met() -> None:
    candidate = [run(34, incorrect_trusted=None, unaccepted_page_trusted=None)] * 3
    found = gates(acc.judge(CONTROL, candidate))
    assert found["1"] is None and found["3"] is None  # never taken as 0


def test_the_fields_that_lose_their_verified_mark_are_listed() -> None:
    both = frozenset({("mp1/d1", "total"), ("mp1/d2", "invoice_date")})
    control = [run(30, trusted=both)] * 3
    candidate = [
        run(34, trusted=both),
        run(34, trusted=both - {("mp1/d1", "total")}),
    ] * 2
    reported = acc.judge(control, candidate[:3])["proposed_gates"][-1]
    assert reported["control"] == 2 and reported["candidate"] == ["mp1/d1: total"]


# ------------------------------------------------- from the reports of the harness


@pytest.fixture(scope="module")
def perfect() -> dict[str, Any]:
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("NVIDIA_KEY", "test-key-value")
        patch.setenv("VLM_BASE_URL", "https://vlm.test/v1")
        patch.setenv("VLM_MODEL", "omni-test")
        return {
            scope: evaluated(scope)["run_details"][0] for scope in ("page", "document")
        }


@pytest.mark.parametrize("scope", ["page", "document"])
def test_the_facts_of_a_run_of_a_perfect_model_on_mp1(
    perfect: dict[str, Any], scope: str
) -> None:
    found = acc.facts(perfect[scope], documents=8)
    assert found.complete
    assert (found.right, found.wrong, found.missing) == (35, 0, 0)
    assert found.reading_right == 31  # 35 less the 4 business-category fields
    assert found.guarded_wrong == 0 and found.classified_right == 8
    assert (found.text_lines_right, found.text_lines_expected) == (35, 35)
    assert found.reader_errors == 0 and found.seconds is not None
    assert (
        sum(found.right_by_document.values()) == 35
        and len(found.right_by_document) == 8
    )
    assert found.pages == {"clean": (26, 26), "scan": (7, 7)}
    assert (found.incorrect_trusted, found.incorrect_trusted_absent) == (0, 0)
    assert found.unaccepted_page_trusted == 0 and found.placeholders == 0
    assert len(found.trusted) == 26  # the 7 fields of the scans are for a person


def test_a_run_with_another_number_of_documents_is_not_complete(
    perfect: dict[str, Any],
) -> None:
    assert not acc.facts(perfect["page"], documents=9).complete


def test_two_perfect_scopes_give_no_gain_and_the_control_stays(
    perfect: dict[str, Any],
) -> None:
    control = [acc.facts(perfect["page"], documents=8)] * 3
    candidate = [acc.facts(perfect["document"], documents=8)] * 3
    verdict = acc.judge(control, candidate)
    assert verdict["procedure"]["valid"] and verdict["official"]["gain"] == 0.0
    assert unmet(verdict) == ["1", "2", "4"] and verdict["decision"] == CONTROL_STAYS
    assert all(met for gate, met in gates(verdict).items() if gate != "reported")


def test_the_facts_see_a_wrong_date_a_lure_and_a_trusted_wrong_total(
    perfect: dict[str, Any],
) -> None:
    """Rows changed by hand, as a reader with these mistakes would give them."""
    run_ = {
        **perfect["page"],
        "documents": [dict(d) for d in perfect["page"]["documents"]],
    }
    for d in run_["documents"]:
        d["fields"] = [dict(r) for r in d["fields"]]
        for r in d["fields"]:
            if (d["file"][:6], r["name"]) == ("mp1-08", "incident_date"):
                r |= {"status": "wrong", "read": "2026-11-11", "confidence": 0.5}
            if (d["file"][:6], r["name"]) == ("mp1-04", "total"):
                r |= {"status": "wrong", "read": 9763.5, "confidence": 0.9}
            if (d["file"][:6], r["name"]) == ("mp1-07", "trip_price"):
                r |= {
                    "status": "wrong",
                    "read": 430.0,
                    "confidence": 0.5,
                    "page_right": False,
                }
    found = acc.facts(run_)
    assert found.guarded_wrong == 3  # a date and two amounts
    assert found.incorrect_trusted == 1 and found.incorrect_trusted_absent == 0
    assert ("mp1/mp1-04-repair-quote-terms.pdf", "total") not in found.trusted


# ----------------------------------------------------------------------- the text


@pytest.mark.parametrize(
    "verdict",
    [
        acc.judge(CONTROL, CANDIDATE),
        acc.judge(CONTROL, [run(32)] * 3),
        acc.judge(CONTROL, CANDIDATE, integrity=["a key error"]),
    ],
)
def test_the_text_of_a_verdict_says_the_decision_first(verdict: dict[str, Any]) -> None:
    lines = acc.text(verdict).splitlines()
    assert lines[0] == f"**Decision: {verdict['decision']}.**"
    for word in ("reject", "deny", "denied", "declin"):
        assert word not in acc.text(verdict).casefold()
