"""Plain words and rules shared by the scripted agent and the LangGraph agent.

No import from the store, the gateway or LangChain: the graph module may use it in
the sandbox, and the scripted agent in `local` mode.
"""

import re
from typing import Any

FALLBACK = "Sorry, I could not answer just now. Please send your message again."
CLOSED = "Thank you. I have your claim and I will come back to you."
CORRECTION = (
    "Your last message is not allowed. Write it again for the customer: short and "
    "polite, with no mention of checks, scores, risk, outcomes, tools, models or "
    "internal steps, and no promise or judgement about the claim."
)

# What the customer must never read in a message of the model. Underscored words
# cover the tool names, the outcomes and the signal names; no English word has one.
FORBIDDEN = re.compile(
    r"fraud|\bscor(?:e|es|ed|ing)\b|\bsignals?\b|\b[RTC]\d{3}\b|\w+_\w+"
    r"|straight[ -]through|handler[ -]review|investigat|tamper"
    r"|reject|\bdeni(?:ed|al)\b|\bdeny\b|approv"
    r"|nemotron|nvidia|openshell|hermes|\bllm\b",
    re.I,
)

# What a customer types to say "I have nothing more to add", without case, spaces,
# apostrophes and punctuation. The Done button of the chat sends "done".
DONE = {"done", "no", "no more", "thats all", "nothing else", "finish"}


def check_message(message: str | None, model: str) -> str | None:
    """None when the customer may read it, else why not."""
    if not message or not message.strip():
        return "empty"
    if m := FORBIDDEN.search(message):
        return f"forbidden word: {m.group()}"
    if model.lower() in message.lower():
        return "model name"
    return None


def is_done(text: str) -> bool:
    words = re.sub(r"[^\w\s]", "", text.replace("'", "").replace("’", ""))
    return " ".join(words.lower().split()) in DONE


def next_state(s: dict[str, Any]) -> str:
    """The chat step of a claim summary. The Done button of the chat needs it."""
    for slot, state in (
        ("description", "ask_story"),
        ("incident_date", "ask_date"),
        ("claimed_amount", "ask_amount"),
    ):
        if not s[slot]:
            return state
    return "ask_documents"
