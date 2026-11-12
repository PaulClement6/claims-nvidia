# Lessons

## Spike: LangGraph agent in OpenShell (issue #13, 2026-11-12)

- Pin `mcp` to a 1.x version. `pip install mcp` now gives 2.3.0, where `FastMCP` is gone. `langchain-mcp-adapters` 0.3.2 needs `mcp<2`.
- `openshell sandbox exec` does not pass the `ENV` of the image. Set non-secret variables with `--env` on `sandbox create`.
- The model writes identifiers with a special hyphen (U+2011) in some runs. Never match a policy number in the answer text. Read it from the tool result or the gateway event. In tests, normalise the hyphens.
- A Python MCP server (FastMCP) refuses a Host header it does not know (HTTP 421). The sandbox calls `host.openshell.internal`: list that name in `allowed_hosts`.
- In a shell script, `set -o pipefail` with `grep -q` gives false failures: `grep -q` closes the pipe early.
- The first `docker pull` of a base image took 4.4 minutes on the slow wifi. Pull the image before you need it, and build the agent image on the machine that runs the gateway.

## LangGraph agent (issue #47, 2026-11-12)

- `ChatOpenAI(max_tokens=...)` sends `max_completion_tokens`. The NVIDIA endpoint accepts it. mypy knows the field only under that name.
- A tool call with arguments that are not JSON does not come in `AIMessage.tool_calls`. It comes in `invalid_tool_calls`. Answer it with a `ToolMessage` error like any other bad call, or the history is broken.
- Keep the tool calls of a turn inside the node. If they go in the graph state, every checkpoint and every later prompt grows with them. The state keeps the conversation only.
- A graph that ends after each turn and starts again from `START` with a router on the saved `step` is enough for a chat. A failure in a code node leaves the step in the checkpoint, so the next message resumes there. No `interrupt()` is needed.
- Do not put the enforcement in a tool that tests and the scripted agent also call directly. Put it in the `call_tool` of the model agent, and make the MCP server call the same function.
- `position: sticky` in a scroll container with padding sticks at the content edge, not at the scrollport edge. Use a negative `top` equal to the padding, and add the same padding inside the sticky box, or the content scrolls in the gap above it.
- The shell wins over `.env` for the live tests. The `.env` of this machine can hold an older `AGENT_MODEL`: pass the model on the command line.
