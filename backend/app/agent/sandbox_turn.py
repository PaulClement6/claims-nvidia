"""One turn of the claim graph, inside the OpenShell sandbox (issue #14).

    printf '%s' '{"text": "My trip was cancelled", "document_id": null}' \\
      | python -m app.agent.sandbox_turn --thread CL-1a2b3c4d

The backend runs it with `openshell sandbox exec`. The customer text comes on stdin,
never as an argument. One process is one turn: the conversation is kept by the SQLite
checkpointer in the scratch folder (thread id = claim id). The last line of stdout is
one JSON line:

    {"reply": "...", "events": [{"actor", "stage", "title", ...}], "seconds": 4.2}

The backend treats it as data. `events` are the node and model events of the graph,
shown in the pipeline panel and nothing more.

This is the graph of the backend, the same file (`app.agent.graph`). Its tools are MCP
tools: `MCP_URL` is the path of this claim on the backend (`/mcp/<token>`), the only
way out apart from the model. The model key is a placeholder in `NVIDIA_API_KEY`.
This module imports nothing from the store, the gateway or FastAPI.
"""

import argparse
import asyncio
import json
import os
import sqlite3
import sys
import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite import SqliteSaver
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.agent.graph import CallTool, Model, build_graph, turn_input
from app.agent.model import make_model
from app.agent.phrases import FALLBACK

CHECKPOINTS = "/sandbox/state/checkpoints.sqlite"
TOOL_TIMEOUT = 120  # seconds, for one MCP call (a document read can be slow)


async def _session(url: str, work: Callable[[ClientSession], Any]) -> Any:
    """One MCP session: initialize, work, close. The server is stateless, so a
    session per call costs three small local requests."""
    timeout = timedelta(seconds=TOOL_TIMEOUT)
    async with streamable_http_client(url) as (read, write, _):
        async with ClientSession(read, write, read_timeout_seconds=timeout) as session:
            await session.initialize()
            return await work(session)


def tool_specs(url: str) -> list[dict[str, Any]]:
    """The tool list of the backend, in the OpenAI function format."""

    async def work(session: ClientSession) -> list[dict[str, Any]]:
        listed = await session.list_tools()
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description or "",
                    "parameters": t.inputSchema,
                },
            }
            for t in listed.tools
        ]

    out: list[dict[str, Any]] = asyncio.run(_session(url, work))
    return out


def mcp_caller(url: str) -> CallTool:
    """`call_tool` of the graph: an MCP `tools/call` on the path of this claim."""

    async def call(name: str, inputs: dict[str, Any]) -> dict[str, Any]:
        async def work(session: ClientSession) -> dict[str, Any]:
            result = await session.call_tool(name, inputs)
            text = "".join(getattr(c, "text", "") for c in result.content)
            if result.isError:
                raise RuntimeError(text or f"{name} failed")
            out: dict[str, Any] = json.loads(text)
            return out

        out: dict[str, Any] = await _session(url, work)
        return out

    def call_tool(name: str, inputs: dict[str, Any]) -> dict[str, Any]:
        return asyncio.run(call(name, inputs))

    return call_tool


def run_turn(
    thread: str,
    payload: dict[str, Any],
    model: Model,
    url: str,
    checkpoints: str = CHECKPOINTS,
) -> dict[str, Any]:
    """The turn: the graph on this thread. Never raises: a failure is the fallback
    message and an event."""
    start = time.perf_counter()
    events: list[dict[str, Any]] = []

    def emit(
        actor: str,
        stage: str,
        title: str,
        inputs: dict[str, Any] | None = None,
        outputs: dict[str, Any] | None = None,
        model: str | None = None,
        duration: float | None = None,
    ) -> None:
        events.append(
            {
                "actor": actor,
                "stage": stage,
                "title": title,
                "inputs": inputs or {},
                "outputs": outputs or {},
                "model": model,
                "duration": duration,
            }
        )

    reply: str | None = FALLBACK
    try:
        Path(checkpoints).parent.mkdir(parents=True, exist_ok=True)
        saver = SqliteSaver(sqlite3.connect(checkpoints, check_same_thread=False))
        graph = build_graph(model, mcp_caller(url), saver, tool_specs(url), emit)
        config: RunnableConfig = {"configurable": {"thread_id": thread}}
        out = graph.invoke(
            turn_input(payload.get("text"), payload.get("document_id")), config
        )
        reply = out.get("reply") or FALLBACK
    except Exception as e:  # a tool failed in a code node: the next message resumes
        emit("agent", "fallback", "Fallback message", {}, {"reason": _why(e)})
    return {
        "reply": reply,
        "events": events,
        "seconds": round(time.perf_counter() - start, 2),
    }


def _why(e: Exception) -> str:
    """Why the graph stopped, with no text of the customer or of a document."""
    return f"the graph stopped: {type(e).__name__}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--thread", required=True)
    args = parser.parse_args()
    payload = json.loads(sys.stdin.read())
    # the key is a placeholder in NVIDIA_API_KEY; the supervisor swaps it on the way out
    model = make_model("the sandbox", key_var="NVIDIA_API_KEY")
    line = run_turn(args.thread, payload, model, os.environ["MCP_URL"])
    print(json.dumps(line, default=str))


if __name__ == "__main__":
    main()
