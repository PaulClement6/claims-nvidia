"""What the pipeline panel shows of a sandbox (issue #15), with the fake `openshell`:
the policy view, the denials, the confinement probe, the order of the events, and the
rule that the token never leaves the backend. No OpenShell, no Docker: CI runs it.
The log lines and the probe line are copied from live runs (`tests/fake_shell.py`)."""

import json
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.agent.graph import GRAPH_TOOLS
from app.agent.sandbox import display_time
from app.agent.sandbox_panel import PROBE, cut_token, parse_denial
from app.models import PipelineEvent
from tests.fake_shell import DENIAL_LINES, FakeShell, line, probe_line
from tests.rig import Rig

OTHER = "x" * 43


def token_of(rig: Rig) -> str:
    env = rig.shell.sandboxes[f"claims-{rig.cid.lower()}"]["env"]
    return str(env["MCP_URL"].rsplit("/", 1)[1])


def with_log(rig: Rig) -> list[str]:
    """The sandbox has denied some requests: its log holds the lines, token whole."""
    lines = rig.shell.denial_lines(f"claims-{rig.cid.lower()}")
    rig.shell.log = lines
    return lines


def denied(rig: Rig) -> list[PipelineEvent]:
    return [e for e in rig.events("sandbox") if e.stage == "denied"]


def dump(events: list[PipelineEvent]) -> str:
    return json.dumps([e.model_dump(mode="json") for e in events])


# ---- the log lines -----------------------------------------------------------------


def test_a_denial_line_gives_what_and_why_and_no_token() -> None:
    tok = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-abcde"
    fmt = {"token": tok, "other": OTHER, "port": "50594"}
    parsed = [parse_denial(ln.format(**fmt)) for ln in DENIAL_LINES]
    # the listener, the allowed open and the warning are not denials
    assert [p is not None for p in parsed] == [
        False,
        True,
        False,
        True,
        False,
        True,
        True,
    ]
    refuse, opened, other_claim, other_tool = (p for p in parsed if p)
    assert (refuse.what, refuse.reason) == ("example.com", "policy_dns_ineligible")
    assert (opened.what, opened.kind) == ("example.com:443", "NET:OPEN")
    assert opened.reason == "transparent_tcp_policy_denied"
    assert other_claim.what == (
        "POST host.openshell.internal:50594/mcp/xxxxxx… (tools/call get_claim)"
    )
    assert other_tool.what == (
        "POST host.openshell.internal:50594/mcp/AbCdEf… (tools/call write_fraud_report)"
    )
    assert other_tool.reason == "MCP tool call has no matching allow rule; ask ..."
    assert other_tool.policy == "claims_gateway"
    assert tok not in json.dumps([p.model_dump(mode="json") for p in parsed if p])
    assert cut_token(f"/mcp/{tok}/x") == "/mcp/AbCdEf…/x"
    assert cut_token(cut_token(f"/mcp/{tok}")) == "/mcp/AbCdEf…"  # safe to repeat
    assert parse_denial("not a log line") is None


def test_a_key_in_a_denial_line_is_not_stored() -> None:
    raw = DENIAL_LINES[3] + " nvapi-0123456789abcdefghij"
    d = parse_denial(raw)
    assert d is None or "nvapi-0123" not in d.model_dump_json()  # no match is fine too
    from app.agent.sandbox_panel import clean

    assert "nvapi-" not in clean("key nvapi-0123456789abcdefghij here")


# ---- denials after a turn ----------------------------------------------------------


def test_each_denial_of_the_log_is_one_event_with_its_reason_and_no_token(
    make: Any,
) -> None:
    rig: Rig = make()
    lines = with_log(rig)
    rig.say()
    events = denied(rig)
    assert len(events) == 4  # the DENIED lines; not LISTEN, ALLOWED, or the warning
    assert [e.title for e in events][:2] == [
        "Denied: example.com",
        "Denied: example.com:443",
    ]
    assert events[1].outputs == {"reason": "transparent_tcp_policy_denied"}
    assert events[2].title.startswith(
        "Denied: POST host.openshell.internal:8000/mcp/xxxxxx"
    )
    assert all(e.actor == "sandbox" for e in events)
    assert token_of(rig) in "\n".join(lines)  # the log does hold the whole token
    assert token_of(rig) not in dump(rig.events())
    assert token_of(rig) not in rig.client.get(f"/claims/{rig.cid}").text


def test_a_denial_is_stored_once_and_a_new_one_later_is_stored_too(make: Any) -> None:
    rig: Rig = make()
    with_log(rig)
    rig.say()
    rig.say()
    assert len(denied(rig)) == 4  # two turns read the same four lines
    rig.shell.log.append(
        "[1794522400.100] [sandbox] [OCSF ] [ocsf] NET:OPEN [MED] DENIED "
        "/usr/local/bin/python3.12(0) -> github.com:443 "
        "[reason:transparent_tcp_policy_denied]"
    )
    rig.say()
    assert [e.title for e in denied(rig)][-1] == "Denied: github.com:443"
    assert len(denied(rig)) == 5


def test_the_event_time_of_a_denial_is_the_time_of_its_log_line(make: Any) -> None:
    rig: Rig = make()
    rig.shell.log = [
        "[1794522400.500] [sandbox] [OCSF ] [ocsf] NET:OPEN [MED] DENIED "
        "/usr/local/bin/python3.12(0) -> github.com:443 [reason:x]",
        # a clock that runs ahead cannot put an event in the future
        "[9994522400.500] [sandbox] [OCSF ] [ocsf] NET:OPEN [MED] DENIED "
        "/usr/local/bin/python3.12(0) -> later.example:443 [reason:x]",
    ]
    rig.say()
    first, second = denied(rig)
    assert first.time == datetime.fromtimestamp(1794522400.5, UTC)
    assert second.time <= datetime.now(UTC)


def test_a_log_that_cannot_be_read_does_not_fail_the_turn(make: Any) -> None:
    rig: Rig = make()
    rig.shell.fail_log = True
    rig.shell.turn = lambda s, n, a, i: line("Fine.")
    assert rig.say() == "Fine."
    assert denied(rig) == []
    assert rig.shell.log_reads >= 1
    rig.shell.fail_log = False
    with_log(rig)
    rig.say()
    assert len(denied(rig)) == 4  # the next turn reads them again


def test_the_denials_are_read_before_the_sandbox_is_deleted(make: Any) -> None:
    rig: Rig = make()
    with_log(rig)
    events_before = len(denied(rig))

    def close(s: FakeShell, name: str, argv: list[str], stdin: str) -> str:
        rig.store.update_claim(
            rig.cid, {"outcome": "straight_through", "status": "closed", "score": 0}
        )
        return line("Done.")

    rig.shell.turn = close
    rig.say("done")
    assert len(denied(rig)) == events_before + 4
    stages = rig.sandbox_stages()
    assert stages.index("denied") < stages.index("deleted")


# ---- the policy view ---------------------------------------------------------------


def test_a_claim_with_no_sandbox_has_no_sandbox_view_and_no_probe(make: Any) -> None:
    rig: Rig = make()
    other = rig.client.post("/claims", json={"customer_id": "C-1001"}).json()[
        "claim_id"
    ]
    assert rig.client.get(f"/claims/{other}/sandbox").status_code == 404
    assert rig.client.post(f"/claims/{other}/sandbox/probe").status_code == 404
    assert rig.client.get("/claims/CL-nope/sandbox").status_code == 404
    assert rig.client.post("/claims/CL-nope/sandbox/probe").status_code == 404


def test_the_sandbox_view_is_the_policy_of_this_claim_with_the_token_cut(
    make: Any,
) -> None:
    rig: Rig = make()
    res = rig.client.get(f"/claims/{rig.cid}/sandbox")
    assert res.status_code == 200
    view = res.json()
    token = token_of(rig)
    assert view["name"] == f"claims-{rig.cid.lower()}"
    assert view["state"] == "running"
    created = rig.events("sandbox")[0]
    assert created.duration is not None
    assert view["created_ms"] == round(created.duration * 1000)
    p = view["policy"]
    assert p["model"] == {
        "host": "integrate.api.nvidia.com",
        "port": 443,
        "method": "POST",
        "path": "/v1/chat/completions",
    }
    assert p["gateway"] == {
        "host": "host.openshell.internal",
        "port": 8000,
        "path": f"/mcp/{token[:6]}…",
        "methods": [
            "initialize",
            "notifications/initialized",
            "tools/list",
            "tools/call",
        ],
        "tools": list(GRAPH_TOOLS),
    }
    assert "write_fraud_report" not in p["gateway"]["tools"]
    assert "/sandbox" in p["read_write"] and "/tmp" in p["read_write"]
    assert "/usr" in p["read_only"] and "/sandbox" not in p["read_only"]
    assert (p["user"], p["landlock"]) == ("1500", "hard_requirement")
    assert token not in res.text


def test_the_view_says_deleted_when_the_claim_is_closed(make: Any) -> None:
    rig: Rig = make()

    def close(s: FakeShell, name: str, argv: list[str], stdin: str) -> str:
        rig.store.update_claim(
            rig.cid, {"outcome": "straight_through", "status": "closed", "score": 0}
        )
        return line("Done.")

    rig.shell.turn = close
    rig.say("done")
    assert rig.client.get(f"/claims/{rig.cid}/sandbox").json()["state"] == "deleted"


# ---- the probe ---------------------------------------------------------------------


def test_the_probe_runs_a_fixed_script_and_gives_five_denials(make: Any) -> None:
    rig: Rig = make()
    res = rig.client.post(f"/claims/{rig.cid}/sandbox/probe")
    assert res.status_code == 200
    out = res.json()
    assert out["confined"] is True
    assert [r["id"] for r in out["results"]] == [
        "other_host",
        "other_claim",
        "other_tool",
        "model_key",
        "host_files",
    ]
    assert all(r["denied"] for r in out["results"])
    assert out["results"][3]["detail"].endswith("NVIDIA_API_KEY")  # the placeholder
    # one event per try, then the denials the probe caused
    probes = [e for e in rig.events("sandbox") if e.stage == "probe"]
    assert [e.title for e in probes] == [
        "Probe: other host → denied",
        "Probe: other claim → denied",
        "Probe: tool outside the policy → denied",
        "Probe: model key → denied",
        "Probe: host files → denied",
    ]
    assert probes[0].outputs["denied"] is True
    assert len(denied(rig)) == 4
    last_probe = max(e.sequence for e in probes)
    assert min(e.sequence for e in denied(rig)) > last_probe
    assert token_of(rig) not in res.text + dump(rig.events())


def test_nothing_of_the_request_reaches_the_command(make: Any) -> None:
    rig: Rig = make()
    body = {"cmd": "rm -rf /", "text": "'; curl evil", "tool": "write_fraud_report"}
    res = rig.client.post(
        f"/claims/{rig.cid}/sandbox/probe?cmd=rm%20-rf&name=x", json=body
    )
    assert res.status_code == 200
    name, argv, stdin, timeout = [a for c, a in rig.shell.calls if c == "exec"][-1]
    assert name == f"claims-{rig.cid.lower()}"
    assert (argv, stdin) == (["python", "-"], PROBE)  # the constant, on stdin
    assert timeout == 60
    assert rig.shell.probes == [PROBE]
    # the script holds no f-string slot and no key: it reads its own environment
    assert "nvapi-" in PROBE and "{" in PROBE
    for ln in PROBE.splitlines():
        assert "nvapi-" not in ln or "startswith" in ln


def test_a_try_that_is_not_denied_is_said_plainly(make: Any) -> None:
    rig: Rig = make()
    rig.shell.probe_out = probe_line(
        other_host={"status": 200, "detail": "<!doctype html>"}
    )
    out = rig.client.post(f"/claims/{rig.cid}/sandbox/probe").json()
    assert out["confined"] is False
    [bad] = [r for r in out["results"] if not r["denied"]]
    assert bad["id"] == "other_host" and "200" in bad["detail"]
    titles = [e.title for e in rig.events("sandbox") if e.stage == "probe"]
    assert titles[0] == "Probe: other host → NOT denied"
    assert titles[1:] == [t for t in titles[1:] if t.endswith("→ denied")]
    first = [e for e in rig.events("sandbox") if e.stage == "probe"][0]
    assert first.outputs["denied"] is False


@pytest.mark.parametrize(
    "changes",
    [
        {"other_claim": {"status": 404, "detail": "not found"}},  # reached the backend
        {"other_tool": {"status": 200, "detail": "ok"}},
        {"other_host": {"error": "ConnectTimeout", "detail": "timed out"}},  # unproven
        {"model_key": {"has_real_key": True, "placeholder": None}},
        {"model_key": {"has_real_key": False, "placeholder": "something else"}},
        {"model_key": {"has_real_key": False, "placeholder": None}},
        {"host_files": {"/Users": {"count": 3}, "/host": {"error": "OSError"}}},
        {"host_files": {"/Users": {"error": "OSError"}}},
        {"other_host": "junk"},
        {"other_host": None},
    ],
)
def test_a_try_counts_as_denied_only_when_its_answer_proves_it(
    make: Any, changes: dict[str, Any]
) -> None:
    rig: Rig = make()
    rig.shell.probe_out = probe_line(**changes)
    out = rig.client.post(f"/claims/{rig.cid}/sandbox/probe").json()
    assert out["confined"] is False
    assert [r["denied"] for r in out["results"]].count(False) == 1


def test_a_probe_with_no_answer_is_a_502_and_one_failed_event(make: Any) -> None:
    rig: Rig = make()
    rig.shell.probe_out = "no json here\n"
    res = rig.client.post(f"/claims/{rig.cid}/sandbox/probe")
    assert res.status_code == 502
    assert "no JSON" in res.json()["detail"]
    failed = [e for e in rig.events("sandbox") if e.stage == "failed"]
    assert failed[-1].title == "Sandbox probe failed"
    rig.shell.probe_out = "{}\n"  # JSON with no result: nothing is proved
    out = rig.client.post(f"/claims/{rig.cid}/sandbox/probe").json()
    assert out["confined"] is False
    assert not any(r["denied"] for r in out["results"])


def test_the_probe_is_a_409_when_the_sandbox_is_not_running(make: Any) -> None:
    rig: Rig = make()

    def close(s: FakeShell, name: str, argv: list[str], stdin: str) -> str:
        rig.store.update_claim(
            rig.cid, {"outcome": "straight_through", "status": "closed", "score": 0}
        )
        return line("Done.")

    rig.shell.turn = close
    rig.say("done")
    assert rig.shell.sandboxes == {}
    assert rig.client.post(f"/claims/{rig.cid}/sandbox/probe").status_code == 409
    assert rig.shell.probes == []  # nothing ran


def test_the_probe_waits_for_a_turn_of_the_same_claim(make: Any) -> None:
    rig: Rig = make()
    lock = rig.client.app.state.locks[rig.cid]  # type: ignore[attr-defined]
    done: list[int] = []
    with lock:  # a turn is running
        t = threading.Thread(
            target=lambda: done.append(
                rig.client.post(f"/claims/{rig.cid}/sandbox/probe").status_code
            )
        )
        t.start()
        time.sleep(0.3)
        assert rig.shell.probes == [] and done == []  # waiting for the lock
    t.join(10)
    assert done == [200] and len(rig.shell.probes) == 1


def test_the_probe_collects_its_denials_when_the_log_is_slow(make: Any) -> None:
    """The supervisor log reaches the gateway about 0.3 s late: the probe reads again
    until the denials are there, for a few seconds at most."""
    rig: Rig = make()
    agent = rig.client.app.state.agent  # type: ignore[attr-defined]
    agent.denial_wait = 5.0
    full = rig.shell.denial_lines(f"claims-{rig.cid.lower()}")
    reads: list[int] = []

    def slow(name: str) -> list[str]:
        reads.append(1)
        return [] if len(reads) < 3 else full  # empty twice, then the lines

    rig.shell.log_lines = slow  # type: ignore[method-assign]
    rig.client.post(f"/claims/{rig.cid}/sandbox/probe")
    assert len(reads) == 3 and len(denied(rig)) == 4
    agent.denial_wait = 0.0
    reads.clear()
    rig.shell.log_lines = lambda name: []  # type: ignore[method-assign]
    started = time.monotonic()
    rig.client.post(f"/claims/{rig.cid}/sandbox/probe")
    assert time.monotonic() - started < 2  # it gives up at once, with no wait


# ---- the token never leaves the backend --------------------------------------------


def test_the_token_is_in_no_answer_and_no_event_of_a_sandbox_claim(
    make: Any, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("DEBUG")
    rig: Rig = make()
    with_log(rig)
    rig.say("hello")
    rig.client.post(f"/claims/{rig.cid}/sandbox/probe")
    token = token_of(rig)
    cid = rig.cid
    answers = [
        rig.client.get(f"/claims/{cid}"),
        rig.client.get(f"/claims/{cid}/customer"),
        rig.client.get(f"/claims/{cid}/messages"),
        rig.client.get(f"/claims/{cid}/policies"),
        rig.client.get(f"/claims/{cid}/sandbox"),
        rig.client.post(f"/claims/{cid}/sandbox/probe"),
        rig.client.get("/customers"),
        rig.client.get("/health"),
    ]
    assert all(a.status_code == 200 for a in answers)
    assert len(token) >= 43
    for answer in answers:
        assert token not in answer.text, answer.url
    # the events, as the stream serves them
    assert token not in dump(rig.events())
    assert token[:12] not in dump(rig.events())  # not even a longer piece
    assert token not in caplog.text
    # the short form of the sandbox view is there, and it is the only form
    assert f"/mcp/{token[:6]}…" in answers[4].text
    # the record of the store holds the hash only
    assert token not in rig.store.get_sandbox(cid).model_dump_json()


# ---- the order of the events -------------------------------------------------------


def test_display_time_is_the_clock_of_the_sandbox_kept_inside_the_turn() -> None:
    start = datetime(2026, 11, 12, 10, 0, 0, tzinfo=UTC)
    end = start + timedelta(seconds=5)
    at = start.timestamp()
    assert display_time(start, end, at + 2.5) == start + timedelta(seconds=2.5)
    # a clock that is wrong cannot put an event outside the time the backend waited
    assert display_time(start, end, at - 3600) == start
    assert display_time(start, end, at + 3600) == end
    # no usable number: the event keeps its arrival time
    for bad in (None, "1", True, False, 0, -1.0, float("nan"), float("inf"), 5e9):
        assert display_time(start, end, bad) is None


def test_the_events_of_a_turn_sort_by_the_time_the_sandbox_gave_them(
    make: Any,
) -> None:
    """The graph reports its events in one batch after the turn. Each one carries `at`,
    when it happened. Sorted by time, "Node: intake" comes before the tool call that
    the node made, and the model event after it."""
    rig: Rig = make()
    first = [e for e in rig.events()][-1].sequence

    def turn(s: FakeShell, name: str, argv: list[str], stdin: str) -> str:
        node_at = time.time()  # the node starts, then a tool call reaches the gateway
        time.sleep(0.2)
        rig.store.append_event(rig.cid, "tool", "get_claim_summary", "Get the claim")
        time.sleep(0.2)
        node = {"actor": "workflow", "stage": "intake", "title": "Node: intake"}
        llm = {"actor": "agent", "stage": "llm", "title": "Model writes a message"}
        events = [{**node, "at": node_at}, {**llm, "at": time.time()}]
        return json.dumps({"reply": "Hi.", "seconds": 0.4, "events": events}) + "\n"

    rig.shell.turn = turn
    rig.say()
    new = [e for e in rig.events() if e.sequence > first]
    by_sequence = [
        e.title for e in new if e.actor != "sandbox" and e.stage != "message"
    ]
    assert by_sequence == [  # the order they were stored in: the tool event first
        "Get the claim",
        "Node: intake",
        "Model writes a message",
    ]
    by_time = sorted(new, key=lambda e: (e.time, e.sequence))
    shown = [e.title for e in by_time if e.actor != "sandbox" and e.stage != "message"]
    assert shown == ["Node: intake", "Get the claim", "Model writes a message"]


def test_a_sandbox_with_a_wrong_clock_cannot_move_an_event_out_of_its_turn(
    make: Any,
) -> None:
    rig: Rig = make()
    node = {"actor": "workflow", "stage": "intake", "title": "Node: intake"}
    for wrong in (1.0, 3e9):  # 1970, and 2065

        def turn(s: FakeShell, n: str, a: list[str], i: str, w: float = wrong) -> str:
            return line("Hi.", [{**node, "at": w}])

        rig.shell.turn = turn
        before = datetime.now(UTC)
        rig.say()
        [e] = rig.events("workflow")[-1:]
        assert before <= e.time <= datetime.now(UTC)


def test_an_event_with_no_time_keeps_its_arrival_time(make: Any) -> None:
    rig: Rig = make()
    node = {"actor": "workflow", "stage": "intake", "title": "Node: intake"}
    rig.shell.turn = lambda s, n, a, i: line("Hi.", [node])  # no `at`, no `seconds`
    before = datetime.now(UTC)
    rig.say()
    [e] = [e for e in rig.events("workflow")][-1:]
    assert before <= e.time <= datetime.now(UTC)


def test_the_gateway_events_keep_their_own_time(make: Any) -> None:
    rig: Rig = make()
    stamp = datetime(2020, 1, 1, tzinfo=UTC)
    shown = rig.store.append_event(rig.cid, "workflow", "x", "X", at=stamp)
    assert shown.time == stamp
    own = rig.store.append_event(rig.cid, "tool", "y", "Y")
    assert own.time > datetime.now(UTC) - timedelta(seconds=5)
