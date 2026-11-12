"""The tool gateway as an MCP server, for the OpenShell sandbox (issue #14).

Streamable HTTP, stateless, mounted in the FastAPI app at `/mcp/<token>`. Every
sandbox has its own token. The token is the only authorisation of a sandbox toward
its claim: the server maps it to the claim id and uses that id in every call, so the
agent cannot name another claim, and `claim_id` is in no tool schema.

- The token is long and random (made by `app.agent.sandbox`). Only its SHA-256 is
  stored. It is compared in constant time with every open sandbox, so a request
  takes the same time for any token. An unknown or closed token gets 404.
- It is never logged: `RedactToken` cuts it from the access log of uvicorn, and the
  events show it shortened.
- Every call goes through `guarded_call`: the same rule 1 as the graph agent in the
  backend, and `gateway.call` writes one event per call (non-negotiable 5). Those
  events are the trusted events of a sandbox claim.
- The tool list is `gateway.tool_specs()`: the tools the LLM may use, which are the
  tools of the graph (the code nodes `checks` and `route` call them too, through the
  same door). `write_fraud_report` and `get_customer_policies` have no description,
  so they are not listed and not callable.
- Input is not checked against the schema. The schema is the one the model sees,
  and the code nodes send `agent_state` to `update_claim`, which the model must not
  see. Unknown arguments fail in the tool function and write an error event.
"""

import asyncio
import hashlib
import hmac
import json
import logging
from typing import Any

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.responses import PlainTextResponse
from starlette.types import Receive, Scope, Send

from app.tools.gateway import Gateway
from app.tools.guard import guarded_call

PATH = "/mcp"
CLAIM_KEY = "claims_claim_id"  # where the claim id sits in the ASGI scope


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class RedactToken(logging.Filter):
    """The access log of uvicorn names the path of a request: `/mcp/<token>`."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                f"{PATH}/<token>"
                if isinstance(a, str) and a.startswith(f"{PATH}/")
                else a
                for a in record.args
            )
        return True


class McpServer:
    """An ASGI app. `run()` must be entered once (the lifespan of the backend)."""

    def __init__(self, gateway: Gateway) -> None:
        self.gateway = gateway
        self.specs = {
            s["function"]["name"]: s["function"] for s in gateway.tool_specs()
        }
        server: Server[Any, Any] = Server("claims-gateway")

        @server.list_tools()  # type: ignore[no-untyped-call,untyped-decorator]
        async def list_tools() -> list[types.Tool]:
            return [
                types.Tool(
                    name=name,
                    description=spec["description"],
                    inputSchema=spec["parameters"],
                )
                for name, spec in self.specs.items()
            ]

        @server.call_tool(validate_input=False)  # type: ignore[untyped-decorator]
        async def call_tool(
            name: str, arguments: dict[str, Any]
        ) -> types.CallToolResult:
            claim_id = server.request_context.request.scope[CLAIM_KEY]  # type: ignore[union-attr]
            try:
                if name not in self.specs:
                    raise KeyError(f"unknown tool: {name}")
                out = await asyncio.to_thread(
                    guarded_call, self.gateway, claim_id, name, arguments
                )
            except (
                Exception
            ) as e:  # the gateway wrote the event; the agent gets the text
                text = str(e.args[0] if isinstance(e, KeyError) and e.args else e)
                return _result(text[:500], error=True)
            return _result(json.dumps(out))

        self.manager = StreamableHTTPSessionManager(
            server, json_response=True, stateless=True
        )

    def run(self) -> Any:
        return self.manager.run()

    def claim_for_token(self, token: str) -> str | None:
        """The claim of an open sandbox. It compares with every one, in constant time
        for each, and never stops at a match."""
        digest = hash_token(token)
        found = None
        for record in self.gateway.store.sandboxes():
            if hmac.compare_digest(record.token_sha256, digest) and not record.closed:
                found = record.claim_id
        return found

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """`scope["path"]` is `/mcp/<token>` and nothing else."""
        parts = scope["path"].strip("/").split("/")
        claim_id = self.claim_for_token(parts[1]) if len(parts) == 2 else None
        if claim_id is None or parts[0] != PATH.strip("/"):
            await PlainTextResponse("not found", 404)(scope, receive, send)
            return
        scope[CLAIM_KEY] = claim_id
        await self.manager.handle_request(scope, receive, send)


def _result(text: str, error: bool = False) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)], isError=error
    )
