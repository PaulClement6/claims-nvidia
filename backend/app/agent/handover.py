"""A code node that fails every time (the gateway is down): after two failures in a
row the claim goes to a person. Backend only; the graph module stays free of the store.

The system never rejects a claim and does not invent an outcome here: the claim gets
the status `in_review` and no outcome. A person decides.
"""

from typing import Any

from app.agent.phrases import CODE_NODES

HANDOVER = (
    "Sorry, something went wrong on our side. "
    "A claims specialist will take over your case."
)


def hand_over_if_stuck(store: Any, claim_id: str) -> str | None:
    """The handover text when the last two failures of the claim are in the same code
    node (each `fallback` event of a failure carries its `node`); else None."""
    failed = [
        e
        for e in store.events_since(claim_id, 0)
        if e.stage == "fallback" and e.outputs.get("node") in CODE_NODES
    ]
    if len(failed) < 2 or failed[-1].outputs["node"] != failed[-2].outputs["node"]:
        return None
    node = failed[-1].outputs["node"]
    store.update_claim(claim_id, {"status": "in_review", "agent_state": "closed"})
    store.append_event(
        claim_id, "workflow", "failed", f"Failed twice in a row: {node}",
        {"node": node},
        {
            "reason": failed[-1].outputs.get("reason"),
            "status": "in_review",
            "outcome": None,
            "why": (
                "no outcome set: the system decides nothing here and does not "
                "invent an outcome; a claims specialist decides"
            ),
        },
    )  # fmt: skip
    return HANDOVER


def is_handed_over(claim: Any) -> bool:
    return claim is not None and claim.outcome is None and claim.status == "in_review"
