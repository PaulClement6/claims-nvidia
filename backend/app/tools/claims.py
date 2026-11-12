from typing import Any

from app.tools.gateway import Gateway


def register(gateway: Gateway) -> None:
    def get_claim_summary(claim_id: str) -> dict[str, Any]:
        claim = gateway.store.get_claim(claim_id)
        if claim is None:
            raise KeyError(f"unknown claim: {claim_id}")
        return {
            "customer_name": claim.customer.name,
            "policy_number": claim.policy.number if claim.policy else None,
            "type": claim.type,
            "description": claim.description,
            "incident_date": claim.incident_date.isoformat()
            if claim.incident_date
            else None,
            "claimed_amount": claim.claimed_amount,
            "cause": claim.cause,
            "agent_state": claim.agent_state,
            "document_types": [d.document_type for d in claim.documents],
        }

    def update_claim(
        claim_id: str,
        description: str | None = None,
        incident_date: str | None = None,
        claimed_amount: float | None = None,
        cause: str | None = None,
        agent_state: str | None = None,
    ) -> dict[str, Any]:
        given = {
            "description": description,
            "incident_date": incident_date,
            "claimed_amount": claimed_amount,
            "cause": cause,
            "agent_state": agent_state,
        }
        changes = {k: v for k, v in given.items() if v is not None}
        if gateway.store.update_claim(claim_id, changes) is None:
            raise KeyError(f"unknown claim: {claim_id}")
        return {"updated": sorted(changes)}

    gateway.register("get_claim_summary", get_claim_summary)
    gateway.register("update_claim", update_claim)
