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
        return self.turn(self, name, argv, stdin)

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


__all__ = ["FakeShell", "ShellError", "ShellTimeout", "line", "use_fake_shell"]
