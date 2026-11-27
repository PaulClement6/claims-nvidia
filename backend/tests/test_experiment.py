"""The controlled runner of issue #126, rehearsed end to end with a stub of the
endpoint: no key, no network.

Every test runs the real plan through the real reader, the real harness, the real
record of the requests and the real acceptance rules. Only the endpoint is a stub.
Its answers come from the answer keys of MP1, with mistakes added on purpose, so the
numbers below say how the machinery counts. They are NOT results of a model.
"""

import hashlib
import itertools
import json
from collections.abc import Callable, Iterator
from functools import cache
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest

from app.docintel import acceptance, experiment, omni, telemetry
from app.docintel.acceptance import CANDIDATE_MEETS, CONTROL_STAYS, NO_VERDICT
from tests.test_mp1_reader import MP1, key_of, page_answers

Json = dict[str, Any]
KEY = "test-key-value-0123456789"
PAGE_RUNS = ["r1-page", "r4-page", "r5-page"]
DOCUMENT_RUNS = ["r2-document", "r3-document", "r6-document"]
Mistakes = Callable[[str, str, list[Json]], None]
Fault = Callable[[str, str, int, int], httpx.Response | None]


@pytest.fixture(scope="module", autouse=True)
def stage() -> Iterator[None]:
    """The variables of the reader, and one render per page for the whole module
    (the reader renders each page again in each run; the pixels are the same)."""
    real, seen = omni.pages, {}

    def pages(data: bytes, first: int | None = None, dpi: int = omni.DPI) -> Any:
        mark = (hashlib.sha256(data).digest(), first, dpi)
        if mark not in seen:
            seen[mark] = real(data, first, dpi)
        return seen[mark]

    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("NVIDIA_KEY", KEY)
        patch.setenv("VLM_BASE_URL", "https://vlm.test/v1")
        patch.setenv("VLM_MODEL", "omni-test")
        patch.setattr(omni, "pages", pages)
        yield


@cache
def shown() -> dict[str, tuple[str, int]]:
    found = {}
    for path in sorted(MP1.glob("*.pdf")):
        for number, page in enumerate(omni.pages(path.read_bytes()), 1):
            found[hashlib.sha256(page.image).hexdigest()] = (path.name, number)
    return found


def answer(
    content: Any, finish: str | None = "stop", usage: Any = "full"
) -> httpx.Response:
    text = content if isinstance(content, str) else json.dumps(content, indent=2)
    choice: Json = {"message": {"content": text}}
    if finish is not None:
        choice["finish_reason"] = finish
    body: Json = {"choices": [choice]}
    if usage == "full":
        body["usage"] = {
            "prompt_tokens": 2500,
            "completion_tokens": len(text) // 3,
            "prompt_tokens_details": {"cached_tokens": 1900},
            "completion_tokens_details": {"reasoning_tokens": 0},
        }
    elif usage == "plain":  # counts, and no word about the cache or the reasoning
        body["usage"] = {"prompt_tokens": 2500, "completion_tokens": len(text) // 3}
    return httpx.Response(200, json=body)


class Endpoint:
    """A stub of the endpoint. It answers from the answer key of the document that
    the first image shows. `mistakes` changes the page answers (it gets the kind of
    the call, so one scope can err and the other not). `fault` can answer a request
    in another way: it gets the kind, the file, the page and how many times this
    same request was seen."""

    def __init__(
        self, mistakes: Mistakes | None = None, fault: Fault | None = None
    ) -> None:
        self.mistakes, self.fault = mistakes, fault
        self.seen: list[tuple[str, str, int]] = []
        self.bodies: list[Json] = []
        self.headers: list[httpx.Headers] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        kind = telemetry.call_kind(body)
        images = experiment._images(body)
        name, page = shown()[hashlib.sha256(images[0]).hexdigest()]
        self.seen.append((kind, name, page))
        self.bodies.append(body)
        self.headers.append(request.headers)
        if self.fault:
            other = self.fault(kind, name, page, self.seen.count((kind, name, page)))
            if other is not None:
                return other
        key = key_of(name)
        if kind == "classify":
            return answer({"document_type": key["document_type"]})
        answers = page_answers(key)
        if self.mistakes:
            self.mistakes(kind, name, answers)
        if kind == "page":
            return answer(answers[page - 1])
        return answer({f"page_{n}": a for n, a in enumerate(answers, 1)})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)


def ticking() -> Callable[[], float]:
    """A clock that moves one second at each look: every call takes one second."""
    tick = itertools.count()
    return lambda: float(next(tick))


def rehearse(
    folder: Path, endpoint: Endpoint, **more: Any
) -> tuple[Json, Json, list[float]]:
    """One main run through the stub: (meta, verdict, the waits of the retries)."""
    waits: list[float] = []
    meta = experiment.run_main(
        folder / "run",
        endpoint.transport,
        mode="rehearsal",
        lock=folder / "lock",
        clock=ticking(),
        sleep=waits.append,
        **more,
    )
    return meta, experiment.assess(folder / "run"), waits


def page_scope_errs(kind: str, name: str, answers: list[Json]) -> None:
    """Three mistakes of the page scope only: a subtotal as the total (mp1-01), the
    date of the report as the incident date (mp1-08), an invented snippet (mp1-05)."""
    if kind != "page":
        return
    if name.startswith("mp1-01"):
        answers[0]["values"]["total"] = 2435.0
        answers[0]["snippets"]["total"] = "2,435.00"
    if name.startswith("mp1-08"):
        answers[0]["values"]["incident_date"] = "2026-11-11"
        answers[0]["snippets"]["incident_date"] = "2026-11-11"
    if name.startswith("mp1-05"):
        answers[0]["snippets"]["customer_name"] = "a text that the page does not print"


def document_scope_errs(kind: str, name: str, answers: list[Json]) -> None:
    """Two mistakes of the document scope only: the total with options at 0.9
    (mp1-04) and one row left out (mp1-05)."""
    if kind != "document":
        return
    if name.startswith("mp1-04"):
        answers[1]["values"]["total"] = 9763.5
        answers[1]["snippets"]["total"] = "CHF 9,763.50"
    if name.startswith("mp1-05"):
        del answers[1]["values"]["line_items"][2]


def unmet(verdict: Json) -> list[str]:
    return [r["rule"] for r in verdict["official"]["rules"] if not r["met"]]


@pytest.fixture(scope="module")
def better(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, Endpoint, Json, Json]:
    """A rehearsal in which the document scope is right and the page scope errs."""
    folder = tmp_path_factory.mktemp("better")
    endpoint = Endpoint(mistakes=page_scope_errs)
    meta, verdict, _ = rehearse(folder, endpoint)
    return folder / "run", endpoint, meta, verdict


# ----------------------------------------------------------- the plan and the order


def test_the_plan_is_page_document_document_page_page_document() -> None:
    assert experiment.plan() == [
        ("r1-page", "page"),
        ("r2-document", "document"),
        ("r3-document", "document"),
        ("r4-page", "page"),
        ("r5-page", "page"),
        ("r6-document", "document"),
    ]
    assert experiment.planned_calls("page") == 27
    assert experiment.planned_calls("document") == 16
    assert sum(experiment.planned_calls(s) for s in experiment.ORDER) == 129
    assert experiment.PLANNED == {"main": 129, "pilot": 4}
    assert experiment.CAP == {"main": 145, "pilot": 8}


def test_a_rehearsal_sends_129_requests_in_the_order_of_the_plan(
    better: tuple[Path, Endpoint, Json, Json],
) -> None:
    out, endpoint, meta, verdict = better
    assert meta["status"] == "complete" and meta["attempts"] == 129
    assert len(endpoint.seen) == 129
    assert meta["runs"] == [tag for tag, _ in experiment.plan()]
    assert verdict["requests"] == [
        {"run": tag, "planned": n, "calls": n, "attempts": n, "retries": 0}
        for tag, n in zip(meta["runs"], [27, 16, 16, 27, 27, 16], strict=True)
    ]
    lines = telemetry.load(out / "calls.jsonl")
    assert [line["tag"] for line in lines] == [
        tag
        for tag, n in zip(meta["runs"], [27, 16, 16, 27, 27, 16], strict=True)
        for _ in range(n)
    ]


def test_each_run_classifies_every_document_again_with_a_new_reader(
    better: tuple[Path, Endpoint, Json, Json],
) -> None:
    """A reader keeps the type of a document. A new reader for each run means that
    no run is cheaper than another: 8 classify calls in each of the 6 runs."""
    _, _, _, verdict = better
    by_tag = verdict["telemetry"]["by_tag"]
    for tag in PAGE_RUNS:
        assert by_tag[tag]["calls"] == {"classify": 8, "page": 19}
    for tag in DOCUMENT_RUNS:
        assert by_tag[tag]["calls"] == {"classify": 8, "document": 8}


def test_only_the_scope_of_the_extraction_call_differs_between_the_runs(
    better: tuple[Path, Endpoint, Json, Json],
) -> None:
    out, endpoint, _, _ = better
    lines = telemetry.load(out / "calls.jsonl")
    for body, line in zip(endpoint.bodies, lines, strict=True):
        assert body["model"] == "omni-test"
        assert (body["temperature"], body["max_tokens"]) == (0.2, 2048)
        assert body["chat_template_kwargs"] == {"enable_thinking": False}
        assert body["response_format"]["type"] == "json_schema"
        images = len(experiment._images(body))
        assert images == len(line["images"])
        assert images == (1 if line["kind"] != "document" else key_pages(line))
    classify = [
        b
        for b, s in zip(endpoint.bodies, endpoint.seen, strict=True)
        if s[0] == "classify"
    ]
    first = {json.dumps(b, sort_keys=True) for b in classify[:8]}
    assert (
        len(classify) == 48
        and len({json.dumps(b, sort_keys=True) for b in classify}) == 8
    )
    assert len(first) == 8  # the same 8 classify requests in every run and scope
    settings = {
        tag: experiment._read(out / f"run-{tag}.json")["settings"] for tag in PAGE_RUNS
    }
    assert all("scope" not in s for s in settings.values())
    for tag in DOCUMENT_RUNS:
        assert (
            experiment._read(out / f"run-{tag}.json")["settings"]["scope"] == "document"
        )


def key_pages(line: Json) -> int:
    name, _ = shown()[line["images"][0]]
    pages: int = key_of(name)["pages"]
    return pages


def test_the_six_runs_read_the_same_documents_in_the_same_order(
    better: tuple[Path, Endpoint, Json, Json],
) -> None:
    out, _, meta, _ = better
    order = []
    for tag in meta["runs"]:
        [run] = experiment._read(out / f"run-{tag}.json")["run_details"]
        order.append([d["id"] for d in run["documents"]])
    assert len(order) == 6 and all(o == order[0] for o in order) and len(order[0]) == 8


# ------------------------------------------------------------ the verdict, three ways


def test_a_candidate_that_reads_three_more_fields_meets_the_official_rules(
    better: tuple[Path, Endpoint, Json, Json],
) -> None:
    """Stub answers. The page scope gets 32 of 35 fields, the document scope 35."""
    _, _, _, verdict = better
    assert verdict["procedure"] == {"valid": True, "problems": []}
    assert verdict["official"]["gain"] == 3.0 and unmet(verdict) == []
    assert verdict["decision"] == CANDIDATE_MEETS
    by_rule = {r["rule"]: r for r in verdict["official"]["rules"]}
    assert (by_rule["1"]["control"], by_rule["1"]["candidate"]) == (32.0, 35.0)
    assert (by_rule["3a"]["control"], by_rule["3a"]["candidate"]) == (6, 0)
    assert by_rule["3e"]["candidate"] == [35, 35, 35]
    assert set(by_rule["4"]["candidate"].values()) == {2.0, 3.0}
    assert (by_rule["3g"]["control"], by_rule["3g"]["candidate"]) == (2.0, 2.0)


def test_the_verdict_is_written_next_to_the_runs(
    better: tuple[Path, Endpoint, Json, Json],
) -> None:
    out, _, _, verdict = better
    assert experiment._read(out / "verdict.json")["decision"] == verdict["decision"]
    text = (out / "verdict.md").read_text(encoding="utf-8")
    assert text.startswith(f"**Decision: {CANDIDATE_MEETS}.**")
    assert "Supplementary (never the verdict)" in text
    assert "Proposed gates (not approved, never the verdict)" in text


def test_a_candidate_that_errs_does_not_replace_the_control(tmp_path: Path) -> None:
    """The document scope gives a wrong total at 0.9 and leaves a row out."""
    meta, verdict, _ = rehearse(tmp_path, Endpoint(mistakes=document_scope_errs))
    assert meta["status"] == "complete" and verdict["procedure"]["valid"]
    assert verdict["decision"] == CONTROL_STAYS
    assert unmet(verdict) == ["1", "2", "3a", "3b", "3e", "4"]
    gates = {g["gate"]: g["met"] for g in verdict["proposed_gates"]}
    assert gates["1"] is False  # a wrong value that no person would check
    assert gates["2"] and gates["3"] and gates["5"]


def test_two_perfect_scopes_give_no_gain_and_the_control_stays(tmp_path: Path) -> None:
    _, verdict, _ = rehearse(tmp_path, Endpoint())
    assert verdict["official"]["gain"] == 0.0 and verdict["decision"] == CONTROL_STAYS
    assert unmet(verdict) == ["1", "2", "4"]
    thresholds = verdict["supplementary"]["thresholds"]
    assert not thresholds["1"]["rule_1_met"] and not thresholds["2"]["rule_1_met"]


def test_a_gain_of_two_fields_is_reported_and_is_not_a_pass(tmp_path: Path) -> None:
    def two(kind: str, name: str, answers: list[Json]) -> None:
        if not name.startswith("mp1-05"):
            page_scope_errs(kind, name, answers)

    _, verdict, _ = rehearse(tmp_path, Endpoint(mistakes=two))
    assert verdict["official"]["gain"] == 2.0 and unmet(verdict) == ["1"]
    assert verdict["decision"] == CONTROL_STAYS
    assert verdict["supplementary"]["thresholds"]["2"] == {
        "rule_1_met": True,
        "every_rule_met": True,
    }


# ----------------------------------------------- failures: mode, infrastructure, cap


def test_a_cut_document_answer_is_a_valid_run_and_a_failed_candidate(
    tmp_path: Path,
) -> None:
    """A measured weakness of the scope: the answer of mp1-01 ends at the token
    limit in one run. The run is valid; the document has no field; rule 3f fails."""

    def cut(kind: str, name: str, page: int, times: int) -> httpx.Response | None:
        if kind == "document" and name.startswith("mp1-01"):
            whole = {
                f"page_{n}": a for n, a in enumerate(page_answers(key_of(name)), 1)
            }
            return answer(json.dumps(whole)[:900], finish="length")
        return None

    meta, verdict, _ = rehearse(tmp_path, Endpoint(mistakes=page_scope_errs, fault=cut))
    assert meta["status"] == "complete" and meta["attempts"] == 129  # no second try
    assert verdict["procedure"]["valid"] is True
    assert "3f" in unmet(verdict) and verdict["decision"] == CONTROL_STAYS
    by_rule = {r["rule"]: r for r in verdict["official"]["rules"]}
    assert by_rule["3f"]["candidate"] == [(0, 0, 1)] * 3
    reasons = verdict["telemetry"]["by_tag"]["r2-document"]["finish_reasons"]
    assert reasons["document"] == {"stop": 7, "length": 1}


def test_a_whole_answer_that_the_endpoint_marks_as_cut_stays_a_cut_answer(
    tmp_path: Path,
) -> None:
    """The answer of mp1-01 is whole JSON, and the endpoint gives the finish reason
    `length`. The reader reads it (every field for a person, no line). That it can
    be read does not make it a usable answer: the record keeps `length` and rule 3f
    fails."""

    def marked(kind: str, name: str, page: int, times: int) -> httpx.Response | None:
        if kind == "document" and name.startswith("mp1-01"):
            whole = {
                f"page_{n}": a for n, a in enumerate(page_answers(key_of(name)), 1)
            }
            return answer(whole, finish="length")
        return None

    endpoint = Endpoint(mistakes=page_scope_errs, fault=marked)
    meta, verdict, _ = rehearse(tmp_path, endpoint)
    assert meta["status"] == "complete" and meta["attempts"] == 129  # no second try
    assert verdict["procedure"]["valid"] is True
    by_rule = {r["rule"]: r for r in verdict["official"]["rules"]}
    assert by_rule["3f"]["candidate"] == [(0, 0, 1)] * 3
    assert "3f" in unmet(verdict) and verdict["decision"] == CONTROL_STAYS
    for tag in DOCUMENT_RUNS:
        reasons = verdict["telemetry"]["by_tag"][tag]["finish_reasons"]
        assert reasons["document"] == {"stop": 7, "length": 1}
        report = json.loads((tmp_path / "run" / f"run-{tag}.json").read_text("utf-8"))
        [cut] = [
            d
            for d in report["run_details"][0]["documents"]
            if d["file"].startswith("mp1-01")
        ]
        assert cut["counts"]["right"] == 5  # read, and not trusted:
        assert all(f["confidence"] < 0.8 for f in cut["fields"])
        assert cut["line_items"]["read"] == 0


def test_a_document_block_in_another_form_is_an_unusable_answer(tmp_path: Path) -> None:
    """The answer of mp1-02 names both pages and ends with `stop`, and the block of
    page 2 has no `values` object. A page answer in that form is unusable in the
    page scope; the same holds for a block: rule 3f fails."""

    def bent(kind: str, name: str, page: int, times: int) -> httpx.Response | None:
        if kind == "document" and name.startswith("mp1-02"):
            blocks = page_answers(key_of(name))
            whole: Json = {f"page_{n}": a for n, a in enumerate(blocks, 1)}
            whole["page_2"] = {"values": None, "snippets": {}}
            return answer(whole)
        return None

    endpoint = Endpoint(mistakes=page_scope_errs, fault=bent)
    _, verdict, _ = rehearse(tmp_path, endpoint)
    assert verdict["procedure"]["valid"] is True
    by_rule = {r["rule"]: r for r in verdict["official"]["rules"]}
    assert by_rule["3f"]["candidate"] == [(0, 0, 1)] * 3
    assert "3f" in unmet(verdict) and verdict["decision"] == CONTROL_STAYS


def test_an_unusable_answer_of_the_control_gives_no_verdict(tmp_path: Path) -> None:
    """A page answer of the control is cut in each of its runs: page 1 of mp1-01 is
    not read and the control loses its fields. With no rule, that loss would be a
    gain of the candidate (both scopes are perfect here). So it is no verdict."""

    def cut(kind: str, name: str, page: int, times: int) -> httpx.Response | None:
        if kind == "page" and name.startswith("mp1-01") and page == 1:
            whole = json.dumps(page_answers(key_of(name))[0])
            return answer(whole[:200], finish="length")
        return None

    meta, verdict, _ = rehearse(tmp_path, Endpoint(fault=cut))
    assert meta["status"] == "complete" and meta["attempts"] == 129
    assert verdict["procedure"]["valid"] is False and verdict["decision"] == NO_VERDICT
    problems = verdict["procedure"]["problems"]
    assert [p.split(":")[0] for p in problems] == PAGE_RUNS
    assert all("of the control are cut or not in the asked form" in p for p in problems)


def test_a_retry_that_gets_an_answer_is_a_success_and_is_counted(
    tmp_path: Path,
) -> None:
    def once(kind: str, name: str, page: int, times: int) -> httpx.Response | None:
        first = (
            kind == "page" and name.startswith("mp1-02") and page == 2 and times == 1
        )
        return (
            httpx.Response(429, headers={"Retry-After": "3"}, json={})
            if first
            else None
        )

    meta, verdict, waits = rehearse(
        tmp_path, Endpoint(mistakes=page_scope_errs, fault=once)
    )
    assert meta["status"] == "complete" and meta["attempts"] == 130
    assert waits == [3.0]
    assert verdict["requests"][0] == {
        "run": "r1-page",
        "planned": 27,
        "calls": 27,
        "attempts": 28,
        "retries": 1,
    }
    assert verdict["telemetry"]["http_429"] == 1
    assert verdict["procedure"]["valid"] and verdict["decision"] == CANDIDATE_MEETS


def test_a_call_with_no_answer_after_the_retries_gives_no_verdict(
    tmp_path: Path,
) -> None:
    def dead(kind: str, name: str, page: int, times: int) -> httpx.Response | None:
        lost = kind == "page" and name.startswith("mp1-04") and page == 3 and times <= 3
        return httpx.Response(503, json={}) if lost else None

    meta, verdict, waits = rehearse(
        tmp_path, Endpoint(mistakes=page_scope_errs, fault=dead)
    )
    assert meta["status"] == "complete" and meta["attempts"] == 131
    assert waits == [omni.WAITS[0], omni.WAITS[1]]
    assert verdict["decision"] == NO_VERDICT and verdict["official"]["met"] is None
    assert "no answer after the retries" in verdict["procedure"]["problems"][0]


def test_the_cap_stops_the_run_before_the_next_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(experiment.CAP, "main", 40)
    endpoint = Endpoint()
    meta, verdict, _ = rehearse(tmp_path, endpoint)
    assert meta["status"].startswith("stopped:") and "cap" in meta["status"]
    assert meta["attempts"] == 40 and len(endpoint.seen) == 40  # attempt 41 never left
    assert meta["runs"] == ["r1-page"]  # nothing restarts, nothing is tried again
    assert verdict["decision"] == NO_VERDICT
    assert not (tmp_path / "lock").exists()


def test_an_endpoint_that_fails_stops_the_run(tmp_path: Path) -> None:
    def down(kind: str, name: str, page: int, times: int) -> httpx.Response | None:
        return httpx.Response(500, json={})

    endpoint = Endpoint(fault=down)
    meta, verdict, _ = rehearse(tmp_path, endpoint)
    assert meta["status"].startswith("stopped:") and "failing" in meta["status"]
    assert len(endpoint.seen) == 15  # 5 calls of 3 attempts, then nothing
    assert verdict["decision"] == NO_VERDICT


def test_a_second_run_at_the_same_time_is_refused_and_sends_nothing(
    tmp_path: Path,
) -> None:
    (tmp_path / "lock").write_text("another run", encoding="utf-8")
    endpoint = Endpoint()
    meta, verdict, _ = rehearse(tmp_path, endpoint)
    assert meta["status"].startswith("stopped:") and endpoint.seen == []
    assert verdict["decision"] == NO_VERDICT
    assert (tmp_path / "lock").read_text(
        encoding="utf-8"
    ) == "another run"  # not taken over


# ----------------------------------------------------- the record: secrets and usage


def test_no_key_no_header_and_no_image_is_in_any_written_file(tmp_path: Path) -> None:
    def echo(kind: str, name: str, page: int, times: int) -> httpx.Response | None:
        if kind == "classify" and name.startswith("mp1-03") and times == 1:
            return httpx.Response(401, json={"error": f"bad key {KEY}"})
        return None

    endpoint = Endpoint(fault=echo)
    meta, _, _ = rehearse(tmp_path, endpoint)
    assert all(h["authorization"] == f"Bearer {KEY}" for h in endpoint.headers)
    for path in sorted((tmp_path / "run").rglob("*")):
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            assert KEY not in text and KEY[:8] not in text, path.name
            assert "Bearer" not in text and "authorization" not in text.casefold()
            assert "data:image" not in text
    assert meta["status"] == "complete"


def test_usage_that_the_endpoint_does_not_report_stays_missing(tmp_path: Path) -> None:
    """Page calls report no usage at all; document calls report counts and no word
    about the cache. A missing count is counted as missing, never as zero."""

    def quiet(kind: str, name: str, page: int, times: int) -> httpx.Response | None:
        key = key_of(name)
        answers = page_answers(key)
        if kind == "page":
            return answer(answers[page - 1], usage=None)
        if kind == "document":
            return answer(
                {f"page_{n}": a for n, a in enumerate(answers, 1)}, usage="plain"
            )
        return None

    _, verdict, _ = rehearse(tmp_path, Endpoint(fault=quiet))
    tokens = verdict["telemetry"]["tokens"]
    assert tokens["prompt"]["missing"] == 57 and tokens["prompt"]["reported"] == 72
    assert tokens["cached"] == {"sum": 48 * 1900, "reported": 48, "missing": 81}
    page_run = verdict["telemetry"]["by_tag"]["r1-page"]["tokens"]
    assert (
        page_run["completion"]["reported"] == 8
        and page_run["completion"]["missing"] == 19
    )
    report = experiment._read(tmp_path / "run" / "run-r1-page.json")
    assert report["run_details"][0]["http"]["tokens"]["no_usage"] == 19


def test_the_cache_and_the_finish_reasons_of_a_full_answer_are_kept(
    better: tuple[Path, Endpoint, Json, Json],
) -> None:
    _, _, _, verdict = better
    tokens = verdict["telemetry"]["tokens"]
    assert tokens["cached"] == {"sum": 129 * 1900, "reported": 129, "missing": 0}
    assert tokens["reasoning"] == {"sum": 0, "reported": 129, "missing": 0}
    assert verdict["telemetry"]["by_tag"]["r1-page"]["finish_reasons"] == {
        "classify": {"stop": 8},
        "page": {"stop": 19},
    }


# ------------------------------------------------------------------------ the replay


def test_the_replay_gives_the_saved_reports_with_no_request(
    better: tuple[Path, Endpoint, Json, Json], monkeypatch: pytest.MonkeyPatch
) -> None:
    out, endpoint, _, _ = better
    sent = len(endpoint.seen)
    monkeypatch.setattr(experiment, "_live_transport", forbidden)
    assert experiment.replay(out) == []
    assert len(endpoint.seen) == sent
    again = experiment.assess(out)
    assert again["decision"] == CANDIDATE_MEETS and again["official"]["gain"] == 3.0


def forbidden(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("a test reached for the network")


def test_a_changed_answer_or_a_changed_request_is_found_by_the_replay(
    better: tuple[Path, Endpoint, Json, Json], tmp_path: Path
) -> None:
    out, _, _, _ = better
    copy = tmp_path / "copy"
    copy.mkdir()
    for path in out.iterdir():
        if path.is_file():
            (copy / path.name).write_bytes(path.read_bytes())
    lines = telemetry.load(copy / "calls.jsonl")
    target = next(
        i
        for i, x in enumerate(lines)
        if x["tag"] == "r2-document" and x["kind"] == "document"
    )
    edited = [dict(x) for x in lines]
    edited[target]["body"] = edited[target]["body"].replace("WB-26-0714", "WB-26-0715")
    write_lines(copy / "calls.jsonl", edited)
    assert experiment.replay(copy) == [
        "r2-document: run_details differs from the saved report"
    ]

    edited = [dict(x) for x in lines]
    edited[target]["request_sha256"] = "0" * 64
    write_lines(copy / "calls.jsonl", edited)
    [difference] = experiment.replay(copy)
    assert difference.startswith("r2-document: replay")


def write_lines(path: Path, lines: list[Json]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.writelines(json.dumps(line) + "\n" for line in lines)


# -------------------------------------------------------- the preflight and the CLI


@pytest.fixture
def frozen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A freeze of the tree as it is, with a clean and known git state."""
    monkeypatch.setattr(experiment, "LOCK", tmp_path / "lock")
    monkeypatch.setattr(
        experiment, "_git", lambda *args: "" if args[0] == "status" else "abc123"
    )
    method = tmp_path / "METHOD.md"
    method.write_text("the method\n", encoding="utf-8")
    experiment.freeze(tmp_path / "freeze.json", method)
    return tmp_path / "freeze.json"


GOOD = "issue126-main-129-requests-cap-145 Nikola 2026-12-01"


def test_a_dry_run_needs_no_authorization_and_no_freeze(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(experiment, "LOCK", tmp_path / "lock")
    assert experiment.preflight("main", tmp_path / "new", live=False) == []


def test_a_live_run_with_everything_in_order_may_start(
    frozen: Path, tmp_path: Path
) -> None:
    found = experiment.preflight(
        "main", tmp_path / "new", live=True, frozen=frozen, authorize=GOOD
    )
    assert found == []


@pytest.mark.parametrize(
    "authorize",
    [
        "",
        "yes",
        "issue126-main-129-requests-cap-145",  # no name and no date
        "issue126-main-129-requests-cap-145 ",
        "issue126-pilot-4-requests-cap-8 Nikola 2026-12-01",  # the pilot text
        "issue126-main-129-requests-cap-200 Nikola 2026-12-01",
        "ISSUE126-MAIN-129-REQUESTS-CAP-145 Nikola 2026-12-01",
    ],
)
def test_a_live_main_run_needs_its_own_authorization_text(
    frozen: Path, tmp_path: Path, authorize: str
) -> None:
    found = experiment.preflight(
        "main", tmp_path / "new", live=True, frozen=frozen, authorize=authorize
    )
    assert len(found) == 1 and "--authorize" in found[0]


def test_the_authorization_of_the_main_run_does_not_start_the_pilot(
    frozen: Path, tmp_path: Path
) -> None:
    found = experiment.preflight(
        "pilot", tmp_path / "new", live=True, frozen=frozen, authorize=GOOD
    )
    assert len(found) == 1 and "issue126-pilot-4-requests-cap-8" in found[0]
    pilot = "issue126-pilot-4-requests-cap-8 Nikola 2026-12-01"
    assert (
        experiment.preflight(
            "pilot", tmp_path / "new", live=True, frozen=frozen, authorize=pilot
        )
        == []
    )


def test_a_live_run_needs_the_variables_of_the_reader(
    frozen: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NVIDIA_KEY")
    found = experiment.preflight(
        "main", tmp_path / "new", live=True, frozen=frozen, authorize=GOOD
    )
    assert found == ["a live run needs NVIDIA_KEY"]


def test_a_live_run_needs_the_freeze(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(experiment, "LOCK", tmp_path / "lock")
    found = experiment.preflight("main", tmp_path / "new", live=True, authorize=GOOD)
    assert found == ["a live run needs --freeze with the file that `freeze` wrote"]


def test_a_file_changed_after_the_freeze_stops_a_live_run(
    frozen: Path, tmp_path: Path
) -> None:
    (tmp_path / "METHOD.md").write_text("another method\n", encoding="utf-8")
    found = experiment.preflight(
        "main", tmp_path / "new", live=True, frozen=frozen, authorize=GOOD
    )
    assert found == ["changed since the freeze: method"]


def test_another_commit_or_an_uncommitted_file_stops_a_live_run(
    frozen: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        experiment,
        "_git",
        lambda *args: (
            " M backend/app/docintel/omni.py" if args[0] == "status" else "def456"
        ),
    )
    found = experiment.preflight(
        "main", tmp_path / "new", live=True, frozen=frozen, authorize=GOOD
    )
    assert found == [
        "the commit is not the commit of the freeze",
        "a frozen file is not committed",
    ]


def test_an_old_folder_or_an_open_lock_stops_every_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(experiment, "LOCK", tmp_path / "lock")
    (tmp_path / "old").mkdir()
    (tmp_path / "lock").write_text("open", encoding="utf-8")
    found = experiment.preflight("main", tmp_path / "old", live=False)
    assert len(found) == 2 and "exists" in found[0] and "exists" in found[1]


def test_the_freeze_holds_the_plan_the_caps_the_ids_and_the_official_threshold(
    frozen: Path,
) -> None:
    saved = experiment._read(frozen)
    assert saved["order"] == list(experiment.ORDER)
    assert saved["planned"] == {"main": 129, "pilot": 4} and saved["cap"] == {
        "main": 145,
        "pilot": 8,
    }
    assert saved["config_ids"] == {"page": "b0f2e2ccab2b", "document": "54b875de9dfa"}
    assert saved["rules"] == {"gain": 3, "supplementary_thresholds": [1, 2], "runs": 3}
    assert "backend/app/docintel/acceptance.py" in saved["files"]
    assert sum(name.startswith("data/mock/mp1/") for name in saved["files"]) == 17
    assert acceptance.GAIN == 3


def test_the_live_command_is_refused_before_anything_can_be_sent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No authorization that fits and no usable freeze: exit code 2, and the place
    that makes the real transport is never reached."""
    monkeypatch.setattr(experiment, "LOCK", tmp_path / "lock")
    monkeypatch.setattr(experiment, "_live_transport", forbidden)
    for command in ("main", "pilot"):
        code = experiment.main(
            [
                command,
                "--out",
                str(tmp_path / command),
                "--freeze",
                str(tmp_path / "none.json"),
                "--authorize",
                "yes please",
            ]
        )
        assert code == 2 and not (tmp_path / command).exists()
    assert "not started" in capsys.readouterr().out


def test_the_dry_run_command_runs_the_whole_plan_and_cannot_reach_the_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(experiment, "LOCK", tmp_path / "lock")
    monkeypatch.setattr(experiment, "_live_transport", forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    for name in experiment.ENV:  # a dry run brings its own stand-in values
        monkeypatch.delenv(name, raising=False)
    assert experiment.main(["dry-run", "main", "--out", str(tmp_path / "dry")]) == 0
    meta = experiment._read(tmp_path / "dry" / "meta.json")
    assert (meta["mode"], meta["status"], meta["attempts"]) == (
        "dry-run",
        "complete",
        129,
    )
    assert meta["model"] == "dry-run-model" and "dry-run.invalid" in meta["endpoint"]
    assert f"**Decision: {CONTROL_STAYS}.**" in capsys.readouterr().out
    assert experiment.main(["replay", str(tmp_path / "dry")]) == 0
    assert experiment.main(["verdict", str(tmp_path / "dry")]) == 0
    assert all(name not in __import__("os").environ for name in experiment.ENV)


# ------------------------------------------------------------------------- the pilot


def test_the_pilot_dry_run_sends_four_requests_and_passes_its_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(experiment, "_live_transport", forbidden)
    result = experiment.run_pilot(
        tmp_path / "pilot", None, mode="dry-run", lock=tmp_path / "lock"
    )
    assert (result["status"], result["attempts"], result["planned"], result["cap"]) == (
        "complete",
        4,
        4,
        8,
    )
    assert result["technical_checks_passed"] is True
    assert result["telemetry"]["calls"] == {"classify": 2, "document": 2}
    assert [d["pages"] for d in result["documents"]] == [2, 3]
    assert [d["kind"] for d in result["documents"]] == ["text", "scan"]
    for document in result["documents"]:
        assert len(document["checks"]) == 9 and all(
            c["passed"] for c in document["checks"]
        )
        seen = document["observed"]
        assert (
            seen["type_right"]
            and seen["fields_stored"] == seen["fields_on_an_accepted_page"]
        )
    assert "Nothing starts by itself" in result["next"]
    made = sorted(p.name for p in (tmp_path / "pilot").iterdir())
    assert made == ["calls.jsonl", "documents", "pilot.json"]  # no file of a main run


def test_the_pilot_documents_are_not_documents_of_mp1(tmp_path: Path) -> None:
    paths = experiment.pilot_documents(tmp_path / "documents")
    assert len(paths) == 2
    mp1 = {hashlib.sha256(p.read_bytes()).hexdigest() for p in MP1.glob("*.pdf")}
    assert not mp1 & {hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    assert not any((MP1 / p.name).exists() for p in paths)


def pilot_with(
    tmp_path: Path, handler: Callable[[httpx.Request], httpx.Response]
) -> Json:
    return experiment.run_pilot(
        tmp_path / "pilot",
        httpx.MockTransport(handler),
        mode="rehearsal",
        lock=tmp_path / "lock",
        sleep=lambda seconds: None,
    )


def test_a_pilot_whose_document_call_is_refused_does_not_pass(tmp_path: Path) -> None:
    """The endpoint answers 400 to a request with several images."""

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if telemetry.call_kind(body) == "document":
            return httpx.Response(400, json={"error": "one image per request"})
        kind = "medical_invoice" if len(calls) == 0 else "repair_quote"
        calls.append(1)
        return answer({"document_type": kind})

    calls: list[int] = []
    result = pilot_with(tmp_path, handle)
    assert result["status"] == "complete" and result["attempts"] == 4
    assert result["technical_checks_passed"] is False
    failed = [
        c["check"] for d in result["documents"] for c in d["checks"] if not c["passed"]
    ]
    assert "the document call got a 200" in failed


def test_a_pilot_answer_cut_at_the_token_limit_does_not_pass(tmp_path: Path) -> None:
    stub = experiment.key_model

    def handle(request: httpx.Request) -> httpx.Response:
        if not inner:
            inner.append(stub(tmp_path / "pilot" / "documents"))
        real = inner[0].handle_request(request)
        body = json.loads(request.content)
        if telemetry.call_kind(body) != "document":
            return real
        content = json.loads(real.read())["choices"][0]["message"]["content"]
        return answer(content[: len(content) // 2], finish="length")

    inner: list[httpx.MockTransport] = []
    result = pilot_with(tmp_path, handle)
    assert result["technical_checks_passed"] is False
    failed = {
        c["check"] for d in result["documents"] for c in d["checks"] if not c["passed"]
    }
    assert "the answer ended by itself (finish reason stop)" in failed
    assert "the answer is JSON with exactly one block for each page" in failed


def test_a_whole_pilot_answer_marked_as_cut_does_not_pass(tmp_path: Path) -> None:
    """Whole JSON with the finish reason `length`: the form checks pass, and the
    pilot does not, because the finish reason is reported as the endpoint gave it."""
    stub = experiment.key_model

    def handle(request: httpx.Request) -> httpx.Response:
        if not inner:
            inner.append(stub(tmp_path / "pilot" / "documents"))
        real = inner[0].handle_request(request)
        if telemetry.call_kind(json.loads(request.content)) != "document":
            return real
        content = json.loads(real.read())["choices"][0]["message"]["content"]
        return answer(content, finish="length")

    inner: list[httpx.MockTransport] = []
    result = pilot_with(tmp_path, handle)
    assert result["status"] == "complete" and result["attempts"] == 4
    assert result["technical_checks_passed"] is False
    for document in result["documents"]:
        checks = {c["check"]: c for c in document["checks"]}
        finish = checks["the answer ended by itself (finish reason stop)"]
        assert (finish["passed"], finish["seen"]) == (False, "length")
        assert checks["the answer is JSON with exactly one block for each page"][
            "passed"
        ]
        assert checks["every block is a valid page answer"]["passed"]
    assert result["telemetry"]["finish_reasons"]["document"] == {"length": 2}


def test_the_pilot_never_sends_more_than_eight_attempts(tmp_path: Path) -> None:
    seen: list[int] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(1)
        return httpx.Response(503, json={})

    result = pilot_with(tmp_path, handle)
    assert len(seen) == 8 and result["attempts"] == 8
    assert result["status"].startswith("stopped:") and "cap" in result["status"]
    assert result["technical_checks_passed"] is False
