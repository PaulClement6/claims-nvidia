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

## Sandbox panel (issue #15, 2026-11-12)

- `openshell logs` is about 0.3 s late: a read right after the request does not hold its lines yet. Read again until the lines you expect are there, with a limit.
- Do not place the events of a process by counting back from its end. The process takes about 0.2 s to exit and `openshell` to return. Use the clock of the process and keep the result inside the time you waited.
- A function that cuts a secret and adds a mark (`/mcp/ab12cd…`) must also match the mark, or a second run adds a second one. Test it twice.
- The test of non-negotiable 1 reads all string literals of the backend. A new feature that needs `denied` for another meaning needs a named exception in that test.
- One refused connection gives two OpenShell lines: the DNS refusal (`NET:REFUSE`), then the connection (`NET:OPEN`). A non-DENIED line (`WARN`, `ALLOWED`) can talk about the same host: keep only the OCSF lines with `DENIED`.
- A JSON line from a sandbox is data. Judge a probe in the backend (status, error name) and accept a placeholder only if it matches its form: a lying sandbox then cannot show a key.
- The setup script builds the image from the graph files: run it again after a change of `sandbox_turn.py` or the graph, or the sandbox runs the old code.
- A shared pytest fixture (`make` in `tests/rig.py`) is imported once in `conftest.py`. Never name a test argument like an imported fixture in the test file.


## Document kit (issue #23, 2026-11-13)

- C003 (outside travel) compares the claimed amount with the first `total` field of the claim, so a receipt must not call its amount `total`: it uses `amount_paid`. A quote is `quote_number` and `quote_date`, never `invoice_date`, or R005 would read it.
- One message can hold the story, the date and the amount: the scripted agent fills the three slots from it. The kit stories are one line each.
- The expected signals of a story are checked against the real rules, not computed by hand: a threshold story (60 points for `investigation`, 20 for a review) breaks the day somebody tunes the points. The test names the story that moved.
- reportlab keeps a JPEG as it is (DCTDecode) when `drawImage` gets a JPEG file or an `ImageReader` of one: the scan PDF is a plain page with one image, no text layer, and the bytes of the JPEG are the ones Pillow wrote.
- pdfium does not embed the standard fonts of the reportlab PDFs: it draws a substitute that depends on the machine. A pixel test of a scan must be coarse (reduced to 1/8, mean grey level): two different documents differ by about 11 on that scale, a Mac against Linux by far less.
- The titles and stories of `/demo/kit` are read by a visitor in the customer panel (non-negotiable 2): never write "fraud", "tampered" or "edited" there. The seeded signals live in `index.json` only, which no route serves.


## Deploy on the Brev VM (issue #16, 2026-11-13)

- A `umask 077` in the shell that runs `infra/sandbox/setup.sh` gave an image where the agent user could not read `/app/app/agent`: every turn failed in less than a second with the fallback message and no network line in the sandbox log. The script now makes the build context readable (`chmod -R a+rX`). When a turn fails with no denial in the log, run `python -m app.agent.sandbox_turn` by hand with `openshell sandbox exec`.
- The NemoForge recipe works unchanged on Ubuntu 22.04 with the GCP kernel 6.8: write `gateway.toml` before the OpenShell installer starts the gateway.
- `/claims/{id}/events` is a stream: a `curl` on it never ends. Use `/claims/{id}` to read the state in a script.
- `pkill -f <text>` over SSH kills its own shell when the text is in the command line.
- A Brev VM has no public SSH port: `brev port-forward` or `ssh -L` through the Brev host is the only way in. Good for us, the app has no login.

