"""The claim agents and the mode switch: `get_agent` is the only place that picks."""

import os
from typing import Protocol

from app.agent.chat import HttpChat
from app.agent.llm import LlmAgent
from app.agent.scripted import ScriptedAgent
from app.tools.gateway import Gateway


class Agent(Protocol):
    def handle(
        self, claim_id: str, text: str | None, document_id: str | None
    ) -> list[str]:
        """The messages for the customer. `text`, or `document_id`, or neither (the
        greeting)."""
        ...


def get_agent(gateway: Gateway) -> Agent:
    mode = os.environ.get("CLAIMS_MODE", "local")
    if mode == "local":
        return ScriptedAgent(gateway)  # no key, no network: CI runs this
    if mode == "nim":
        return LlmAgent(gateway, HttpChat.from_env())  # no key stops the startup
    raise ValueError(f"unknown CLAIMS_MODE {mode!r} (known: local, nim)")
