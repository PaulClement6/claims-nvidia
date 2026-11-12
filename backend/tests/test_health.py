import pytest
from fastapi.testclient import TestClient

from app.main import app


def test_health() -> None:
    res = TestClient(app).get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "mode": "local", "agent_model": None}


def test_health_says_the_mode_and_the_agent_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # not in `local` mode: `nim` and `sandbox` have a model; `local` has none, even
    # when AGENT_MODEL is set in the shell
    monkeypatch.setenv("AGENT_MODEL", "nvidia/some-model")
    assert TestClient(app).get("/health").json()["agent_model"] is None
    monkeypatch.setenv("CLAIMS_MODE", "nim")
    assert TestClient(app).get("/health").json() == {
        "status": "ok",
        "mode": "nim",
        "agent_model": "nvidia/some-model",
    }
