"""The rules the agent cannot break, in trusted code (issue #12, kept by issue #47).

`blocker` says why `assess_and_route` may not run yet. The graph already runs the
checks in order, so this is the second line: the same rule holds for any agent that
reaches the tools, in the backend or through the MCP server of the sandbox.
"""

from typing import Any

from app.tools.gateway import Gateway

CHECKS = ("run_checks", "check_tampering", "check_consistency")


def blocker(gateway: Gateway, claim_id: str) -> str | None:
    """Rule 1: why `assess_and_route` may not run yet, or None. The story must be
    saved, every document read, and the three checks run after the last change of
    the claim. It reads the events: what was read and checked, and when, is there."""
    s = gateway.call(claim_id, "get_claim_summary", {"claim_id": claim_id})
    if not (s["description"] and s["incident_date"] and s["claimed_amount"]):
        return (
            "The story is not complete. Ask the customer for what happened, the "
            "date and the amount, and save them with update_claim."
        )
    last, read, changed = _log(gateway, claim_id)
    if unread := [d["id"] for d in s["documents"] if d["id"] not in read]:
        return (
            f"These documents are not read yet: {', '.join(unread)}. Call "
            "classify_document and extract_document for each one, then the checks."
        )
    changed = max([changed, *read.values()])
    if stale := [c for c in CHECKS if last.get(c, 0) < changed]:
        return (
            f"The checks are not up to date. Call {', '.join(stale)}, then "
            "assess_and_route again."
        )
    return None


def _log(gateway: Gateway, claim_id: str) -> tuple[dict[str, int], dict[str, int], int]:
    """From the events: the sequence of the last success of each check, of the
    extraction of each document, and of the last change of the claim data."""
    last: dict[str, int] = {}
    read: dict[str, int] = {}
    changed = 0
    for e in gateway.store.events_since(claim_id, 0):
        if e.actor != "tool" or "error" in e.outputs:
            continue
        inputs: dict[str, Any] = e.inputs
        if e.stage == "extract_document":
            read[inputs["document_id"]] = e.sequence
        elif e.stage == "update_claim" and set(inputs) - {"claim_id", "agent_state"}:
            changed = e.sequence
        elif e.stage in CHECKS:
            last[e.stage] = e.sequence
    return last, read, changed
