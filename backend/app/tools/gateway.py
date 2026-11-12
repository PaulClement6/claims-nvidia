"""The tool gateway: the only door for the agent (non-negotiable 5).

Every call writes exactly one PipelineEvent, also when the tool fails.
"""

import time
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Any

from app.store import Store


@dataclass(frozen=True)
class Tool:
    fn: Callable[..., dict[str, Any]]
    model: str | None = None
    visible: Collection[str] | None = None
    title: str | None = None
    description: str | None = None  # a tool with no description is not for the LLM
    parameters: dict[str, Any] | None = None  # JSON schema, without `claim_id`


class Gateway:
    def __init__(self, store: Store) -> None:
        self.store = store
        self._tools: dict[str, Tool] = {}

    def register(
        self,
        name: str,
        fn: Callable[..., dict[str, Any]],
        model: str | None = None,
        visible: Collection[str] | None = None,
        title: str | None = None,
        description: str | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> None:
        """`visible`: the output keys the caller gets back. The event holds them all.
        `title`: the event title, when "Tool name" is not enough.
        `description` and `parameters`: what the LLM agent sees (a JSON schema of the
        arguments it chooses; the runner adds `claim_id`, so the schema has none)."""
        self._tools[name] = Tool(fn, model, visible, title, description, parameters)

    def tool_specs(self) -> list[dict[str, Any]]:
        """The tools the LLM may call, in the OpenAI function format."""
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": t.description,
                    "parameters": t.parameters
                    or {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                },
            }
            for name, t in self._tools.items()
            if t.description
        ]

    def call(
        self, claim_id: str, tool_name: str, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        tool = self._tools.get(tool_name)
        outputs: dict[str, Any] = {}
        start = time.perf_counter()
        try:
            if tool is None:
                raise KeyError(f"unknown tool: {tool_name}")
            outputs = tool.fn(**inputs)
            if tool.visible is None:
                return outputs
            return {k: v for k, v in outputs.items() if k in tool.visible}
        except Exception as e:
            outputs = {"error": e.args[0] if isinstance(e, KeyError) else str(e)}
            raise
        finally:
            self.store.append_event(
                claim_id,
                "tool",
                tool_name,
                (tool and tool.title) or tool_name.replace("_", " ").capitalize(),
                inputs,
                outputs,
                tool.model if tool else None,
                time.perf_counter() - start,
            )
