"""Plays the three claim files through the HTTP API with the words a real customer
would type. Used by the live test (`pytest -m live`) and `scripts/measure_agent.py`.

It reads `.env` at the repository root into a dict and never prints a value.
"""

import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.agent.phrases import FALLBACK
from app.views import TO_A_PERSON, VALIDATED
from tests.reader_contract import MOCK

ROOT = Path(__file__).resolve().parents[2]


def load_env(path: Path = ROOT / ".env") -> dict[str, str]:
    env: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip("'\"")
    return env


@dataclass(frozen=True)
class ClaimFile:
    customer: str
    policy: str
    folder: str
    files: tuple[str, ...]  # in the order a customer would send them
    stories: tuple[tuple[str, ...], ...]  # three wordings; each is a list of messages
    date_text: str
    amount_text: str
    # what the claim must hold when the agent is right
    incident_date: str
    claimed_amount: float
    outcome: str
    customer_text: str


CLAIM_FILES = {
    "travel": ClaimFile(
        "C-1001",
        "TRV-2026-0417",
        "claim-1-travel",
        (
            "booking-confirmation.pdf",
            "cancellation-confirmation.pdf",
            "medical-certificate.pdf",
        ),
        (
            (
                "Hi, I had to cancel my trip because I came down with the flu. "
                "This was on 3 August 2026 and I would like to claim CHF 1,500.",
            ),
            (
                "My holiday was cancelled, I was sick with a fever. It happened on "
                "03.08.2026. I want to claim 1500 francs.",
            ),
            ("Hello. I got ill just before my holiday so I had to cancel it.",),
        ),
        "3 August 2026",
        "CHF 1,500",
        "2026-08-03",
        1500.0,
        "straight_through",
        VALIDATED,
    ),
    "property": ClaimFile(
        "C-1002",
        "HH-2024-0933",
        "claim-2-property",
        ("repair-invoice.pdf", "damage-photo-1.jpg", "damage-photo-2.jpg"),
        (
            (
                "Hello, a pipe burst in my kitchen and there was water everywhere. "
                "It happened on 14 September 2026. The repair cost me CHF 3,200.",
            ),
            (
                "We had a water leak under the kitchen sink on 2026-09-14 and the "
                "floor is ruined. I am claiming 3200 CHF.",
            ),
            ("Hi, my kitchen was flooded after a water pipe broke.",),
        ),
        "14 September 2026",
        "CHF 3,200",
        "2026-09-14",
        3200.0,
        "handler_review",
        TO_A_PERSON,
    ),
    "health": ClaimFile(
        "C-1003",
        "ACC-2026-2210",
        "claim-3-health",
        ("medical-invoice.pdf", "medical-report.pdf"),
        (
            (
                "Hi, I slipped on some ice and sprained my ankle on 25 October 2026. "
                "The clinic invoice is CHF 4,450 and I would like it refunded.",
            ),
            (
                "I had an accident while jogging, I twisted my ankle. Date: "
                "25.10.2026. Amount: CHF 4450.",
            ),
            ("Good morning. I fell down the stairs and hurt my ankle.",),
        ),
        "25 October 2026",
        "CHF 4,450",
        "2026-10-25",
        4450.0,
        "investigation",
        TO_A_PERSON,
    ),
}
DONE = (
    "No, that's everything. Thanks!",
    "That is all, I have nothing else to add.",
    "Nope, I think that's all.",
)
INSISTS = "Yes, I am done. Please go ahead."


@dataclass
class Run:
    kind: str
    variant: int
    claim_id: str = ""
    outcome: str | None = None
    customer_text: str | None = None
    incident_date: str | None = None
    claimed_amount: float | None = None
    closed: bool = False
    turn_seconds: list[float] = field(default_factory=list)
    transcript: list[tuple[str, str]] = field(default_factory=list)
    model_calls: int = 0
    tool_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    rate_limited: int = 0
    model_errors: int = 0
    refused_calls: int = 0
    fallbacks: int = 0
    llm_times: list[float] = field(default_factory=list)  # epoch seconds of each call
    problems: list[str] = field(default_factory=list)  # model errors, fallbacks
    error: str | None = None

    @property
    def ok(self) -> bool:
        spec = CLAIM_FILES[self.kind]
        return (
            self.error is None
            and self.outcome == spec.outcome
            and self.customer_text == spec.customer_text
        )

    @property
    def fields_ok(self) -> bool:
        spec = CLAIM_FILES[self.kind]
        return (self.incident_date, self.claimed_amount) == (
            spec.incident_date,
            spec.claimed_amount,
        )

    def summary(self) -> dict[str, Any]:
        return {
            "claim": self.kind,
            "variant": self.variant,
            "ok": self.ok,
            "fields_ok": self.fields_ok,
            "outcome": self.outcome,
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
            "median_turn_s": round(statistics.median(self.turn_seconds or [0]), 2),
            "worst_turn_s": round(max(self.turn_seconds or [0]), 2),
            "tokens": self.prompt_tokens + self.completion_tokens,
            "rate_limited": self.rate_limited,
            "model_errors": self.model_errors,
            "refused_calls": self.refused_calls,
            "fallbacks": self.fallbacks,
            "problems": self.problems,
            "error": self.error,
        }


def play(client: TestClient, kind: str, variant: int) -> Run:
    spec = CLAIM_FILES[kind]
    run = Run(kind, variant)

    def timed(method: str, url: str, **kw: Any) -> Any:
        start = time.perf_counter()
        res = client.request(method, url, **kw)
        run.turn_seconds.append(time.perf_counter() - start)
        if res.status_code >= 300:
            raise RuntimeError(f"{method} {url}: HTTP {res.status_code}")
        return res.json()

    def say(text: str) -> None:
        timed("POST", f"/claims/{run.claim_id}/messages", json={"text": text})

    def claim() -> dict[str, Any]:
        out: dict[str, Any] = client.get(f"/claims/{run.claim_id}").json()
        return out

    try:
        run.claim_id = client.post(
            "/claims", json={"customer_id": spec.customer}
        ).json()["claim_id"]
        timed(
            "PUT", f"/claims/{run.claim_id}/policy", json={"policy_number": spec.policy}
        )
        for text in spec.stories[variant]:
            say(text)
        for _ in range(4):  # the agent asks for the date or the amount: answer it
            c = claim()
            asked = run_last_agent_text(client, run.claim_id).lower()
            if c["agent_state"] == "ask_documents" or "upload" in asked:
                break
            if "amount" in asked:
                say(f"I am claiming {spec.amount_text}.")
            elif "date" in asked or "when" in asked:
                say(f"It happened on {spec.date_text}.")
            else:
                say(f"It was on {spec.date_text}. I am claiming {spec.amount_text}.")
        for name in spec.files:
            data = (MOCK / spec.folder / name).read_bytes()
            timed(
                "POST",
                f"/claims/{run.claim_id}/documents",
                files={"file": (name, data)},
            )
        say(DONE[variant])
        for _ in range(2):  # the agent asks once more: say it again
            if claim()["agent_state"] == "closed":
                break
            say(INSISTS)
    except Exception as e:  # a failed claim is a result, not a crash of the measure
        run.error = f"{type(e).__name__}: {e}"
    collect(client, run, claim() if run.claim_id else {})
    return run


def run_last_agent_text(client: TestClient, claim_id: str) -> str:
    messages = client.get(f"/claims/{claim_id}/messages").json()
    return str(
        next((m["text"] for m in reversed(messages) if m["role"] == "agent"), "")
    )


def collect(client: TestClient, run: Run, c: dict[str, Any]) -> None:
    run.outcome, run.closed = c.get("outcome"), c.get("agent_state") == "closed"
    run.incident_date, run.claimed_amount = (
        c.get("incident_date"),
        c.get("claimed_amount"),
    )
    if not run.claim_id:
        return
    for m in client.get(f"/claims/{run.claim_id}/messages").json():
        run.transcript.append((m["role"], m["text"]))
    run.customer_text = next(
        (t for r, t in reversed(run.transcript) if r == "agent"), None
    )
    run.fallbacks = sum(1 for r, t in run.transcript if r == "agent" and t == FALLBACK)
    store = client.app.state.store  # type: ignore[attr-defined]
    for e in store.events_since(run.claim_id, 0):
        if e.stage == "refused":
            run.refused_calls += 1
        if e.stage == "fallback":
            run.problems.append(e.outputs["reason"])
        if e.stage == "llm" and "error" in e.outputs:
            run.problems.append(e.outputs["error"])
        if e.stage != "llm":
            continue
        run.model_calls += 1
        run.llm_times.append(e.time.timestamp())
        run.tool_calls += len(e.outputs.get("tool_calls", []))
        run.rate_limited += e.outputs.get("rate_limited", 0)
        run.model_errors += 1 if "error" in e.outputs else 0
        usage = e.outputs.get("usage", {})
        run.prompt_tokens += usage.get("prompt_tokens", 0)
        run.completion_tokens += usage.get("completion_tokens", 0)
        run.reasoning_tokens += usage.get("reasoning_tokens", 0)
