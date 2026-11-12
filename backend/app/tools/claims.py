from typing import Any

from app.tools.gateway import Gateway
from app.workflows import WORKFLOWS


def register(gateway: Gateway) -> None:
    def get_claim_summary(claim_id: str) -> dict[str, Any]:
        claim = gateway.store.get_claim(claim_id)
        if claim is None:
            raise KeyError(f"unknown claim: {claim_id}")
        workflow = WORKFLOWS.get(claim.type or "")
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
            "documents": [
                {
                    "id": d.id,
                    "file_name": d.file_metadata.get("original_name", ""),
                    "type": d.document_type,
                }
                for d in claim.documents
            ],
            "allowed_causes": workflow.all_causes() if workflow else [],
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
        if cause is not None:
            claim = gateway.store.get_claim(claim_id)
            workflow = WORKFLOWS.get(claim.type or "") if claim else None
            if workflow and cause not in workflow.all_causes():
                raise ValueError(
                    f"cause must be one of: {', '.join(workflow.all_causes())}"
                )
        if gateway.store.update_claim(claim_id, changes) is None:
            raise KeyError(f"unknown claim: {claim_id}")
        return {"updated": sorted(changes)}

    gateway.register(
        "get_claim_summary",
        get_claim_summary,
        description=(
            "Read the claim: what is saved so far (story, date, amount, cause), the "
            "documents with their ids and types, and the causes you may use."
        ),
    )
    gateway.register(
        "update_claim",
        update_claim,
        description=(
            "Save what the customer told you. Give only the fields you know. "
            "`cause` must be one of `allowed_causes` of the claim summary."
        ),
        parameters={
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "What happened, in the customer's words, short.",
                },
                "incident_date": {
                    "type": "string",
                    "description": "Date of the incident, YYYY-MM-DD.",
                },
                "claimed_amount": {
                    "type": "number",
                    "description": "Amount claimed, in CHF.",
                },
                "cause": {"type": "string", "description": "One allowed cause."},
            },
            "additionalProperties": False,
        },
    )
