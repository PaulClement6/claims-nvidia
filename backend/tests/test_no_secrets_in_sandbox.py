"""Non-negotiable 6: no API key ever enters the sandbox (issue #14).

Three places: the image (its build context), the sandbox environment the runner asks
for, and, with a real sandbox (`pytest -m sandbox`), the process environment inside it.
The key is read only to look for it. It is never printed: a failure names a file.
"""

import json
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.agent.sandbox_image import INFRA, build_context
from app.main import create_app
from tests.fake_shell import FakeShell, line, use_fake_shell
from tests.live_claims import load_env

KEY = "nvapi-0123456789abcdefghijklmnopqrstuvwxyz-TESTKEY"


def real_key() -> str | None:
    return load_env().get("NVIDIA_KEY") or None


def test_the_build_context_holds_no_env_file_and_no_key(tmp_path: Path) -> None:
    ctx = tmp_path / "ctx"
    build_context(ctx)
    files = [p for p in ctx.rglob("*") if p.is_file()]
    assert files
    assert not [p.name for p in files if p.name.startswith(".env")]
    assert not [p.name for p in files if "nvapi-" in p.read_text()]
    secret = real_key()  # the value of this machine, looked for and never printed
    if secret:
        assert not [p.name for p in files if secret.encode() in p.read_bytes()]


def test_nothing_of_the_infra_folder_holds_a_key() -> None:
    for path in INFRA.rglob("*"):
        if path.is_file():
            assert "nvapi-" not in path.read_text(), path.name


def test_setup_gives_the_key_to_the_provider_by_the_environment_only() -> None:
    script = (INFRA / "setup.sh").read_text()
    # `provider create` reads NVIDIA_API_KEY from its own environment: the value is
    # never a command argument (`--credential NAME=value` would be one)
    assert re.search(r'NVIDIA_API_KEY="\$key" openshell provider create', script)
    assert not re.search(r"--credential\s+\S+=", script)
    assert "echo" not in "\n".join(
        ln
        for ln in script.splitlines()
        if "key" in ln and not ln.lstrip().startswith("#")
    )


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CLAIMS_DB", str(tmp_path / "claims.db"))
    monkeypatch.setenv("CLAIMS_UPLOADS", str(tmp_path / "uploads"))
    monkeypatch.setenv(
        "NVIDIA_KEY", KEY
    )  # the backend may have it: the sandbox may not
    monkeypatch.setenv("NVIDIA_API_KEY", KEY)
    shell = use_fake_shell(monkeypatch, FakeShell(), tmp_path)
    shell.turn = lambda s, n, a, i: line("Hello.")
    with TestClient(create_app()) as c:
        c.app.state.shell = shell  # type: ignore[attr-defined]
        yield c


def test_the_environment_the_runner_passes_holds_no_key(client: TestClient) -> None:
    shell: FakeShell = client.app.state.shell  # type: ignore[attr-defined]
    cid = client.post("/claims", json={"customer_id": "C-1001"}).json()["claim_id"]
    client.put(f"/claims/{cid}/policy", json={"policy_number": "TRV-2026-0417"})
    client.post(f"/claims/{cid}/messages", json={"text": "hello"})
    [create] = [a for c, a in shell.calls if c == "create"]
    env = create[4]
    assert KEY not in json.dumps(env) and "nvapi-" not in json.dumps(env)
    assert not [k for k in env if "KEY" in k.upper()]  # not even a placeholder name
    assert KEY not in json.dumps(shell.calls, default=str)  # no argument, no stdin
    events = client.app.state.store.events_since(cid, 0)  # type: ignore[attr-defined]
    assert KEY not in json.dumps([e.model_dump(mode="json") for e in events])
    assert KEY not in shell.sandboxes[create[0]]["policy"]


def test_the_model_provider_is_the_only_holder_of_the_key() -> None:
    profile = (INFRA / "profile-nim.yaml").read_text()
    assert "env_vars: [NVIDIA_API_KEY]" in profile
    assert "integrate.api.nvidia.com" in profile
    assert "nvapi-" not in profile
