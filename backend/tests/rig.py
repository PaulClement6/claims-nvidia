"""A claim in `sandbox` mode with the fake `openshell`: `Rig` and the `make` fixture
(`tests/conftest.py` shares it). Used by the sandbox tests."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import PipelineEvent
from tests.fake_shell import FakeShell, use_fake_shell


class Rig:
    def __init__(self, client: TestClient, shell: FakeShell, tmp_path: Path) -> None:
        self.client, self.shell, self.tmp = client, shell, tmp_path
        self.store = client.app.state.store  # type: ignore[attr-defined]
        self.cid = ""

    def start(self) -> "Rig":
        """A claim with its policy: the greeting is the first turn."""
        self.cid = self.client.post("/claims", json={"customer_id": "C-1001"}).json()[
            "claim_id"
        ]
        res = self.client.put(
            f"/claims/{self.cid}/policy", json={"policy_number": "TRV-2026-0417"}
        )
        assert res.status_code == 200
        return self

    def say(self, text: str = "Hello") -> str:
        res = self.client.post(f"/claims/{self.cid}/messages", json={"text": text})
        assert res.status_code == 200
        return str(res.json()[-1]["text"])

    def events(self, actor: str | None = None) -> list[PipelineEvent]:
        found: list[PipelineEvent] = self.store.events_since(self.cid, 0)
        return [e for e in found if actor is None or e.actor == actor]

    def sandbox_stages(self) -> list[str]:
        return [e.stage for e in self.events("sandbox")]


@pytest.fixture
def make(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    clients: list[Any] = []

    def build(shell: FakeShell | None = None) -> Rig:
        monkeypatch.setenv("CLAIMS_DB", str(tmp_path / "claims.db"))
        monkeypatch.setenv("CLAIMS_UPLOADS", str(tmp_path / "uploads"))
        fake = use_fake_shell(monkeypatch, shell or FakeShell(), tmp_path)
        cm = TestClient(create_app())
        client = cm.__enter__()
        clients.append(cm)
        return Rig(client, fake, tmp_path).start()

    yield build
    for cm in clients:
        cm.__exit__(None, None, None)
