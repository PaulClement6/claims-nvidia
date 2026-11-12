"""The chat model, from the environment: `ChatOpenAI` on an OpenAI-compatible endpoint.

Used by the graph agent in the backend (`nim` mode) and by the turn process in the
sandbox, so the settings are the same in both. The key goes in the client only: not
in a repr, not in an error text. A missing setting stops the startup.
"""

import os
from typing import Any

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from app.agent.graph import Model

MAX_TOKENS = 2000  # room for the reasoning tokens of a reasoning model
TIMEOUT = 60.0  # seconds, per model call
RETRIES = 2  # the OpenAI client retries a 429, a 5xx and a timeout


def make_model(who: str = "CLAIMS_MODE=nim", key_var: str = "NVIDIA_KEY") -> Model:
    """`key_var` names the variable that holds the key. In the sandbox it holds a
    placeholder that OpenShell swaps for the real key on the way out."""
    env = os.environ
    if not env.get(key_var):
        raise RuntimeError(f"{who} needs {key_var} (see .env.example)")
    for name in ("AGENT_BASE_URL", "AGENT_MODEL"):
        if not env.get(name):
            raise RuntimeError(f"{who} needs {name} (see .env.example)")
    thinking = env.get("AGENT_THINKING", "")
    if thinking not in ("", "on", "off"):
        raise RuntimeError("AGENT_THINKING must be on, off or empty")
    extra: dict[str, Any] = {}
    if thinking:  # NVIDIA model cards: chat_template_kwargs.enable_thinking
        extra["chat_template_kwargs"] = {"enable_thinking": thinking == "on"}
    return ChatOpenAI(
        model=env["AGENT_MODEL"],
        base_url=env["AGENT_BASE_URL"],
        api_key=SecretStr(env[key_var]),
        max_completion_tokens=MAX_TOKENS,
        timeout=TIMEOUT,
        max_retries=RETRIES,
        extra_body=extra or None,
    )
