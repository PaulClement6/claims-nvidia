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
from langgraph.checkpoint.sqlite import SqliteSaver

from app.agent.graph import CallTool, Model, build_graph, turn_input
from app.agent.model import make_model
from app.agent.phrases import FALLBACK
from app.tools.gateway import Gateway
from app.tools.guard import Refused as Refused  # re-export: tests import it here
from app.tools.guard import guarded_call


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
            return guarded_call(self.gateway, claim_id, name, inputs)

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
