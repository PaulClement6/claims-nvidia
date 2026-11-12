"""The three claim files end to end with the LLM agent and a fake model (no key).

The fake plays a good model (`tests/fake_chat.py::good_model`); the same `run_claim`
helpers drive the scripted agent in the other test files.
"""

import logging
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.views import TO_A_PERSON, VALIDATED
from tests import test_health_api, test_property_api, test_travel_api
from tests.fake_chat import use_fake_model


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CLAIMS_DB", str(tmp_path / "claims.db"))
    monkeypatch.setenv("CLAIMS_UPLOADS", str(tmp_path / "uploads"))
    use_fake_model(monkeypatch)
    with TestClient(create_app()) as c:
        yield c


RUNS: list[tuple[Callable[[TestClient], str], str, int, str]] = [
    (test_travel_api.run_claim, "straight_through", 0, VALIDATED),
    (test_property_api.run_claim, "handler_review", 0, TO_A_PERSON),
    (test_health_api.run_claim, "investigation", 130, TO_A_PERSON),
]


@pytest.mark.parametrize(
    ("run", "outcome", "score", "text"), RUNS, ids=["travel", "property", "health"]
)
def test_each_claim_file_gives_its_outcome_and_the_exact_customer_text(
    client: TestClient,
    run: Callable[[TestClient], str],
    outcome: str,
    score: int,
    text: str,
) -> None:
    cid = run(client)
    c: dict[str, Any] = client.get(f"/claims/{cid}").json()
    assert (c["outcome"], c["score"]) == (outcome, score)
    assert (c["fraud_report"] is not None) is (outcome == "investigation")
    assert c["agent_state"] == "closed"
    last = client.get(f"/claims/{cid}/messages").json()[-1]
    assert last["role"] == "agent" and last["text"] == text  # word for word
    assert client.get(f"/claims/{cid}/customer").json()["message"] == text


def test_the_events_show_the_model_the_tools_and_the_routing(
    client: TestClient,
) -> None:
    cid = test_travel_api.run_claim(client)
    events = client.app.state.store.events_since(cid, 0)  # type: ignore[attr-defined]
    llm = [e for e in events if e.stage == "llm"]
    assert llm and all(e.model == "fake-model" and e.duration is not None for e in llm)
    tools = [e.stage for e in events if e.actor == "tool"]
    assert tools.count("assess_and_route") == 1
    assert tools.index("assess_and_route") > tools.index("check_consistency")
    for name in ("classify_document", "extract_document"):
        assert tools.count(name) == 3


def test_medical_text_never_reaches_the_application_logs(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    cid = test_health_api.run_claim(client)
    assert caplog.records, "the capture works"
    logged = " ".join(r.getMessage() for r in caplog.records).lower()
    for word in ("sprained", "jonas", "ankle", "lukas", "physiotherapy"):
        assert word not in logged
    events = client.app.state.store.events_since(cid, 0)  # type: ignore[attr-defined]
    assert "sprained" in " ".join(str(e.outputs) for e in events)  # events hold it
