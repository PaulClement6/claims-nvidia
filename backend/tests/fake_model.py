"""A fake LangChain chat model for the agent tests: no network, no key."""

import copy
import json
import re
from collections.abc import Callable, Sequence
from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage

from app.agent.graph import OPENING, SUMMARY_MARKER
from app.agent.phrases import is_done
from app.agent.scripted import ScriptedAgent

Policy = Callable[[list[BaseMessage], list[str]], AIMessage]
USAGE = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}


def text(content: str, finish: str = "stop") -> AIMessage:
    return AIMessage(
        content=content,
        usage_metadata={**USAGE, "output_token_details": {"reasoning": 5}},
        response_metadata={"finish_reason": finish},
    )


def calls(*items: tuple[str, dict[str, Any] | str]) -> AIMessage:
    """One reply with tool calls. A str as arguments is broken JSON: LangChain puts
    such a call in `invalid_tool_calls`."""
    good = [
        {"name": n, "args": a, "id": f"call_{i}", "type": "tool_call"}
        for i, (n, a) in enumerate(items)
        if not isinstance(a, str)
    ]
    bad = [
        {"name": n, "args": a, "id": f"call_{i}", "error": "bad JSON",
         "type": "invalid_tool_call"}
        for i, (n, a) in enumerate(items)
        if isinstance(a, str)
    ]  # fmt: skip
    return AIMessage(
        content="",
        tool_calls=good,
        invalid_tool_calls=bad,
        usage_metadata={**USAGE, "output_token_details": {"reasoning": 5}},
        response_metadata={"finish_reason": "tool_calls"},
    )


class _Bound:
    def __init__(self, owner: "FakeModel", names: list[str]) -> None:
        self.owner, self.names = owner, names

    def invoke(self, messages: list[BaseMessage]) -> AIMessage:
        return self.owner.answer(messages, self.names)


class FakeModel:
    """Plays a queue of answers (a callable or an exception stands for one answer),
    or asks a `policy`. `seen` holds a copy of the messages of each call and
    `bound` the tool names of each call."""

    model_name = "fake-model"

    def __init__(
        self,
        replies: Sequence[AIMessage | Exception | Callable[..., AIMessage]] = (),
        policy: Policy | None = None,
    ) -> None:
        self.replies = list(replies)
        self.policy = policy
        self.seen: list[list[BaseMessage]] = []
        self.bound: list[list[str]] = []

    def bind_tools(self, tools: Sequence[dict[str, Any]], **_: Any) -> _Bound:
        return _Bound(self, [t["function"]["name"] for t in tools])

    def answer(self, messages: list[BaseMessage], names: list[str]) -> AIMessage:
        self.seen.append(copy.deepcopy(messages))
        self.bound.append(names)
        if self.policy:
            return self.policy(messages, names)
        reply = self.replies.pop(0)  # IndexError: the agent asked for too much
        if callable(reply):
            reply = reply(messages)
        if isinstance(reply, Exception):
            raise reply
        return reply


def last_document_id(messages: list[BaseMessage]) -> str:
    found = re.findall(r"id (D-\w+)", json.dumps([str(m.content) for m in messages]))
    return str(found[-1])


def good_model(messages: list[BaseMessage], names: list[str]) -> AIMessage:
    """A model that does what the prompts say, for the three claim files."""
    summary = json.loads(str(messages[0].content).split(SUMMARY_MARKER, 1)[1])
    start = max(i for i, m in enumerate(messages) if m.type == "human")
    said = str(messages[start].content)
    made = [
        c["name"]
        for m in messages[start + 1 :]
        if isinstance(m, AIMessage)
        for c in m.tool_calls
    ]

    def next_of(*tools: str) -> str | None:
        return next((t for t in tools if t not in made), None)

    if "required_documents" in names:  # the documents node
        if said.startswith("The customer uploaded a document"):
            if tool := next_of("classify_document", "extract_document"):
                return calls((tool, {"document_id": last_document_id(messages)}))
            return text("Thank you. I read this document. Do you have another one?")
        if is_done(said):
            return calls(("customer_is_done", {}))
        if not made:
            return calls(("required_documents", {}))
        return text("Please upload the documents.")
    if said == OPENING:
        return text("Hello. What happened?")
    changes = ScriptedAgent(None)._fill(summary, said)  # type: ignore[arg-type]
    if changes and not made:
        return calls(("update_claim", changes))
    return text("Thank you. What else can you tell me?")


def use_fake_model(
    monkeypatch: pytest.MonkeyPatch, model: FakeModel | None = None
) -> FakeModel:
    """CLAIMS_MODE=nim, with the fake in place of ChatOpenAI."""
    import app.agent.graph_agent as graph_agent

    fake = model or FakeModel(policy=good_model)
    monkeypatch.setenv("CLAIMS_MODE", "nim")
    monkeypatch.setattr(graph_agent, "make_model", lambda: fake)
    return fake
