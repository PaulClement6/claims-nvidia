"""A fake chat client for the LLM agent tests: no network, no key."""

import copy
import json
import re
from collections.abc import Callable
from typing import Any

import pytest

from app.agent.chat import ChatError, ChatReply, HttpChat, ToolCall
from app.agent.llm import OPENING, SUMMARY_MARKER
from app.agent.scripted import ScriptedAgent, is_done

Policy = Callable[[list[dict[str, Any]], list[dict[str, Any]]], ChatReply]


def text(content: str | None, finish: str = "stop") -> ChatReply:
    return ChatReply(
        text=content,
        finish_reason=finish,
        usage={"prompt_tokens": 100, "completion_tokens": 20, "reasoning_tokens": 5},
    )


def calls(*items: tuple[str, dict[str, Any] | str]) -> ChatReply:
    """One reply with tool calls. A str as arguments is sent as it is (broken JSON)."""
    return ChatReply(
        text=None,
        tool_calls=[
            ToolCall(f"call_{i}", name, a if isinstance(a, str) else json.dumps(a))
            for i, (name, a) in enumerate(items)
        ],
        finish_reason="tool_calls",
        usage={"prompt_tokens": 100, "completion_tokens": 20, "reasoning_tokens": 5},
    )


class FakeChat:
    """Plays a queue of answers (a callable or an exception stands for one answer),
    or asks a `policy`. `seen` holds a copy of the messages of each call."""

    model = "fake-model"

    def __init__(
        self,
        replies: list[ChatReply | ChatError | Callable[..., ChatReply]] = (),  # type: ignore[assignment]
        policy: Policy | None = None,
    ) -> None:
        self.replies = list(replies)
        self.policy = policy
        self.seen: list[list[dict[str, Any]]] = []
        self.tools: list[dict[str, Any]] = []

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ChatReply:
        self.seen.append(copy.deepcopy(messages))
        self.tools = tools
        if self.policy:
            return self.policy(messages, tools)
        reply = self.replies.pop(0)  # IndexError: the agent asked for too much
        if callable(reply):
            reply = reply(messages)
        if isinstance(reply, Exception):
            raise reply
        return reply


def last_document_id(messages: list[dict[str, Any]]) -> str:
    found = re.findall(r"id (D-\w+)", json.dumps(messages))
    return str(found[-1])


def good_model(
    messages: list[dict[str, Any]], tools: list[dict[str, Any]]
) -> ChatReply:
    """A model that does what the system prompt says, for the three claim files."""
    summary = json.loads(messages[0]["content"].split(SUMMARY_MARKER, 1)[1])
    start = max(i for i, m in enumerate(messages) if m["role"] == "user")
    said = messages[start]["content"]
    made = [
        c["function"]["name"]
        for m in messages[start + 1 :]
        for c in m.get("tool_calls", [])
    ]

    def next_of(*names: str) -> str | None:
        return next((n for n in names if n not in made), None)

    if said == OPENING:
        return text("Hello. What happened?")
    if said.startswith("The customer uploaded a document"):
        if tool := next_of("classify_document", "extract_document"):
            return calls((tool, {"document_id": last_document_id(messages)}))
        return text("Thank you. I read this document. Do you have another one?")
    if is_done(said):
        if tool := next_of(
            "run_checks", "check_tampering", "check_consistency", "assess_and_route"
        ):
            return calls((tool, {}))
        return text("Your claim is approved and we will pay you.")  # never sent
    changes = ScriptedAgent(None)._fill(summary, said)  # type: ignore[arg-type]
    if changes and not made:
        return calls(("update_claim", changes))
    return text("Thank you. What else can you tell me?")


def use_fake_model(
    monkeypatch: pytest.MonkeyPatch, chat: FakeChat | None = None
) -> FakeChat:
    """CLAIMS_MODE=nim, with the fake in place of the HTTP client."""
    fake = chat or FakeChat(policy=good_model)
    monkeypatch.setenv("CLAIMS_MODE", "nim")
    monkeypatch.setattr(HttpChat, "from_env", classmethod(lambda cls: fake))
    return fake
