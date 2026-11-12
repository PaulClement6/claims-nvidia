"""Measure the LangGraph agent: the three claim files, N runs each, per model.

    cd backend && uv run python ../scripts/measure_agent.py          # the default model
    cd backend && uv run python ../scripts/measure_agent.py --models nvidia/x --runs 1
    cd backend && uv run python ../scripts/measure_agent.py --mode sandbox --runs 2

It starts the backend in `nim` mode in this process (TestClient, a temporary database),
plays each claim file with the words of a customer (`backend/tests/live_claims.py`)
and prints a markdown table. The key is read from `.env` into the process environment
and never printed or written. With `--mode sandbox` the backend runs in a thread on a
free loopback port, with `CLAIMS_MODE=sandbox` (needs `infra/sandbox/setup.sh`); the
key is not read at all, the provider of OpenShell holds it. It also prints the seconds
to create a sandbox and of the `exec` of each turn. The results go to a JSON file
(transcripts of every run included: mock data only). Needs the network (and NVIDIA_KEY
in `nim` mode).
"""

import argparse
import json
import os
import socket
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402
from tests.live_claims import CLAIM_FILES, Run, load_env, play  # noqa: E402

MODELS = ["nvidia/nemotron-3.5-lightning-30b-a3b"]  # D17


SANDBOX_SECONDS: dict[str, list[float]] = {"create": [], "exec": []}


class LiveClient(httpx.Client):
    """A client of a real server, with the `app` that `play` reads the events from."""

    def __init__(self, app: Any, port: int) -> None:
        super().__init__(base_url=f"http://127.0.0.1:{port}", timeout=300)
        self.app = app


def measure(
    model: str, runs: int, pause: float, thinking: str, mode: str = "nim"
) -> list[Run]:
    env = load_env()
    # in sandbox mode the key stays in the provider of OpenShell: not read here
    for name in ("NVIDIA_KEY", "AGENT_BASE_URL"):
        if name == "AGENT_BASE_URL" or mode == "nim":
            os.environ[name] = env.get(name, os.environ.get(name, ""))
    os.environ.update(CLAIMS_MODE=mode, AGENT_MODEL=model, AGENT_THINKING=thinking)
    done: list[Run] = []
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CLAIMS_DB"] = f"{tmp}/claims.db"
        os.environ["CLAIMS_UPLOADS"] = f"{tmp}/uploads"
        os.environ["CLAIMS_CHECKPOINTS"] = f"{tmp}/checkpoints.sqlite"
        os.environ["CLAIMS_SANDBOX_DIR"] = f"{tmp}/sandboxes"
        server = None
        if mode == "sandbox":  # a real server: the sandbox calls the backend over HTTP
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            os.environ["BACKEND_PORT"] = str(port)
            app = create_app()
            server = uvicorn.Server(
                uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
            )
            threading.Thread(target=server.run, daemon=True).start()
            while not server.started:
                time.sleep(0.1)
            client: Any = LiveClient(app, port)
        else:
            client = TestClient(create_app()).__enter__()
        try:
            for variant in range(runs):
                for kind in CLAIM_FILES:
                    run = play(client, kind, variant)
                    done.append(run)
                    print(
                        f"  {model[-24:]} {kind} #{variant + 1}: {run.summary()}",
                        flush=True,
                    )
                    for e in client.app.state.store.events_since(run.claim_id, 0):
                        if e.actor == "sandbox" and e.duration is not None:
                            key = "create" if e.stage == "created" else "exec"
                            if e.stage in ("created", "turn"):
                                SANDBOX_SECONDS[key].append(e.duration)
                    # wait after a 429 (the limit is per minute), a little between runs
                    time.sleep(60 if run.rate_limited else pause)
        finally:
            client.close() if server else client.__exit__(None, None, None)
            if server:
                server.should_exit = True
    return done


def table(model: str, runs: list[Run]) -> str:
    def row(label: str, rs: list[Run]) -> str:
        n = len(rs)
        turns = [t for r in rs for t in r.turn_seconds]
        cells = [
            label,
            f"{sum(r.ok for r in rs)}/{n}",
            f"{sum(r.fields_ok for r in rs)}/{n}",
            f"{sum(r.model_calls for r in rs) / n:.1f}",
            f"{sum(r.tool_calls for r in rs) / n:.1f}",
            f"{statistics.median(turns):.1f} / {max(turns):.1f}",
            f"{sum(r.prompt_tokens + r.completion_tokens for r in rs) / n:,.0f}",
            str(sum(r.rate_limited for r in rs)),
            str(sum(r.fallbacks for r in rs)),
        ]
        return "| " + " | ".join(cells) + " |"

    titles = [
        "Claim file",
        "Right outcome",
        "Right date and amount",
        "Model calls",
        "Tool calls",
        "Seconds per turn (median / worst)",
        "Tokens",
        "429",
        "Fallback messages",
    ]
    head = ["| " + " | ".join(titles) + " |", "|" + "---|" * len(titles)]
    rows = [row(k, [r for r in runs if r.kind == k]) for k in CLAIM_FILES]
    return "\n".join([f"**{model}**\n", *head, *rows, row("All", runs)])


def limit_seen(runs: list[Run]) -> str:
    """How many model calls came in the 60 s before the first 429 of this model."""
    times = sorted(t for r in runs for t in r.llm_times)
    if not any(r.rate_limited for r in runs):
        return "no 429"
    first = next(r for r in runs if r.rate_limited)
    hit = first.llm_times[-1] if first.llm_times else times[-1]
    return (
        f"first 429 after {sum(1 for t in times if hit - 60 <= t <= hit)} calls in 60 s"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=MODELS)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--pause", type=float, default=5.0, help="seconds between runs")
    ap.add_argument("--thinking", default="", choices=["", "on", "off"])
    ap.add_argument("--mode", default="nim", choices=["nim", "sandbox"])
    ap.add_argument("--out", default=str(ROOT / ".dev-data" / "measure.json"))
    args = ap.parse_args()

    results: dict[str, Any] = {}
    for model in args.models:
        runs = measure(model, args.runs, args.pause, args.thinking, args.mode)
        print("\n" + table(model, runs), f"\n429: {limit_seen(runs)}\n", flush=True)
        if args.mode == "sandbox":
            for label, values in SANDBOX_SECONDS.items():
                print(
                    f"sandbox {label}: n={len(values)} median "
                    f"{statistics.median(values):.2f} s, worst {max(values):.2f} s"
                )
        results[model] = {
            "thinking": args.thinking or "default",
            "limit": limit_seen(runs),
            "runs": [{**r.summary(), "transcript": r.transcript} for r in runs],
        }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(results, indent=2))
    print(f"results: {args.out}")


if __name__ == "__main__":
    main()
