# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp==1.30.0"]
# ///
"""Tiny MCP server of the spike (issue #13). Streamable HTTP on POST /mcp.

Run on the host: `uv run infra/spike/mcp_server.py` (port from MCP_PORT, default 18091).
Two tools. The sandbox policy allows only `get_customer_policies`.
Mock data only (no real person).
"""

import os

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

PORT = int(os.environ.get("MCP_PORT", "18091"))

# ponytail: no bearer, no TLS. The sandbox policy is the gate in this spike. The real
# tool gateway needs authentication (see DECISIONS.md, spike section).
# The sandbox reaches the server as host.openshell.internal, so the Host header
# check of the SDK must allow that name (else HTTP 421).
mcp = FastMCP(
    "claims-spike-gateway",
    host="127.0.0.1",
    port=PORT,
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[f"host.openshell.internal:{PORT}", f"127.0.0.1:{PORT}"],
        allowed_origins=[],
    ),
)

POLICIES = {
    "C-1001": [
        {"policy_number": "POL-7731-AUTO", "product": "Motor", "limit_chf": 25000},
        {"policy_number": "POL-7732-HOME", "product": "Household", "limit_chf": 12000},
    ],
}


@mcp.tool()
def get_customer_policies(customer_id: str) -> list[dict]:
    """Return the insurance policies of a customer (policy number, product, limit in CHF)."""
    return POLICIES.get(customer_id, [])


@mcp.tool()
def get_customer_notes(customer_id: str) -> str:
    """Return the internal handler notes of a customer. The policy must NOT allow this."""
    return f"INTERNAL NOTE for {customer_id}: secret-note-4242"


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
