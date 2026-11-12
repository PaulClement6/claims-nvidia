# Lessons

## Spike: LangGraph agent in OpenShell (issue #13, 2026-11-12)

- Pin `mcp` to a 1.x version. `pip install mcp` now gives 2.3.0, where `FastMCP` is gone. `langchain-mcp-adapters` 0.3.2 needs `mcp<2`.
- `openshell sandbox exec` does not pass the `ENV` of the image. Set non-secret variables with `--env` on `sandbox create`.
- The model writes identifiers with a special hyphen (U+2011) in some runs. Never match a policy number in the answer text. Read it from the tool result or the gateway event. In tests, normalise the hyphens.
- A Python MCP server (FastMCP) refuses a Host header it does not know (HTTP 421). The sandbox calls `host.openshell.internal`: list that name in `allowed_hosts`.
- In a shell script, `set -o pipefail` with `grep -q` gives false failures: `grep -q` closes the pipe early.
- The first `docker pull` of a base image took 4.4 minutes on the slow wifi. Pull the image before you need it, and build the agent image on the machine that runs the gateway.
