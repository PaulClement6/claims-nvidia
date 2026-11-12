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

## Sandbox mode (issue #14, 2026-11-12)

- A `protocol: mcp` endpoint in the policy governs the whole host and port, not only its `path`. A request with no `MCP-Protocol-Version` header gets 403 `mcp_protocol_version_not_allowed` on any path, `/health` included. To prove that another path is denied, send the header: then the answer is 403 `policy_denied` with the rule `GET /health`. A probe that forgets the header proves nothing.
- The schema of a tool is what the model sees. The code nodes of the graph send arguments the model must not see (`agent_state` to `update_claim`). The MCP SDK checks the arguments against the schema by default (`call_tool(validate_input=True)`), so the code nodes would fail. Switch it off and let the tool function refuse unknown arguments.
- The MCP client of the SDK asks `tools/list` again after a `tools/call` (it reads the output schema). The policy needs `tools/list` for that, and the log then shows `tools/list` after the call.
- A placeholder in the comment of a template is replaced too. A header comment that named `@TOOL_RULES@` made the rendered YAML invalid. Name the places in words in a comment.
- uvicorn writes the path of every request in its access log. A token in the path is then in the log: filter the record (`RedactToken`). The OpenShell log of the sandbox holds the whole path as well.
- Do not send a key into a sandbox to look for it. A real NVIDIA key has the form `nvapi-...`: look for that form in the environment and the files.
- A test that calls the API of a backend in `sandbox` mode starts a sandbox on each greeting and each upload, and the new sandbox gets a new token. A test that made a token by hand must store its documents and its policy without a turn of the agent.
- `ruff format` does not wrap a long string, so `E501` stays. The `timeout` command does not exist on macOS, and BSD `sed -i` needs an argument: use Python for an edit in a script.
