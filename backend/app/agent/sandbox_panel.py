"""What the pipeline panel shows of a sandbox (issue #15). No LangChain here, so
`local` mode can import it.

- `policy_view`: the policy of one sandbox as data, read from its rendered policy file
  and from the provider profile. The token is cut to its first 6 characters.
- `parse_denial`: one denial line of `openshell logs <sandbox> --source sandbox`. The
  OpenShell log holds the whole MCP path, so the token: it is cut before the line goes
  anywhere.
- `PROBE`: the confinement test. A constant: no input of a request reaches it. It runs
  in the sandbox as `python -` (script on stdin) and prints one JSON line. `judge`
  reads that line. The sandbox is not trusted, so a try counts as denied only when its
  answer proves it.

Real lines of the log, as OpenShell 0.1.5 writes them: `DENIAL_LINES` in
`tests/fake_shell.py`.
"""

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel

from app.models import SandboxRecord

PROFILE = Path(__file__).resolve().parents[3] / "infra" / "sandbox" / "profile-nim.yaml"
SHOWN_TOKEN = 6  # characters of the token the panel may show
PROBE_COMMAND = ["python", "-"]
PROBE_EXEC_TIMEOUT = 60.0  # seconds: five tries of at most 8 s each
MAX_TEXT = 300

_PATH_TOKEN = re.compile(r"(/mcp/)([A-Za-z0-9_-]{1,6})[A-Za-z0-9_-]*…?")
_KEY = re.compile(r"nvapi-[A-Za-z0-9_-]+")
_PLACEHOLDER = re.compile(r"openshell:resolve:env:[\w.:-]{0,100}")
_LINE = re.compile(
    r"^\[(?P<t>\d+\.\d+)\] \[sandbox\] \[OCSF \] \[ocsf\] "
    r"(?P<kind>[A-Z]+:[A-Z]+) \[[A-Z]+\] DENIED (?P<body>.*)$"
)


def cut_token(text: str) -> str:
    """`/mcp/<token>` becomes `/mcp/ab12cd…`. Safe to run twice."""
    return _PATH_TOKEN.sub(lambda m: f"{m[1]}{m[2]}…", text)


def clean(text: object) -> str:
    """Text from the sandbox or its log, before it is stored or shown: no token, and
    no key (the sandbox holds none, so a match would be an attack or a bug)."""
    return _KEY.sub("<key>", cut_token(str(text)))[:MAX_TEXT]


# ---- denials --------------------------------------------------------------------


class Denial(BaseModel):
    line: str  # the log line, token cut: the key that makes a denial unique
    at: datetime
    kind: str  # NET:OPEN, HTTP:POST, ...
    what: str
    reason: str
    policy: str | None


def parse_denial(raw: str) -> Denial | None:
    """None when the line is not a denial of the sandbox supervisor."""
    m = _LINE.match(raw.strip())
    if m is None:
        return None
    body = m["body"]
    head = re.split(r" \[(?:policy|reason):", body, maxsplit=1)[0]
    what = head.split(" -> ")[-1]  # NET:OPEN names the program first
    what = re.sub(r"^((?:[A-Z]+ )?)https?://", r"\1", what)  # HTTP: no scheme
    reason = re.search(r"\[reason:(.*)\]\s*$", body)
    why = reason[1].rsplit("reason=", 1)[-1] if reason else ""
    policy = re.search(r"\[policy:([^\s\]]+)", body)
    call = re.search(r"rule_methods=(\S+) tools=(\S+)", body)  # an MCP tool call
    if call:
        what += f" ({call[1]} {call[2]})"
    return Denial(
        line=clean(raw.strip()),
        at=datetime.fromtimestamp(float(m["t"]), UTC),
        kind=m["kind"],
        what=clean(what),
        reason=clean(why),
        policy=policy[1] if policy else None,
    )


# ---- the policy of a sandbox ------------------------------------------------------


class ModelEndpoint(BaseModel):
    host: str
    port: int
    method: str
    path: str


class ToolGateway(BaseModel):
    host: str
    port: int
    path: str  # the MCP path with the token cut
    methods: list[str]
    tools: list[str]


class PolicyView(BaseModel):
    model: ModelEndpoint
    gateway: ToolGateway
    read_only: list[str]
    read_write: list[str]
    user: str
    landlock: str


class SandboxView(BaseModel):
    name: str
    state: Literal["running", "deleted"]
    created: datetime
    created_ms: int | None  # the time `sandbox create` took
    policy: PolicyView


def policy_view(record: SandboxRecord, created_ms: int | None) -> SandboxView:
    """From the rendered policy file of this claim (it holds the token: cut here) and
    from the provider profile (the model endpoint). Raises OSError if a file is gone."""
    policy = yaml.safe_load(Path(record.policy_file).read_text())
    [endpoint] = policy["network_policies"]["claims_gateway"]["endpoints"]
    rules = [r["allow"] for r in endpoint["rules"]]
    profile = yaml.safe_load(PROFILE.read_text())["endpoints"][0]
    allowed = profile["rules"][0]["allow"]
    fs = policy["filesystem_policy"]
    workdir = ["/sandbox"] if fs.get("include_workdir") else []
    return SandboxView(
        name=record.name,
        state="deleted" if record.closed else "running",
        created=record.created,
        created_ms=created_ms,
        policy=PolicyView(
            model=ModelEndpoint(
                host=profile["host"], port=profile["port"],
                method=allowed["method"], path=allowed["path"],
            ),
            gateway=ToolGateway(
                host=endpoint["host"], port=endpoint["port"],
                path=cut_token(endpoint["path"]),
                methods=list(dict.fromkeys(r["method"] for r in rules)),
                tools=[r["tool"] for r in rules if "tool" in r],
            ),
            read_only=fs["read_only"],
            read_write=[*workdir, *fs["read_write"]],
            user=str(policy["process"]["run_as_user"]),
            landlock=policy["landlock"]["compatibility"],
        ),
    )  # fmt: skip


# ---- the confinement probe ----------------------------------------------------------

# Runs in the sandbox. It sends no key and prints none: the model key is reported as a
# boolean (does it start like a real key?) and the placeholder text.
PROBE = """
import json, os
import httpx

TIMEOUT = 8
URL = os.environ["MCP_URL"]
BASE = URL.split("/mcp/")[0]
HEADERS = {
    "Accept": "application/json, text/event-stream",
    "MCP-Protocol-Version": "2025-11-25",
}


def call(tool):
    params = {"name": tool, "arguments": {}}
    return {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params}


def attempt(request):
    try:
        r = request()
        try:
            detail = r.json()["error"]  # {"error": "policy_denied"}
        except Exception:
            detail = r.text
        return {"status": r.status_code, "detail": str(detail)[:100]}
    except Exception as e:
        return {"error": type(e).__name__, "detail": str(e)[:100]}


def listing(path):
    try:
        return {"count": len(os.listdir(path))}
    except Exception as e:
        return {"error": type(e).__name__}


def post(url, tool):
    return lambda: httpx.post(url, json=call(tool), headers=HEADERS, timeout=TIMEOUT)


key = os.environ.get("NVIDIA_API_KEY", "")
out = {
    "other_host": attempt(lambda: httpx.get("https://example.com", timeout=TIMEOUT)),
    "other_claim": attempt(post(BASE + "/mcp/" + "x" * 43, "get_claim")),
    "other_tool": attempt(post(URL, "write_fraud_report")),
    "model_key": {
        "has_real_key": key.startswith("nvapi-"),
        "placeholder": key if key.startswith("openshell:resolve:env:") else None,
    },
    "host_files": {"/Users": listing("/Users"), "/host": listing("/host")},
}
print(json.dumps(out))
"""

# id, short name for the title, what the try does
TRIES = (
    ("other_host", "other host", "HTTPS request to example.com"),
    ("other_claim", "other claim", "tools/call on the MCP path of another claim"),
    ("other_tool", "tool outside the policy", "write_fraud_report on its own path"),
    ("model_key", "model key", "read the model key in the environment"),
    ("host_files", "host files", "list /Users and /host"),
)


class TryResult(BaseModel):
    id: str
    label: str
    what: str
    denied: bool
    detail: str


def _judge(id: str, raw: dict[str, Any]) -> tuple[bool, str]:
    if id == "model_key":
        placeholder = raw.get("placeholder")
        if raw.get("has_real_key") is not False:
            return False, "the environment holds a real key"
        if not isinstance(placeholder, str) or not _PLACEHOLDER.fullmatch(placeholder):
            return False, "the environment holds no placeholder"
        return True, f"the variable holds a placeholder: {placeholder}"
    if id == "host_files":
        listed = {p: v for p, v in raw.items() if isinstance(v, dict)}
        if set(listed) != {"/Users", "/host"}:
            return False, "no answer"
        text = ", ".join(
            f"{p}: {v.get('error') or str(v.get('count')) + ' entries'}"
            for p, v in listed.items()
        )
        return all("error" in v for v in listed.values()), text
    status, detail = raw.get("status"), str(raw.get("detail", ""))
    if status == 403:
        return True, f"HTTP 403 {detail}"
    if status is not None:
        return False, f"HTTP {status} {detail}"
    # the supervisor refuses the connection itself: "[Errno 13] Permission denied"
    return "Permission denied" in detail, f"{raw.get('error')}: {detail}"


def judge(raw: Any) -> list[TryResult]:
    """The five results of a probe line. A try with no usable answer is NOT denied."""
    data = raw if isinstance(raw, dict) else {}
    results = []
    for id, label, what in TRIES:
        item = data.get(id)
        if isinstance(item, dict):
            denied, detail = _judge(id, item)
        else:
            denied, detail = False, "the sandbox gave no answer"
        detail = clean(detail)
        results.append(
            TryResult(id=id, label=label, what=what, denied=denied, detail=detail)
        )
    return results
