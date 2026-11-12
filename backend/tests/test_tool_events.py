"""Non-negotiable 5: every tool call writes exactly one event."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.store import Store
from app.tools.gateway import Gateway
from tests import test_travel_api
from tests.fake_chat import use_fake_model


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(str(tmp_path / "claims.db"))


def test_one_call_one_event_with_everything(store: Store) -> None:
    gateway = Gateway(store)
    gateway.register("add", lambda a, b: {"sum": a + b}, model="mock-model")

    out = gateway.call("CL-1", "add", {"a": 1, "b": 2})

    assert out == {"sum": 3}
    [event] = store.events_since("CL-1", 0)
    assert event.sequence == 1
    assert event.actor == "tool"
    assert event.stage == "add"
    assert event.title
    assert event.inputs == {"a": 1, "b": 2}
    assert event.outputs == {"sum": 3}
    assert event.model == "mock-model"
    assert event.duration is not None and event.duration >= 0


def test_model_is_none_when_not_given(store: Store) -> None:
    gateway = Gateway(store)
    gateway.register("noop", lambda: {})
    gateway.call("CL-1", "noop", {})
    assert store.events_since("CL-1", 0)[0].model is None


def test_failing_tool_gives_one_event_and_the_error_goes_up(store: Store) -> None:
    gateway = Gateway(store)

    def boom(x: int) -> dict[str, int]:
        raise ValueError("bad input")

    gateway.register("boom", boom, model="m")

    with pytest.raises(ValueError, match="bad input"):
        gateway.call("CL-1", "boom", {"x": 1})

    [event] = store.events_since("CL-1", 0)
    assert event.stage == "boom"
    assert event.inputs == {"x": 1}
    assert event.outputs == {"error": "bad input"}
    assert event.model == "m"
    assert event.duration is not None


def test_unknown_tool_gives_one_event(store: Store) -> None:
    gateway = Gateway(store)

    with pytest.raises(KeyError):
        gateway.call("CL-1", "nope", {"a": 1})

    [event] = store.events_since("CL-1", 0)
    assert event.stage == "nope"
    assert event.inputs == {"a": 1}
    assert "unknown tool" in event.outputs["error"]


def test_every_tool_call_of_the_llm_agent_is_one_event_with_the_claim_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAIMS_DB", str(tmp_path / "claims.db"))
    monkeypatch.setenv("CLAIMS_UPLOADS", str(tmp_path / "uploads"))
    use_fake_model(monkeypatch)
    with TestClient(create_app()) as client:
        gateway = client.app.state.gateway  # type: ignore[attr-defined]
        calls: list[tuple[str, dict[str, object]]] = []
        real = gateway.call

        def spy(claim_id: str, tool: str, inputs: dict[str, object]) -> object:
            calls.append((tool, inputs))
            return real(claim_id, tool, inputs)

        monkeypatch.setattr(gateway, "call", spy)
        cid = test_travel_api.run_claim(client)

        events = client.app.state.store.events_since(cid, 0)  # type: ignore[attr-defined]
        tool_events = [e for e in events if e.actor == "tool"]
        assert [e.stage for e in tool_events] == [t for t, _ in calls]
        assert [e.inputs for e in tool_events] == [i for _, i in calls]
        assert all(
            e.inputs["claim_id"] == cid for e in tool_events if "claim_id" in e.inputs
        )
        assert {"classify_document", "extract_document", "assess_and_route"} <= {
            e.stage for e in tool_events
        }
        assert all(e.duration is not None for e in tool_events)
        assert next(e for e in tool_events if e.stage == "extract_document").model
