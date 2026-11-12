"""The claim agent in an OpenShell sandbox (`CLAIMS_MODE=sandbox`, issue #14).

The same `handle` as the other agents. One sandbox per claim: it is created on the
first turn (inside the per-claim lock of the API, about half a second), each turn is
one `openshell sandbox exec` that runs the graph (`app.agent.sandbox_turn`), and it is
deleted when the claim closes and on the demo reset. Every `openshell` call goes
through the `OpenShell` class below, with a timeout, so tests replace it with a fake.

The sandbox is not trusted, and the backend decides what the customer reads:

- The JSON line of a turn is data. The node and model events in it are stored as
  display-only events (`"source": "sandbox"` in their inputs); the backend never acts
  on them and never accepts an event of actor `tool` or `sandbox` from the sandbox.
  The trusted events are the ones the MCP server writes when a tool call arrives.
- The text of the agent passes the output filter. When the claim has an outcome after
  the turn, the backend sends its own `customer_message` word for word and drops the
  text from the sandbox.
- A sandbox that fails to start, a turn that times out or prints no JSON line gives
  the fallback message and one `sandbox` event with the reason. The next message
  tries again.

The only authorisation of a sandbox toward its claim is its MCP path with a random
token (`app.tools.mcp_server`). The policy of the sandbox is made from
`infra/sandbox/policy.yaml` with that path and the tools of the graph. The policy file
of each sandbox is in `CLAIMS_SANDBOX_DIR` (mode 0600: it holds the token) and its
path is in the `created` event. Denied requests: `openshell logs <sandbox> --source
sandbox`, the lines with `DENIED` (the same command is in the event).

For the pipeline panel (issue #15): after each turn the backend reads the denial lines
of the sandbox log and stores each new one as a `denied` event (token cut), and
`probe` runs a fixed confinement test in the sandbox (`app.agent.sandbox_panel`).
"""

import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from app.agent.graph import GRAPH_TOOLS
from app.agent.handover import hand_over_if_stuck, is_handed_over
from app.agent.phrases import CLOSED, FALLBACK, check_message
from app.agent.sandbox_panel import (
    PROBE,
    PROBE_COMMAND,
    PROBE_EXEC_TIMEOUT,
    Denial,
    clean,
    judge,
    parse_denial,
)
from app.models import SandboxRecord
from app.tools.gateway import Gateway
from app.tools.mcp_server import PATH, hash_token
from app.views import customer_message

PREFIX = "claims-"  # every OpenShell object of this app starts with it
PROVIDER = "claims-nim"
SETUP = "infra/sandbox/setup.sh"
HOST = "host.openshell.internal"  # the Mac or the VM, as the sandbox sees it
TEMPLATE = Path(__file__).resolve().parents[3] / "infra" / "sandbox" / "policy.yaml"
TURN = ["python", "-m", "app.agent.sandbox_turn"]
CREATE_TIMEOUT = 90.0  # seconds: create, then wait for the first exec
DELETE_TIMEOUT = 30.0
QUICK_TIMEOUT = 20.0  # status, get, provider get, docker inspect
LOG_LINES = 500  # read from the sandbox log after a turn
MAX_EVENTS = 300  # per turn, from the sandbox
MAX_EVENT_CHARS = 20_000
SANDBOX_ACTORS = ("workflow", "agent")  # what the sandbox may report, display only


class ShellError(Exception):
    """An `openshell` call failed. The text says why, with no secret in it."""


class ShellTimeout(ShellError):
    pass


class SandboxNotRunning(ShellError):
    """The claim has no sandbox that runs: it is closed, or it is gone."""


class Shell(Protocol):
    """What the runner needs from OpenShell. `OpenShell` is the real one."""

    def available(self) -> bool: ...
    def connected(self) -> bool: ...
    def has_provider(self, name: str) -> bool: ...
    def has_image(self, image: str) -> bool: ...
    def exists(self, name: str) -> bool: ...
    def create(
        self, name: str, policy: Path, image: str, provider: str, env: dict[str, str]
    ) -> None: ...
    def exec(self, name: str, argv: list[str], stdin: str, timeout: float) -> str: ...
    def delete(self, name: str) -> None: ...
    def log_lines(self, name: str) -> list[str]: ...


class OpenShell:
    """The `openshell` command (0.1.5). Every call has a timeout."""

    def __init__(self, binary: str = "openshell") -> None:
        self.binary = binary

    def _run(
        self, args: list[str], timeout: float, stdin: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        what = " ".join(args[:2])
        try:
            return subprocess.run(
                [self.binary, *args], input=stdin, capture_output=True, text=True,
                timeout=timeout, check=False,
            )  # fmt: skip
        except subprocess.TimeoutExpired:
            raise ShellTimeout(
                f"openshell {what} timed out after {timeout:.0f} s"
            ) from None
        except OSError as e:
            raise ShellError(f"cannot run openshell: {e.strerror}") from None

    def _checked(
        self, args: list[str], timeout: float, stdin: str | None = None
    ) -> str:
        done = self._run(args, timeout, stdin)
        if done.returncode != 0:
            lines = (done.stderr or done.stdout).strip().splitlines()
            why = lines[-1][:200] if lines else "no output"
            raise ShellError(
                f"openshell {' '.join(args[:2])} failed ({done.returncode}): {why}"
            )
        return done.stdout

    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    def connected(self) -> bool:
        try:
            return "Connected" in self._checked(["status"], QUICK_TIMEOUT)
        except ShellError:
            return False

    def has_provider(self, name: str) -> bool:
        return self._run(["provider", "get", name], QUICK_TIMEOUT).returncode == 0

    def has_image(self, image: str) -> bool:
        """The image must be in the Docker engine of the gateway (Colima on a Mac)."""
        env = dict(os.environ)
        colima = Path.home() / ".colima" / "default" / "docker.sock"
        if "DOCKER_HOST" not in env and colima.exists():
            env["DOCKER_HOST"] = f"unix://{colima}"
        try:
            return (
                subprocess.run(
                    ["docker", "image", "inspect", image], env=env, capture_output=True,
                    timeout=QUICK_TIMEOUT, check=False,
                ).returncode == 0
            )  # fmt: skip
        except (OSError, subprocess.TimeoutExpired):
            return False

    def exists(self, name: str) -> bool:
        return self._run(["sandbox", "get", name], QUICK_TIMEOUT).returncode == 0

    def create(
        self, name: str, policy: Path, image: str, provider: str, env: dict[str, str]
    ) -> None:
        args = [
            "sandbox", "create", "--name", name, "--from", image,
            "--policy", str(policy),
            "--provider", provider, "--no-auto-providers",
        ]  # fmt: skip
        for key, value in env.items():
            args += ["--env", f"{key}={value}"]
        args += ["--label", "app=claims-nvidia", "--detach", "--", "sleep", "infinity"]
        deadline = time.monotonic() + CREATE_TIMEOUT
        self._checked(args, CREATE_TIMEOUT)
        while True:  # the sandbox answers `exec` a moment after create
            try:
                self.exec(name, ["true"], "", 10.0)
                return
            except ShellError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.5)

    def exec(self, name: str, argv: list[str], stdin: str, timeout: float) -> str:
        args = ["sandbox", "exec", "--name", name, "--no-tty", "--no-login-shell"]
        args += ["--timeout", str(int(timeout)), "--", *argv]
        start = time.monotonic()
        try:
            return self._checked(args, timeout + 5, stdin)
        except ShellTimeout:
            raise
        except ShellError as e:  # the CLI stops at its own --timeout, with an exit code
            if time.monotonic() - start >= timeout - 1:
                raise ShellTimeout(f"the turn timed out after {timeout:.0f} s") from e
            raise

    def delete(self, name: str) -> None:
        self._checked(["sandbox", "delete", name], DELETE_TIMEOUT)

    def log_lines(self, name: str) -> list[str]:
        """The last lines of the sandbox log (the supervisor, not the gateway). The
        log reaches the gateway a few tenths of a second after the event."""
        out = self._checked(
            ["logs", name, "--source", "sandbox", "-n", str(LOG_LINES)], QUICK_TIMEOUT
        )
        return out.splitlines()


def sandbox_name(claim_id: str) -> str:
    """At most 19 characters, always with the prefix."""
    name = f"{PREFIX}{claim_id}".lower()
    if re.fullmatch(r"[a-z0-9-]{8,19}", name):
        return name
    return PREFIX + hashlib.sha256(claim_id.encode()).hexdigest()[:12]


def mcp_path(token: str) -> str:
    return f"{PATH}/{token}"


def render_policy(port: int, token: str, template: Path = TEMPLATE) -> str:
    """The policy of one sandbox: the MCP path of its claim, the tools of the graph."""
    rules = "\n".join(
        "          - allow:\n"
        "              method: tools/call\n"
        f"              tool: {tool}"
        for tool in GRAPH_TOOLS
    )
    return (
        template.read_text()
        .replace("@PORT@", str(port))
        .replace("@PATH@", mcp_path(token))
        .replace("@TOOL_RULES@", rules)
    )


class SandboxAgent:
    def __init__(
        self,
        gateway: Gateway,
        shell: Shell,
        model: str,
        env: dict[str, str],
        port: int,
        state_dir: Path,
        image: str,
        turn_timeout: float = 150.0,
        denial_wait: float = 3.0,
    ) -> None:
        self.gateway = gateway
        self.shell = shell
        self.model = model  # its name must not reach the customer
        self.env = env  # given to every sandbox (--env): settings, never a key
        self.port = port
        self.state_dir = state_dir
        self.image = image
        self.turn_timeout = turn_timeout
        self.denial_wait = denial_wait  # seconds the probe waits for the log

    @classmethod
    def from_env(cls, gateway: Gateway) -> "SandboxAgent":
        """A missing piece stops the startup, with the one command that fixes it."""
        env = os.environ
        shell = OpenShell()
        fix = f"Run {SETUP} (see docs/ARCHITECTURE.md section 7)."
        image = env.get("CLAIMS_SANDBOX_IMAGE", "claims-agent:1")
        if not shell.available():
            raise RuntimeError(
                f"CLAIMS_MODE=sandbox needs the openshell command. {fix}"
            )
        if not shell.connected():
            raise RuntimeError(
                f"CLAIMS_MODE=sandbox: no OpenShell gateway answers. {fix}"
            )
        if not shell.has_provider(PROVIDER):
            raise RuntimeError(
                f"CLAIMS_MODE=sandbox: the provider {PROVIDER} is missing. {fix}"
            )
        if not shell.has_image(image):
            raise RuntimeError(
                f"CLAIMS_MODE=sandbox: the image {image} is missing. {fix}"
            )
        for name in ("AGENT_BASE_URL", "AGENT_MODEL"):
            if not env.get(name):
                raise RuntimeError(
                    f"CLAIMS_MODE=sandbox needs {name} (see .env.example)"
                )
        settings = {
            name: env[name]
            for name in ("AGENT_BASE_URL", "AGENT_MODEL", "AGENT_THINKING")
            if env.get(name)
        }  # the key is not here: the provider holds it, outside the sandbox
        return cls(
            gateway, shell, env["AGENT_MODEL"], {"PYTHONPATH": "/app", **settings},
            int(env.get("BACKEND_PORT", "8000")),
            Path(env.get("CLAIMS_SANDBOX_DIR", ".dev-data/sandboxes")).resolve(), image,
            float(env.get("CLAIMS_SANDBOX_TURN_TIMEOUT", "150")),
        )  # fmt: skip

    # ---- life cycle ------------------------------------------------------------

    def _event(
        self,
        claim_id: str,
        stage: str,
        title: str,
        inputs: dict[str, Any],
        outputs: dict[str, Any],
        duration: float | None = None,
    ) -> None:
        self.gateway.store.append_event(
            claim_id, "sandbox", stage, title, inputs, outputs, None, duration
        )

    def _create(self, claim_id: str) -> SandboxRecord:
        store = self.gateway.store
        name = sandbox_name(claim_id)
        if store.get_sandbox(claim_id) is not None and self.shell.exists(name):
            self.shell.delete(name)  # a leftover of this claim, from before
        token = secrets.token_urlsafe(32)
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        policy_file = self.state_dir / f"{name}.policy.yaml"
        # mode 0600: the policy holds the token
        fd = os.open(policy_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(render_policy(self.port, token))
        record = SandboxRecord(
            claim_id=claim_id, name=name, token_sha256=hash_token(token),
            policy_file=str(policy_file), created=datetime.now(UTC),
        )  # fmt: skip
        store.save_sandbox(record)  # before create: a reset finds it, whatever happens
        env = {**self.env, "MCP_URL": f"http://{HOST}:{self.port}{mcp_path(token)}"}
        start = time.perf_counter()
        try:
            self.shell.create(name, policy_file, self.image, PROVIDER, env)
        except ShellError as e:
            self._forget(record)
            raise ShellError(_scrub(f"the sandbox did not start: {e}", token)) from None
        self._event(
            claim_id, "created", "Sandbox created",
            {"sandbox": name, "image": self.image, "provider": PROVIDER},
            {
                "mcp_path": f"{PATH}/{token[:4]}…",  # shortened: the token is a secret
                "policy_file": str(policy_file),
                "denials": f"openshell logs {name} --source sandbox",
            },
            time.perf_counter() - start,
        )  # fmt: skip
        return record

    def _forget(self, record: SandboxRecord) -> None:
        """The sandbox is gone, or never started: its token stops working."""
        try:
            if self.shell.exists(record.name):
                self.shell.delete(record.name)
        except ShellError:
            pass  # the reset tries again: the record stays
        self.gateway.store.save_sandbox(record.model_copy(update={"closed": True}))

    def _close(self, claim_id: str, record: SandboxRecord, why: str) -> None:
        start = time.perf_counter()
        try:
            self.shell.delete(record.name)
            self._event(
                claim_id, "deleted", "Sandbox deleted", {"sandbox": record.name},
                {"reason": why}, time.perf_counter() - start,
            )  # fmt: skip
        except ShellError as e:
            self._event(
                claim_id, "failed", "Sandbox delete failed", {"sandbox": record.name},
                {"reason": str(e)},
            )  # fmt: skip
        self.gateway.store.save_sandbox(record.model_copy(update={"closed": True}))

    def reset(self) -> None:
        """The demo reset: the sandboxes this app made, and only those (a record in
        the store and the prefix), and their policy files."""
        for record in self.gateway.store.sandboxes():
            if record.name.startswith(PREFIX):
                try:
                    if self.shell.exists(record.name):
                        self.shell.delete(record.name)
                except ShellError:
                    pass
        shutil.rmtree(self.state_dir, ignore_errors=True)

    # ---- a turn ----------------------------------------------------------------

    def handle(
        self, claim_id: str, text: str | None, document_id: str | None
    ) -> list[str]:
        store = self.gateway.store
        claim = store.get_claim(claim_id)
        if (claim is not None and claim.outcome is not None) or is_handed_over(claim):
            return [CLOSED]  # closed: the sandbox is gone, nothing to run
        record = store.get_sandbox(claim_id)
        try:
            if record is None or record.closed or not self.shell.exists(record.name):
                record = self._create(claim_id)
            data, seconds, started, ended = self._turn(
                claim_id, record, text, document_id
            )
        except ShellError as e:
            self._event(
                claim_id, "failed", "Sandbox turn failed", {}, {"reason": str(e)}
            )
            if isinstance(e, ShellTimeout) and record is not None:
                # a process may still run in it: delete it, the next one starts clean
                self._forget(record)
                record = None
            # the routing may have run before the failure: the customer still gets it
            if (text := self._outcome_text(claim_id)) is not None:
                if record is not None:
                    self._close(claim_id, record, "the claim is closed")
                return [text]
            return [FALLBACK]
        self._keep_events(claim_id, data, started, ended)
        self._event(
            claim_id, "turn", f"Sandbox turn ({seconds:.1f} s)",
            {"sandbox": record.name},
            {"seconds": round(seconds, 2)}, seconds,
        )  # fmt: skip
        reply, closed = self._reply(claim_id, data.get("reply"))
        if not closed and (handover := hand_over_if_stuck(store, claim_id)):
            reply, closed = handover, True  # a code node failed twice: a person
        self.collect_denials(claim_id, record)  # before a close: the log goes with it
        if closed:
            self._close(claim_id, record, "the claim is closed")
        return [reply]

    def _turn(
        self,
        claim_id: str,
        record: SandboxRecord,
        text: str | None,
        document_id: str | None,
    ) -> tuple[dict[str, Any], float, datetime, datetime]:
        # the customer text goes on stdin, never on a command line
        stdin = json.dumps({"text": text, "document_id": document_id})
        started = datetime.now(UTC)
        start = time.perf_counter()
        out = self.shell.exec(
            record.name, [*TURN, "--thread", claim_id], stdin, self.turn_timeout
        )
        seconds = time.perf_counter() - start
        ended = datetime.now(UTC)
        lines = [line for line in out.splitlines() if line.strip()]
        try:
            data = json.loads(lines[-1])
        except (IndexError, ValueError):
            raise ShellError("the turn printed no JSON line") from None
        if not isinstance(data, dict):
            raise ShellError("the turn printed no JSON object")
        return data, seconds, started, ended

    def _keep_events(
        self, claim_id: str, data: dict[str, Any], started: datetime, ended: datetime
    ) -> None:
        """The node and model events of the sandbox: display only. Only `workflow` and
        `agent` events, each marked with its source, and capped in number and size.
        The display time of an event is when it happened, placed by `display_time`."""
        events = data.get("events")
        if not isinstance(events, list):
            return
        for e in events[:MAX_EVENTS]:
            try:
                if (
                    e["actor"] not in SANDBOX_ACTORS
                    or len(json.dumps(e)) > MAX_EVENT_CHARS
                ):
                    continue
                duration = e.get("duration")
                self.gateway.store.append_event(
                    claim_id, e["actor"], str(e["stage"])[:40], str(e["title"])[:80],
                    {**dict(e.get("inputs") or {}), "source": "sandbox"},
                    dict(e.get("outputs") or {}),
                    str(e["model"]) if e.get("model") else None,
                    float(duration) if duration is not None else None,
                    display_time(started, ended, e.get("at")),
                )  # fmt: skip
            except (KeyError, TypeError, ValueError):
                continue  # a malformed event is dropped

    def _outcome_text(self, claim_id: str) -> str | None:
        """Our text for the customer when the claim has an outcome, else None. The
        claim is closed here if the graph did not do it."""
        store = self.gateway.store
        claim = store.get_claim(claim_id)
        if claim is None or claim.outcome is None:
            return None
        if claim.agent_state != "closed":
            store.update_claim(claim_id, {"agent_state": "closed"})
        return customer_message(claim.outcome)  # ours, word for word

    def _reply(self, claim_id: str, said: Any) -> tuple[str, bool]:
        """What the customer reads, and whether the claim is closed."""
        if (text := self._outcome_text(claim_id)) is not None:
            return text, True  # the text of the sandbox is dropped
        why = check_message(said if isinstance(said, str) else None, self.model)
        if why:
            self.gateway.store.append_event(
                claim_id, "agent", "fallback", "Fallback message", {},
                {"reason": f"the text from the sandbox was refused ({why})"},
            )  # fmt: skip
            return FALLBACK, False
        return str(said), False

    # ---- the pipeline panel (issue #15) ----------------------------------------

    def collect_denials(
        self, claim_id: str, record: SandboxRecord, wait_for: int = 0
    ) -> None:
        """Stores each denial line of the sandbox log that is not stored yet as one
        `denied` event (token cut). The log reaches the gateway about 0.3 s after the
        request, so with `wait_for` it reads again, for at most `denial_wait` seconds,
        until that many new denials are there. A failed read is not an error: the next
        call reads the same lines again, and a line is never stored twice."""
        deadline = time.monotonic() + self.denial_wait
        store = self.gateway.store
        while True:
            try:
                lines = self.shell.log_lines(record.name)
            except ShellError:
                return
            known = {
                e.inputs.get("line")
                for e in store.events_since(claim_id, 0)
                if e.actor == "sandbox" and e.stage == "denied"
            }
            new: dict[str, Denial] = {}
            for raw in lines:
                found = parse_denial(raw)
                if found is not None and found.line not in known:
                    new[found.line] = found
            if len(new) >= wait_for or time.monotonic() >= deadline:
                break
            time.sleep(0.25)
        now = datetime.now(UTC)
        for d in new.values():
            store.append_event(
                claim_id, "sandbox", "denied", f"Denied: {d.what}",
                {"line": d.line, "kind": d.kind, "policy": d.policy},
                {"reason": d.reason}, None, None, min(d.at, now),
            )  # fmt: skip

    def probe(self, claim_id: str) -> dict[str, Any]:
        """The confinement test: a fixed script in the sandbox of this claim. Nothing
        of a request reaches the command. One `probe` event per try, then the denials
        it caused. Raises `ShellError` when the sandbox cannot run it."""
        record = self.gateway.store.get_sandbox(claim_id)
        if record is None or record.closed or not self.shell.exists(record.name):
            raise SandboxNotRunning("the sandbox is not running")
        try:
            out = self.shell.exec(record.name, PROBE_COMMAND, PROBE, PROBE_EXEC_TIMEOUT)
            lines = [line for line in out.splitlines() if line.strip()]
            try:
                raw = json.loads(lines[-1])
            except (IndexError, ValueError):
                raise ShellError("the probe printed no JSON line") from None
        except ShellError as e:
            self._event(
                claim_id, "failed", "Sandbox probe failed", {},
                {"reason": clean(e)},
            )  # fmt: skip
            raise
        results = judge(raw)
        for r in results:
            verdict = "denied" if r.denied else "NOT denied"
            self._event(
                claim_id, "probe", f"Probe: {r.label} → {verdict}",
                {"what": r.what}, {"denied": r.denied, "detail": r.detail},
            )  # fmt: skip
        self.collect_denials(claim_id, record, wait_for=3)
        return {
            "confined": all(r.denied for r in results),
            "results": [r.model_dump() for r in results],
        }


def display_time(started: datetime, ended: datetime, at: Any) -> datetime | None:
    """When an event of the sandbox happened, in the time of the backend.

    The sandbox gives each event `at`, its own clock in epoch seconds. The sandbox is
    a container on the machine of the backend (on a Mac, in the Colima VM), and its
    clock agreed with the backend to a few milliseconds when measured. The result
    stays inside the time the backend waited for the turn (`started` to `ended`), so
    a clock that is wrong places events at the start or the end of their turn and
    never outside it. Counting back from the end of the turn does not work: the
    process takes a time to exit and `openshell` to return, about 0.2 s here, which
    is more than the gap between a node event and its first tool call.

    None when `at` is not a usable number: the event keeps its arrival time."""
    if isinstance(at, bool) or not isinstance(at, int | float) or not 0 < at < 4e9:
        return None  # also a NaN or an infinity: they are not between the bounds
    return min(ended, max(started, datetime.fromtimestamp(at, UTC)))


def _scrub(text: str, token: str) -> str:
    return text.replace(token, "<token>")
