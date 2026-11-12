"""The tool gateway as an MCP server (issue #14), called as the sandbox calls it:
JSON-RPC over POST on `/mcp/<token>`. The claim is fixed by the token; every call
writes one event; the guard of rule 1 holds; the token is never logged."""

import hmac
import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agent.graph import GRAPH_TOOLS
from app.main import create_app
from app.models import Document, SandboxRecord
from app.tools.mcp_server import RedactToken, hash_token
from tests.fake_shell import FakeShell, use_fake_shell

HEADERS = {"Accept": "application/json, text/event-stream"}
TOKEN = "t0ken-of-the-first-claim-abcdefghijklmnopqrstuv"
OTHER = "t0ken-of-the-second-claim-abcdefghijklmnopqrstu"


class Rig:
    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.store = client.app.state.store  # type: ignore[attr-defined]
        self.first = self.claim(TOKEN)
        self.second = self.claim(OTHER)
        self.next_id = 0

    def claim(self, token: str, closed: bool = False) -> str:
        claim_id = str(
            self.client.post("/claims", json={"customer_id": "C-1001"}).json()[
                "claim_id"
            ]
        )
        self.store.save_sandbox(
            SandboxRecord(
                claim_id=claim_id,
                name=f"claims-{claim_id.lower()}",
                token_sha256=hash_token(token),
                policy_file="x",
                created=datetime.now(UTC),
                closed=closed,
            )
        )
        return claim_id

    def rpc(self, token: str, method: str, params: dict[str, Any] | None = None) -> Any:
        self.next_id += 1
        return self.client.post(
            f"/mcp/{token}",
            json={
                "jsonrpc": "2.0",
                "id": self.next_id,
                "method": method,
                "params": params,
            },
            headers=HEADERS,
        )

    def call(self, token: str, tool: str, args: dict[str, Any] | None = None) -> Any:
        res = self.rpc(token, "tools/call", {"name": tool, "arguments": args or {}})
        assert res.status_code == 200, res.text
        return res.json()["result"]

    def add_document(self, claim_id: str, path: Path) -> str:
        """Stored like an upload, without a turn of the agent."""
        doc_id = f"D-{path.stem[:6]}{claim_id[-4:]}"
        doc = Document(id=doc_id, file=f"{claim_id}/{doc_id}.pdf", pages=1)
        target: Path = self.client.app.state.uploads / doc.file  # type: ignore[attr-defined]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
        self.store.save_document(claim_id, doc)
        return doc_id

    def tool_events(self, claim_id: str) -> list[Any]:
        return [e for e in self.store.events_since(claim_id, 0) if e.actor == "tool"]


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Rig]:
    monkeypatch.setenv("CLAIMS_DB", str(tmp_path / "claims.db"))
    monkeypatch.setenv("CLAIMS_UPLOADS", str(tmp_path / "uploads"))
    use_fake_shell(monkeypatch, FakeShell(), tmp_path)
    with TestClient(create_app()) as client:
        yield Rig(client)


def test_an_unknown_or_closed_token_or_path_gets_404(rig: Rig) -> None:
    closed = "t0ken-closed-abcdefghijklmnopqrstuvwxyz0123456"
    rig.claim(closed, closed=True)
    for path in (
        "/mcp/nope",
        f"/mcp/{closed}",
        f"/mcp/{TOKEN}/more",
        f"/mcp/{TOKEN[:-1]}",
        "/mcp/",
    ):
        res = rig.client.post(
            path,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers=HEADERS,
        )
        assert res.status_code == 404, path
    assert rig.rpc(TOKEN, "tools/list").status_code == 200


def test_other_modes_have_no_mcp_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAIMS_DB", str(tmp_path / "claims.db"))
    with TestClient(create_app()) as client:  # local mode
        res = client.post(f"/mcp/{TOKEN}", json={}, headers=HEADERS)
        assert res.status_code == 404


def test_the_tool_list_is_the_tools_of_the_graph_with_no_claim_id(rig: Rig) -> None:
    res = rig.rpc(TOKEN, "tools/list")
    tools = {t["name"]: t for t in res.json()["result"]["tools"]}
    assert sorted(tools) == sorted(GRAPH_TOOLS)  # code nodes call them too
    assert "write_fraud_report" not in tools
    assert "get_customer_policies" not in tools
    for tool in tools.values():
        assert "claim_id" not in tool["inputSchema"].get("properties", {})
        assert tool["description"]
    specs = rig.client.app.state.gateway.tool_specs()  # type: ignore[attr-defined]
    for spec in specs:
        f = spec["function"]
        assert tools[f["name"]]["inputSchema"] == f["parameters"]
    # the model must not see agent_state: the schema of update_claim is the usual one
    assert "agent_state" not in tools["update_claim"]["inputSchema"]["properties"]


def test_a_call_runs_for_the_claim_of_the_token_and_writes_one_tool_event(
    rig: Rig,
) -> None:
    result = rig.call(TOKEN, "get_claim_summary")
    assert result["isError"] is False
    summary = json.loads(result["content"][0]["text"])
    assert summary["customer_name"] == "Anna Keller"
    [event] = rig.tool_events(rig.first)
    assert (event.stage, event.outputs["customer_name"]) == (
        "get_claim_summary",
        "Anna Keller",
    )
    assert rig.tool_events(rig.second) == []


def test_the_agent_cannot_name_another_claim(rig: Rig) -> None:
    rig.call(TOKEN, "update_claim", {"claim_id": rig.second, "description": "mine"})
    assert rig.store.get_claim(rig.first).description == "mine"
    assert rig.store.get_claim(rig.second).description is None
    [event] = rig.tool_events(rig.first)
    assert event.inputs["claim_id"] == rig.first
    assert rig.tool_events(rig.second) == []


def test_each_token_reaches_its_own_claim_only(rig: Rig) -> None:
    rig.call(TOKEN, "update_claim", {"description": "first"})
    rig.call(OTHER, "update_claim", {"description": "second"})
    assert rig.store.get_claim(rig.first).description == "first"
    assert rig.store.get_claim(rig.second).description == "second"


def test_a_document_of_another_claim_is_unknown(rig: Rig) -> None:
    from tests.test_travel_api import CLAIM1

    doc = rig.add_document(rig.second, CLAIM1 / "booking-confirmation.pdf")
    result = rig.call(TOKEN, "extract_document", {"document_id": doc})
    assert result["isError"] is True
    assert "unknown document" in result["content"][0]["text"]
    [event] = rig.tool_events(rig.first)  # a failed call writes its event too
    assert "error" in event.outputs


def test_a_tool_that_is_not_listed_is_not_callable(rig: Rig) -> None:
    for tool in ("write_fraud_report", "get_customer_policies", "nope"):
        result = rig.call(TOKEN, tool, {"customer_id": "C-1001"})
        assert result["isError"] is True
        assert "unknown tool" in result["content"][0]["text"]
    assert rig.tool_events(rig.first) == []  # the gateway was not reached


def test_the_code_nodes_can_set_the_chat_step_through_mcp(rig: Rig) -> None:
    """`agent_state` is not in the schema the model sees, so the SDK must not check
    the input against it: the graph sets it in code."""
    result = rig.call(TOKEN, "update_claim", {"agent_state": "ready_for_checks"})
    assert result["isError"] is False
    assert rig.store.get_claim(rig.first).agent_state == "ready_for_checks"


def test_an_unknown_argument_fails_in_the_tool_and_writes_an_error_event(
    rig: Rig,
) -> None:
    result = rig.call(TOKEN, "update_claim", {"colour": "red"})
    assert result["isError"] is True
    [event] = rig.tool_events(rig.first)
    assert "error" in event.outputs


def test_the_guard_refuses_assess_and_route_through_mcp_too(rig: Rig) -> None:
    result = rig.call(TOKEN, "assess_and_route")
    assert result["isError"] is True
    assert "story is not complete" in result["content"][0]["text"]
    assert all(e.stage != "assess_and_route" for e in rig.tool_events(rig.first))
    refused = [e for e in rig.store.events_since(rig.first, 0) if e.stage == "refused"]
    assert len(refused) == 1 and refused[0].actor == "agent"
    assert rig.store.get_claim(rig.first).outcome is None


def test_the_checks_and_the_routing_run_through_mcp(rig: Rig) -> None:
    """What the code nodes `checks` and `route` call, in the order of the graph."""
    from tests.test_travel_api import CLAIM1

    claim = rig.first
    rig.store.set_claim_policy(claim, "TRV-2026-0417")
    rig.call(
        TOKEN,
        "update_claim",
        {
            "description": "My trip was cancelled because I was ill.",
            "incident_date": "2026-08-03",
            "claimed_amount": 1500,
            "cause": "illness",
        },
    )
    for name in (
        "booking-confirmation",
        "cancellation-confirmation",
        "medical-certificate",
    ):
        doc = rig.add_document(claim, CLAIM1 / f"{name}.pdf")
        rig.call(TOKEN, "classify_document", {"document_id": doc})
        rig.call(TOKEN, "extract_document", {"document_id": doc})
    for tool in ("run_checks", "check_tampering", "check_consistency"):
        assert rig.call(TOKEN, tool)["isError"] is False
    routed = rig.call(TOKEN, "assess_and_route")
    assert routed["isError"] is False
    text = json.loads(routed["content"][0]["text"])
    assert list(text) == ["customer_message"]  # nothing but the text
    assert rig.store.get_claim(claim).outcome.value == "straight_through"


def test_the_token_is_checked_against_every_open_sandbox_in_constant_time(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, str]] = []
    real = hmac.compare_digest

    def spy(a: str, b: str) -> bool:
        seen.append((a, b))
        return real(a, b)

    monkeypatch.setattr(hmac, "compare_digest", spy)
    assert rig.client.app.state.mcp.claim_for_token(TOKEN) == rig.first  # type: ignore[attr-defined]
    assert len(seen) == 2  # the match is the first one, and the second is still checked
    assert {b for _, b in seen} == {hash_token(TOKEN)}
    assert all(a != TOKEN for a, _ in seen)  # only hashes are stored and compared


def test_the_token_is_not_stored_and_not_in_any_log_or_event(
    rig: Rig, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    rig.call(TOKEN, "get_claim_summary")
    rig.rpc("wrong-token-wrong-token-wrong-token-wrong-token-1", "tools/list")
    # the HTTP client of the test (httpx) logs its own request: it is not the server
    server = "\n".join(
        r.getMessage() for r in caplog.records if not r.name.startswith("httpx")
    )
    assert server and TOKEN not in server and "wrong-token" not in server
    assert TOKEN not in rig.store.get_sandbox(rig.first).model_dump_json()
    assert TOKEN not in json.dumps(
        [e.model_dump(mode="json") for e in rig.store.events_since(rig.first, 0)]
    )


def test_the_access_log_of_uvicorn_never_shows_the_token() -> None:
    """uvicorn logs `POST /mcp/<token> HTTP/1.1`: the filter cuts the token."""
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, "", 0,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "POST", f"/mcp/{TOKEN}", "1.1", 200), None,
    )  # fmt: skip
    assert TOKEN in record.getMessage()
    RedactToken().filter(record)
    assert TOKEN not in record.getMessage()
    assert "POST /mcp/<token> HTTP/1.1" in record.getMessage()
    other = logging.LogRecord(
        "uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", "/claims/CL-1", "1.1", 200), None,
    )  # fmt: skip
    RedactToken().filter(other)
    assert "/claims/CL-1" in other.getMessage()


def test_the_filter_is_on_the_uvicorn_access_log_in_sandbox_mode(rig: Rig) -> None:
    filters = logging.getLogger("uvicorn.access").filters
    assert any(isinstance(f, RedactToken) for f in filters)
