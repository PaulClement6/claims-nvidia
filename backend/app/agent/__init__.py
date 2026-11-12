"""The claim agents and the mode switch: `get_agent` is the only place that picks.

Nothing is imported at the top: `local` mode must not import LangChain or LangGraph,
and the graph module must not import the store (it also runs in the sandbox).
"""

import os
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from app.tools.gateway import Gateway


class Agent(Protocol):
    def handle(
        self, claim_id: str, text: str | None, document_id: str | None
    ) -> list[str]:
        """The messages for the customer. `text`, or `document_id`, or neither (the
        greeting)."""
        ...


def get_agent(gateway: "Gateway") -> Agent:
    mode = os.environ.get("CLAIMS_MODE", "local")
    if mode == "local":
        from app.agent.scripted import ScriptedAgent

        return ScriptedAgent(gateway)  # no key, no network: CI runs this
    if mode == "nim":
        from app.agent.graph_agent import GraphAgent

        return GraphAgent.from_env(gateway)  # no key stops the startup
    raise ValueError(f"unknown CLAIMS_MODE {mode!r} (known: local, nim)")
