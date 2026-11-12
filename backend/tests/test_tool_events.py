"""Non-negotiable 5: every tool call writes exactly one event."""

from pathlib import Path

import pytest

from app.store import Store
from app.tools.gateway import Gateway


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
