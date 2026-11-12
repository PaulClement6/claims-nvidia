"""The three claim files in `sandbox` mode, with no OpenShell and no model (issue #14).

A real backend (uvicorn, on a free port) and the real turn process code
(`app.agent.sandbox_turn.run_turn`): the graph calls its tools over MCP, on
`/mcp/<token>`, with the real MCP client. Only two things are fake: `openshell`
(its `exec` runs the turn in this process, with the host name rewritten to the
loopback) and the model (the fake of the graph tests). So the code nodes `checks`
and `route` are checked through MCP too, and the guard, the token and the events.
"""

import json
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from app.agent.graph import GRAPH_TOOLS
from app.agent.sandbox_turn import run_turn
from app.main import create_app
from app.views import TO_A_PERSON, VALIDATED
from tests import test_health_api, test_property_api, test_travel_api
from tests.fake_model import FakeModel, good_model
from tests.fake_shell import FakeShell, use_fake_shell


class Live:
    def __init__(self, client: httpx.Client, shell: FakeShell, store: Any) -> None:
        self.client, self.shell, self.store = client, shell, store


@pytest.fixture
def live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Live]:
    monkeypatch.setenv("CLAIMS_DB", str(tmp_path / "claims.db"))
    monkeypatch.setenv("CLAIMS_UPLOADS", str(tmp_path / "uploads"))
    shell = use_fake_shell(monkeypatch, FakeShell(), tmp_path)
    model = FakeModel(policy=good_model)

    app = create_app()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)

    def turn(s: FakeShell, name: str, argv: list[str], stdin: str) -> str:
        url = s.sandboxes[name]["env"]["MCP_URL"].replace(
            "host.openshell.internal", "127.0.0.1"
        )
        out = run_turn(
            argv[-1], json.loads(stdin), model, url, str(tmp_path / f"{name}.sqlite")
        )
        return json.dumps(out) + "\n"

    shell.turn = turn
    # the port is only known when the server runs: the runner reads BACKEND_PORT at
    # startup, so bind a port first
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server.config.port = port
    monkeypatch.setenv("BACKEND_PORT", str(port))
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    assert server.started
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=60) as client:
        yield Live(client, shell, app.state.store)
    server.should_exit = True
    thread.join(10)


RUNS: list[tuple[Callable[[TestClient], str], str, int, str]] = [
    (test_travel_api.run_claim, "straight_through", 0, VALIDATED),
    (test_property_api.run_claim, "handler_review", 0, TO_A_PERSON),
    (test_health_api.run_claim, "investigation", 130, TO_A_PERSON),
]


@pytest.mark.parametrize(
    ("run", "outcome", "score", "text"), RUNS, ids=["travel", "property", "health"]
)
def test_each_claim_file_gives_its_outcome_through_the_mcp_gateway(
    live: Live,
    run: Callable[[TestClient], str],
    outcome: str,
    score: int,
    text: str,
) -> None:
    cid = run(live.client)  # type: ignore[arg-type]
    c: dict[str, Any] = live.client.get(f"/claims/{cid}").json()
    assert (c["outcome"], c["score"]) == (outcome, score)
    assert (c["fraud_report"] is not None) is (outcome == "investigation")
    assert c["agent_state"] == "closed"
    last = live.client.get(f"/claims/{cid}/messages").json()[-1]
    assert last["role"] == "agent" and last["text"] == text  # word for word
    assert live.client.get(f"/claims/{cid}/customer").json()["message"] == text
    events = live.store.events_since(cid, 0)
    # the trusted events: the gateway wrote them when the MCP calls arrived
    tools = {e.stage for e in events if e.actor == "tool"}
    assert tools <= set(GRAPH_TOOLS) | {"write_fraud_report"}
    assert {
        "run_checks",
        "check_tampering",
        "check_consistency",
        "assess_and_route",
    } <= tools
    # the sandbox life cycle, and the display-only events of the graph
    sandbox = [e.stage for e in events if e.actor == "sandbox"]
    assert sandbox[0] == "created" and sandbox[-1] == "deleted"
    assert sandbox.count("created") == 1 and "turn" in sandbox
    nodes = [e.stage for e in events if e.actor == "workflow"]
    assert nodes[0] == "intake" and nodes[-3:] == ["checks", "route", "answer"]
    assert all(
        e.inputs["source"] == "sandbox"
        for e in events
        if e.actor in ("workflow", "agent") and e.stage != "message"
    )
    assert live.shell.sandboxes == {}  # deleted when the claim closed


def test_the_graph_only_calls_tools_the_policy_allows(live: Live) -> None:
    """Every tool the graph called is in the rendered policy, and each tool of the
    policy was needed by one of the three claim files."""
    used: set[str] = set()
    for run in (
        test_travel_api.run_claim,
        test_property_api.run_claim,
        test_health_api.run_claim,
    ):
        cid = run(live.client)  # type: ignore[arg-type]
        used |= {e.stage for e in live.store.events_since(cid, 0) if e.actor == "tool"}
    allowed = set(GRAPH_TOOLS)
    assert used - {"write_fraud_report"} <= allowed
    assert allowed <= used  # no tool in the policy that the graph never calls


def test_the_events_of_a_real_turn_sort_into_the_order_they_happened(
    live: Live,
) -> None:
    """The graph runs in one process and reports its node events after the turn, while
    the gateway wrote its tool events at once. Sorted by display time, a node comes
    before the tool calls it made, and the model event of the turn after them."""
    cid = test_travel_api.run_claim(live.client)  # type: ignore[arg-type]
    events = live.store.events_since(cid, 0)
    shown = [e for e in events if e.actor != "sandbox" and e.stage != "message"]
    by_time = sorted(shown, key=lambda e: (e.time, e.sequence))
    stages = [(e.actor, e.stage) for e in by_time]
    # the greeting turn: the node intake, then its two tools
    assert stages[0] == ("workflow", "intake")
    assert stages[1][0] == "tool"
    first_tool = next(e for e in shown if e.actor == "tool")
    intake = next(e for e in shown if e.stage == "intake")
    assert intake.sequence > first_tool.sequence  # stored after: the problem of #14
    assert intake.time < first_tool.time  # and shown before
    # a node is never shown after a tool call of the next node: every `checks` node
    # event comes before the run_checks tool event
    checks = next(e for e in shown if e.stage == "checks" and e.actor == "workflow")
    run_checks = next(e for e in shown if e.stage == "run_checks")
    assert checks.time < run_checks.time
    assert all(e.time <= by_time[-1].time for e in by_time)
