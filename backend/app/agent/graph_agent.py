"""The LangGraph claim agent in the backend process (`CLAIMS_MODE=nim`).

It has the same `handle` as the scripted agent. It builds the graph of
`app.agent.graph` with the gateway as the only way to the tools, the pipeline
events in the store, and a SQLite checkpointer (thread id = claim id).

The model is not trusted: this module holds the rules. It names the claim in every
tool call, and it refuses `assess_and_route` until the story is saved, every
document is read and the three checks ran (`app.tools.guard`, issue #12 rule 1).
The other two rules of issue #12 are in the graph: the customer gets the routing
text word for word, and every message of the model passes the output filter.
"""

import os
import sqlite3
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite import SqliteSaver
from pydantic import SecretStr

from app.agent.graph import CallTool, Model, build_graph, turn_input
from app.agent.phrases import FALLBACK
from app.tools.gateway import Gateway
from app.tools.guard import blocker

MAX_TOKENS = 2000  # room for the reasoning tokens of a reasoning model
TIMEOUT = 60.0  # seconds, per model call
RETRIES = 2  # the OpenAI client retries a 429, a 5xx and a timeout


class Refused(Exception):
    """The trusted code refused a tool call (rule 1)."""


def make_model() -> Model:
    """The chat model, from the environment. The key goes in the client only: not
    in a repr, not in an error text. It stops the startup when a setting is missing."""
    env = os.environ
    if not env.get("NVIDIA_KEY"):
        raise RuntimeError("CLAIMS_MODE=nim needs NVIDIA_KEY (see .env.example)")
    for name in ("AGENT_BASE_URL", "AGENT_MODEL"):
        if not env.get(name):
            raise RuntimeError(f"CLAIMS_MODE=nim needs {name} (see .env.example)")
    thinking = env.get("AGENT_THINKING", "")
    if thinking not in ("", "on", "off"):
        raise RuntimeError("AGENT_THINKING must be on, off or empty")
    extra: dict[str, Any] = {}
    if thinking:  # NVIDIA model cards: chat_template_kwargs.enable_thinking
        extra["chat_template_kwargs"] = {"enable_thinking": thinking == "on"}
    return ChatOpenAI(
        model=env["AGENT_MODEL"],
        base_url=env["AGENT_BASE_URL"],
        api_key=SecretStr(env["NVIDIA_KEY"]),
        max_completion_tokens=MAX_TOKENS,
        timeout=TIMEOUT,
        max_retries=RETRIES,
        extra_body=extra or None,
    )


class GraphAgent:
    def __init__(
        self, gateway: Gateway, model: Model, checkpointer: SqliteSaver
    ) -> None:
        self.gateway = gateway
        self.model = model
        self.checkpointer = checkpointer

    @classmethod
    def from_env(cls, gateway: Gateway) -> "GraphAgent":
        model = make_model()  # no key stops the startup, before any file is made
        path = Path(
            os.environ.get("CLAIMS_CHECKPOINTS", ".dev-data/checkpoints.sqlite")
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        saver = SqliteSaver(sqlite3.connect(path, check_same_thread=False))
        return cls(gateway, model, saver)

    def reset(self) -> None:
        """The demo reset: the checkpoints hold the conversation of every claim."""
        with self.checkpointer.cursor() as cur:
            cur.execute("DELETE FROM checkpoints")
            cur.execute("DELETE FROM writes")

    def caller(self, claim_id: str) -> CallTool:
        """`call_tool` of the graph: the gateway, for this claim."""

        def call_tool(name: str, inputs: dict[str, Any]) -> dict[str, Any]:
            if name == "assess_and_route" and (why := blocker(self.gateway, claim_id)):
                self.gateway.store.append_event(
                    claim_id, "agent", "refused", f"Refused {name}: {why}"[:80],
                    {"tool": name}, {"error": why},
                )  # fmt: skip
                raise Refused(why)
            # the claim id is ours: whatever the inputs say is replaced
            return self.gateway.call(claim_id, name, {**inputs, "claim_id": claim_id})

        return call_tool

    def handle(
        self, claim_id: str, text: str | None, document_id: str | None
    ) -> list[str]:
        store = self.gateway.store

        def emit(
            actor: Any,
            stage: str,
            title: str,
            inputs: dict[str, Any] | None = None,
            outputs: dict[str, Any] | None = None,
            model: str | None = None,
            duration: float | None = None,
        ) -> None:
            store.append_event(
                claim_id, actor, stage, title, inputs, outputs, model, duration
            )

        graph = build_graph(
            self.model, self.caller(claim_id), self.checkpointer,
            self.gateway.tool_specs(), emit,
        )  # fmt: skip
        try:
            config: RunnableConfig = {"configurable": {"thread_id": claim_id}}
            out = graph.invoke(turn_input(text, document_id), config)
        except Exception as e:
            # a tool failed in a code node: the step is in the checkpoint, so the
            # next message of the customer resumes there
            reason = f"the graph stopped: {type(e).__name__}"
            emit("agent", "fallback", "Fallback message", {}, {"reason": reason})
            return [FALLBACK]
        return [out.get("reply") or FALLBACK]
