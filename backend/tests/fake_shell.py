"""A fake `openshell` for the sandbox agent tests: no OpenShell, no Docker.

`FakeShell` records every call. `exec` plays a turn: a callable that gets the sandbox
name, the command, the stdin and the policy of that sandbox, and returns the stdout.
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app.agent.sandbox import ShellError, ShellTimeout

Turn = Callable[["FakeShell", str, list[str], str], str]

# Lines of `openshell logs <sandbox> --source sandbox` (OpenShell 0.1.5), copied from a
# live run of the confinement probe. `{token}` is the token of the sandbox, which the
# real log shows whole; `{other}` is the made-up token of "another claim"; `{port}` the
# backend port. Only the ALLOWED lines, the warning and the DENIED lines are here.
OTHER = "x" * 43
DENIAL_LINES = (
    "[1794522333.576] [sandbox] [OCSF ] [ocsf] NET:LISTEN [INFO] 127.0.0.1:3128",
    "[1794522333.771] [sandbox] [OCSF ] [ocsf] NET:REFUSE [MED] DENIED example.com "
    "[reason:policy_dns_ineligible]",
    "[1794522333.771] [sandbox] [WARN ] [openshell_supervisor_network::proxy] Denied "
    "staged transparent connection destination=198.18.0.2:443 reason=endpoint "
    "example.com:443 is not allowed by any policy",
    "[1794522333.771] [sandbox] [OCSF ] [ocsf] NET:OPEN [MED] DENIED "
    "/usr/local/bin/python3.12(0) -> example.com:443 "
    "[reason:transparent_tcp_policy_denied]",
    "[1794522333.778] [sandbox] [OCSF ] [ocsf] NET:OPEN [INFO] ALLOWED "
    "/usr/local/bin/python3.12(0) -> host.openshell.internal:{port} "
    "[policy:claims_gateway engine:opa]",
    "[1794522333.778] [sandbox] [OCSF ] [ocsf] HTTP:POST [MED] DENIED POST "
    "http://host.openshell.internal:{port}/mcp/{other} "
    "[policy:claims_gateway engine:l7-mcp] [reason:JSONRPC_L7_REQUEST decision=deny "
    "rule_methods=tools/call tools=get_claim http_method=POST "
    "endpoint=host.openshell.internal:{port}/mcp/{other} policy_version=1 "
    "reason=POST /mcp/{other} n...]",
    "[1794522333.788] [sandbox] [OCSF ] [ocsf] HTTP:POST [MED] DENIED POST "
    "http://host.openshell.internal:{port}/mcp/{token} "
    "[policy:claims_gateway engine:l7-mcp] [reason:JSONRPC_L7_REQUEST decision=deny "
    "rule_methods=tools/call tools=write_fraud_report http_method=POST "
    "endpoint=host.openshell.internal:{port}/mcp/{token} policy_version=1 "
    "reason=MCP tool call has no matching allow rule; ask ...]",
)
PLACEHOLDER = "openshell:resolve:env:v15876687825480274345_NVIDIA_API_KEY"


def probe_line(**changes: Any) -> str:
    """The stdout of `PROBE` in a confined sandbox (what a live run printed)."""
    out: dict[str, Any] = {
        "other_host": {
            "error": "ConnectError",
            "detail": "[Errno 13] Permission denied",
        },
        "other_claim": {"status": 403, "detail": "policy_denied"},
        "other_tool": {"status": 403, "detail": "policy_denied"},
        "model_key": {"has_real_key": False, "placeholder": PLACEHOLDER},
        "host_files": {
            "/Users": {"error": "FileNotFoundError"},
            "/host": {"error": "FileNotFoundError"},
        },
        **changes,
    }
    return f"noise\n{json.dumps(out)}\n"


def line(reply: str | None, events: list[dict[str, Any]] | None = None) -> str:
    """The stdout of a good turn: noise, then the JSON line."""
    data = {"reply": reply, "events": events or [], "seconds": 1.0}
    return f"some warning\n{json.dumps(data)}\n"


class FakeShell:
    def __init__(self, turn: Turn | None = None) -> None:
        self.turn = turn or (lambda shell, name, argv, stdin: line("Hello."))
        self.sandboxes: dict[str, dict[str, Any]] = {}  # name -> create call
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fail_create: ShellError | Callable[[dict[str, str]], ShellError] | None = (
            None
        )
        self.fail_exec: ShellError | None = None
        self.probe_out = probe_line()  # what `python -` prints in the sandbox
        self.log: list[str] = []  # the sandbox log; a probe adds its denials
        self.probes: list[str] = []  # the stdin of each probe
        self.log_reads = 0
        self.fail_log = False
        self.fail_delete = False
        self.is_available = True
        self.is_connected = True
        self.providers = {"claims-nim"}
        self.images = {"claims-agent:1"}

    def names(self, call: str) -> list[str]:
        return [args[0] for c, args in self.calls if c == call]

    def available(self) -> bool:
        return self.is_available

    def connected(self) -> bool:
        return self.is_connected

    def has_provider(self, name: str) -> bool:
        return name in self.providers

    def has_image(self, image: str) -> bool:
        return image in self.images

    def exists(self, name: str) -> bool:
        return name in self.sandboxes

    def create(
        self, name: str, policy: Path, image: str, provider: str, env: dict[str, str]
    ) -> None:
        self.calls.append(("create", (name, policy, image, provider, dict(env))))
        if self.fail_create:
            raise (
                self.fail_create
                if isinstance(self.fail_create, ShellError)
                else self.fail_create(env)
            )
        self.sandboxes[name] = {
            "policy": policy.read_text(),
            "policy_file": policy,
            "env": dict(env),
            "provider": provider,
            "image": image,
        }

    def exec(self, name: str, argv: list[str], stdin: str, timeout: float) -> str:
        self.calls.append(("exec", (name, list(argv), stdin, timeout)))
        if self.fail_exec:
            raise self.fail_exec
        if name not in self.sandboxes:
            raise ShellError("no such sandbox")
        if argv == ["python", "-"]:  # the confinement probe
            self.probes.append(stdin)
            self.log += self.denial_lines(name)
            return self.probe_out
        return self.turn(self, name, argv, stdin)

    def denial_lines(self, name: str) -> list[str]:
        """What the supervisor logs for the probe of this sandbox, token whole."""
        env = self.sandboxes[name]["env"]
        token = env["MCP_URL"].rsplit("/", 1)[1]
        port = env["MCP_URL"].split(":")[2].split("/")[0]
        return [ln.format(token=token, other=OTHER, port=port) for ln in DENIAL_LINES]

    def log_lines(self, name: str) -> list[str]:
        self.log_reads += 1
        if self.fail_log:
            raise ShellError("openshell logs failed (1): boom")
        if name not in self.sandboxes:
            raise ShellError("no such sandbox")
        return list(self.log)

    def delete(self, name: str) -> None:
        self.calls.append(("delete", (name,)))
        if self.fail_delete:
            raise ShellError("openshell sandbox delete failed (1): boom")
        self.sandboxes.pop(name, None)


def use_fake_shell(
    monkeypatch: pytest.MonkeyPatch, shell: FakeShell, tmp_path: Path
) -> FakeShell:
    """CLAIMS_MODE=sandbox with the fake in place of `OpenShell`."""
    import app.agent.sandbox as sandbox

    monkeypatch.setenv("CLAIMS_MODE", "sandbox")
    monkeypatch.setenv("AGENT_BASE_URL", "https://nim.test/v1")
    monkeypatch.setenv("AGENT_MODEL", "nvidia/fake-model-1")
    monkeypatch.setenv("BACKEND_PORT", "8000")
    monkeypatch.setenv("CLAIMS_SANDBOX_DIR", str(tmp_path / "sandboxes"))
    monkeypatch.setattr(sandbox, "OpenShell", lambda: shell)
    return shell


__all__ = [
    "DENIAL_LINES",
    "FakeShell",
    "ShellError",
    "ShellTimeout",
    "line",
    "probe_line",
    "use_fake_shell",
]
