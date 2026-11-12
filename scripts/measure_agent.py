"""Measure the LLM agent: the three claim files, N runs each, one backend per model.

    cd backend && uv run python ../scripts/measure_agent.py            # both models
    cd backend && uv run python ../scripts/measure_agent.py --models nvidia/x --runs 1

It starts the backend in `nim` mode in this process (TestClient, a temporary database),
plays each claim file with the words of a customer (`backend/tests/live_claims.py`)
and prints a markdown table. The key is read from `.env` into the process environment
and never printed or written. The results go to a JSON file (transcripts of every
run included: mock data only). Needs the network and NVIDIA_KEY.
"""

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402
from tests.live_claims import CLAIM_FILES, Run, load_env, play  # noqa: E402

MODELS = [
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
    "nvidia/nemotron-3.5-lightning-30b-a3b",
]


def measure(model: str, runs: int, pause: float, thinking: str) -> list[Run]:
    env = load_env()
    for name in ("NVIDIA_KEY", "AGENT_BASE_URL"):
        os.environ[name] = env.get(name, os.environ.get(name, ""))
    os.environ.update(CLAIMS_MODE="nim", AGENT_MODEL=model, AGENT_THINKING=thinking)
    done: list[Run] = []
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["CLAIMS_DB"] = f"{tmp}/claims.db"
        os.environ["CLAIMS_UPLOADS"] = f"{tmp}/uploads"
        with TestClient(create_app()) as client:
            for variant in range(runs):
                for kind in CLAIM_FILES:
                    run = play(client, kind, variant)
                    done.append(run)
                    print(
                        f"  {model[-24:]} {kind} #{variant + 1}: {run.summary()}",
                        flush=True,
                    )
                    # wait after a 429 (the limit is per minute), a little between runs
                    time.sleep(60 if run.rate_limited else pause)
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
    ap.add_argument("--out", default=str(ROOT / ".dev-data" / "measure.json"))
    args = ap.parse_args()

    results: dict[str, Any] = {}
    for model in args.models:
        runs = measure(model, args.runs, args.pause, args.thinking)
        print("\n" + table(model, runs), f"\n429: {limit_seen(runs)}\n", flush=True)
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
