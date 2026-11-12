"""The tool gateway: the only door for the agent (non-negotiable 5).

Every call writes exactly one PipelineEvent, also when the tool fails.
"""

import time
from collections.abc import Callable, Collection
from typing import Any

from app.store import Store


class Gateway:
    def __init__(self, store: Store) -> None:
        self.store = store
        self._tools: dict[
            str,
            tuple[
                Callable[..., dict[str, Any]],
                str | None,
                Collection[str] | None,
                str | None,
            ],
        ] = {}

    def register(
        self,
        name: str,
        fn: Callable[..., dict[str, Any]],
        model: str | None = None,
        visible: Collection[str] | None = None,
        title: str | None = None,
    ) -> None:
        """`visible`: the output keys the caller gets back. The event holds them all.
        `title`: the event title, when "Tool name" is not enough."""
        self._tools[name] = (fn, model, visible, title)

    def call(
        self, claim_id: str, tool_name: str, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        fn, model, visible, title = self._tools.get(tool_name, (None, None, None, None))
        outputs: dict[str, Any] = {}
        start = time.perf_counter()
        try:
            if fn is None:
                raise KeyError(f"unknown tool: {tool_name}")
            outputs = fn(**inputs)
            if visible is None:
                return outputs
            return {k: v for k, v in outputs.items() if k in visible}
        except Exception as e:
            outputs = {"error": e.args[0] if isinstance(e, KeyError) else str(e)}
            raise
        finally:
            self.store.append_event(
                claim_id,
                "tool",
                tool_name,
                title or tool_name.replace("_", " ").capitalize(),
                inputs,
                outputs,
                model,
                time.perf_counter() - start,
            )
