"""`CLAIMS_MODE=sandbox` on a machine with OpenShell (issue #14). Skipped in CI:

    cd backend && uv run pytest -m sandbox

Needs Colima, OpenShell 0.1.5 and `infra/sandbox/setup.sh` run once (the image and the
provider with NVIDIA_KEY). Calls the real model through the provider. The key never
reaches this process: the backend of the test holds only AGENT_BASE_URL and AGENT_MODEL
(read from `.env`; the shell wins). Mock data only. The backend runs in a thread on a
free loopback port, because the sandbox reaches it as host.openshell.internal.
"""

import json
import os
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from app.agent.sandbox import OpenShell, SandboxAgent
from app.main import create_app
from tests.live_claims import CLAIM_FILES, load_env, play

pytestmark = pytest.mark.sandbox


class LiveClient(httpx.Client):
    """The HTTP client of `live_claims.play`, with the `app` of a TestClient."""

    def __init__(self, app: FastAPI, port: int) -> None:
        super().__init__(base_url=f"http://127.0.0.1:{port}", timeout=300)
        self.app = app


@pytest.fixture
def backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[LiveClient]:
    env = load_env()
    for name in ("AGENT_BASE_URL", "AGENT_MODEL", "AGENT_THINKING"):
        if name in env and name not in os.environ:  # the shell wins over `.env`
            monkeypatch.setenv(name, env[name])
    monkeypatch.delenv("NVIDIA_KEY", raising=False)  # the provider holds it
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    monkeypatch.setenv("CLAIMS_MODE", "sandbox")
    monkeypatch.setenv("BACKEND_PORT", str(port))
    monkeypatch.setenv("CLAIMS_DB", str(tmp_path / "claims.db"))
    monkeypatch.setenv("CLAIMS_UPLOADS", str(tmp_path / "uploads"))
    monkeypatch.setenv("CLAIMS_SANDBOX_DIR", str(tmp_path / "sandboxes"))
    app = create_app()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.1)
    assert server.started, "the backend did not start (is setup.sh done?)"
    client = LiveClient(app, port)
    try:
        yield client
    finally:
        client.post("/demo/reset")  # deletes the sandboxes of this test, and only those
        client.close()
        server.should_exit = True
        thread.join(15)


@pytest.mark.parametrize("kind", list(CLAIM_FILES))
def test_the_claim_file_runs_in_a_sandbox_to_its_outcome(
    backend: LiveClient, kind: str
) -> None:
    run = play(backend, kind, variant=0)  # type: ignore[arg-type]
    assert run.error is None, run.error
    assert run.outcome == CLAIM_FILES[kind].outcome, run.transcript
    assert run.customer_text == CLAIM_FILES[kind].customer_text  # word for word
    assert run.fields_ok and run.closed
    events = backend.app.state.store.events_since(run.claim_id, 0)
    sandbox = [e.stage for e in events if e.actor == "sandbox"]
    assert sandbox[0] == "created" and sandbox[-1] == "deleted"
    assert "failed" not in sandbox
    assert backend.app.state.store.sandboxes()[0].closed
    shell = OpenShell()
    assert not shell.exists(backend.app.state.store.sandboxes()[0].name)


# ---- confinement, from inside a live sandbox ----------------------------------------

PROBE = """
import json, os, re, sys
import httpx
port, other, claim = sys.argv[1:4]
out = {}
def get(url, **kw):
    try:
        r = httpx.request(kw.pop("method", "GET"), url, timeout=10, **kw)
        return r.status_code, r.text[:80]
    except Exception as e:
        return type(e).__name__, str(e)[:80]
# 1. another host, and the model host on another path
out["example.com"] = get("https://example.com")
out["github"] = get("https://api.github.com")
out["models"] = get("https://integrate.api.nvidia.com/v1/models")
# 2. the backend: other paths, and the MCP path of another claim
base = f"http://host.openshell.internal:{port}"
rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
hdr = {
    "Accept": "application/json, text/event-stream",
    "MCP-Protocol-Version": "2025-11-25",
}
out["health"] = get(base + "/health", headers=hdr)
out["claim"] = get(f"{base}/claims/{claim}", headers=hdr)
out["other_mcp"] = get(f"{base}/mcp/{other}", method="POST", json=rpc, headers=hdr)
out["own_mcp"] = get(os.environ["MCP_URL"], method="POST", json=rpc, headers=hdr)
call = {"name": "write_fraud_report", "arguments": {}}  # not in the policy
bad_tool = dict(rpc, method="tools/call", params=call)
mine = os.environ["MCP_URL"]
out["other_tool"] = get(mine, method="POST", json=bad_tool, headers=hdr)
# 3. the key: a real key looks like nvapi-...; the environment holds a placeholder
env = {
    k: v for k, v in os.environ.items() if "KEY" in k.upper() or "NVIDIA" in k.upper()
}
out["env_keys"] = env
pattern = re.compile(rb"nvapi-[A-Za-z0-9_-]{10,}")
hits = [k for k, v in os.environ.items() if pattern.search(v.encode())]
for root in ("/sandbox", "/tmp", "/etc", "/app", "/home", "/run"):
    for d, _, names in os.walk(root):
        for n in names:
            try:
                if pattern.search(open(os.path.join(d, n), "rb").read()):
                    hits.append(os.path.join(d, n))
            except OSError:
                pass
if pattern.search(open("/proc/self/environ", "rb").read()):
    hits.append("/proc/self/environ")
out["key_hits"] = hits
out["mounts"] = [l for l in open("/proc/mounts") if "virtiofs" in l or "/Users" in l]
def writable(path):
    try:
        open(path, "w").close()
        os.remove(path)
        return True
    except OSError:
        return False
out["writable"] = {"/sandbox": writable("/sandbox/x"), "/app": writable("/app/x")}
print(json.dumps(out))
"""


def probe(backend: LiveClient) -> dict[str, Any]:
    agent: SandboxAgent = backend.app.state.agent
    store = backend.app.state.store
    mine = store.create_claim("C-1001").id
    theirs = store.create_claim("C-1001").id
    a = agent._create(mine)
    b = agent._create(theirs)
    # the token of the other claim, as its own sandbox has it
    show = "import os; print(os.environ['MCP_URL'].rsplit('/', 1)[1])"
    other_token = agent.shell.exec(b.name, ["python", "-c", show], "", 30).strip()
    out = agent.shell.exec(
        a.name, ["python", "-c", PROBE, str(agent.port), other_token, mine], "", 120
    )
    return dict(json.loads(out.strip().splitlines()[-1]))


def test_a_live_sandbox_reaches_its_own_mcp_path_and_nothing_else(
    backend: LiveClient,
) -> None:
    out = probe(backend)
    print(json.dumps(out, indent=1))  # the evidence: pytest -m sandbox -s
    # 1. another host is denied, and so is another path of the model host
    for name in ("example.com", "github"):
        assert out[name][0] in ("ConnectError", "ProxyError", 403, "ConnectTimeout"), (
            out
        )
    assert out["models"][0] == 403
    # 2. the other paths of the backend, and the MCP path of another claim, are denied
    for name in ("health", "claim", "other_mcp", "other_tool"):
        assert out[name][0] in (403, "ConnectError", "ProxyError"), (name, out[name])
    assert out["own_mcp"][0] == 200  # its own path still works
    # 3. no key: only a placeholder, and no nvapi- value anywhere
    assert out["key_hits"] == []
    for name, value in out["env_keys"].items():
        assert value.startswith("openshell:resolve:env:") or "nvapi-" not in value, name
    assert out["mounts"] == []  # no folder of the host
    assert out["writable"] == {"/sandbox": True, "/app": False}  # scratch only


def test_the_environment_inside_a_live_sandbox_holds_no_key_value(
    backend: LiveClient,
) -> None:
    """Non-negotiable 6. The key is not sent into the sandbox to look for it: a real
    NVIDIA key has the form nvapi-..., so the probe looks for that form."""
    out = probe(backend)
    assert out["key_hits"] == []
    assert all("nvapi-" not in v for v in out["env_keys"].values())
    assert json.dumps(out).count("nvapi-") == 0


# ---- the pipeline panel: policy, denials, confinement probe (issue #15) -----------


def test_the_probe_of_a_live_sandbox_gives_five_denials_and_the_token_stays_in(
    backend: LiveClient,
) -> None:
    agent: SandboxAgent = backend.app.state.agent
    store = backend.app.state.store
    cid = store.create_claim("C-1001").id
    record = agent._create(cid)
    token = Path(record.policy_file).read_text().split("/mcp/")[1].split()[0]
    assert len(token) >= 43

    res = backend.post(f"/claims/{cid}/sandbox/probe")
    assert res.status_code == 200, res.text
    out = res.json()
    print(json.dumps(out, indent=1))  # the evidence: pytest -m sandbox -s
    assert [r["denied"] for r in out["results"]] == [True] * 5
    assert out["confined"] is True
    assert out["results"][3]["detail"].startswith(
        "the variable holds a placeholder: openshell:resolve:env:"
    )

    events = store.events_since(cid, 0)
    probes = [e for e in events if e.stage == "probe"]
    assert [e.outputs["denied"] for e in probes] == [True] * 5
    denials = [e for e in events if e.stage == "denied"]
    print([e.title for e in denials])
    assert len(denials) >= 3  # example.com, another claim, another tool
    assert any("example.com" in e.title for e in denials)
    assert any("write_fraud_report" in e.title for e in denials)
    assert all(e.outputs["reason"] for e in denials)

    view = backend.get(f"/claims/{cid}/sandbox")
    assert view.status_code == 200 and view.json()["state"] == "running"
    assert view.json()["policy"]["gateway"]["path"] == f"/mcp/{token[:6]}…"

    # the token is in no answer and no event, though the OpenShell log holds it whole
    raw = "\n".join(agent.shell.log_lines(record.name))
    assert token in raw
    answers = [
        view,
        res,
        backend.get(f"/claims/{cid}"),
        backend.get(f"/claims/{cid}/messages"),
        backend.get(f"/claims/{cid}/customer"),
        backend.post(f"/claims/{cid}/sandbox/probe"),  # a second probe: new denials
    ]
    for answer in answers:
        assert token not in answer.text
    events = store.events_since(cid, 0)
    assert token not in json.dumps([e.model_dump(mode="json") for e in events])
    assert len([e for e in events if e.stage == "denied"]) > len(denials)

    backend.post("/demo/reset")
    assert not OpenShell().exists(record.name)
    assert backend.post(f"/claims/{cid}/sandbox/probe").status_code == 404
