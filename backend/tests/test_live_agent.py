"""The real model, through the HTTP API. Skipped in CI: `uv run pytest -m live`.

Reads NVIDIA_KEY and the endpoint settings from `.env` at the repository root (or from
the environment). Mock data only. Each test is one claim file with a customer's own
words, and the exact outcome and customer text it must end with.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.live_claims import CLAIM_FILES, load_env, play

pytestmark = pytest.mark.live


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    env = load_env()
    for name in ("NVIDIA_KEY", "AGENT_BASE_URL", "AGENT_MODEL", "AGENT_THINKING"):
        if name in env and name not in os.environ:  # the shell wins over `.env`
            monkeypatch.setenv(name, env[name])
    monkeypatch.setenv("CLAIMS_MODE", "nim")
    monkeypatch.setenv("CLAIMS_DB", str(tmp_path / "claims.db"))
    monkeypatch.setenv("CLAIMS_UPLOADS", str(tmp_path / "uploads"))
    with TestClient(create_app()) as c:  # no key: the startup fails, the test too
        yield c


@pytest.mark.parametrize("kind", list(CLAIM_FILES))
def test_the_real_model_runs_the_claim_file_to_its_outcome(
    client: TestClient, kind: str
) -> None:
    run = play(client, kind, variant=0)
    assert run.error is None, run.error
    assert run.outcome == CLAIM_FILES[kind].outcome, run.transcript
    assert run.customer_text == CLAIM_FILES[kind].customer_text  # word for word
    assert run.fields_ok
    assert run.closed
