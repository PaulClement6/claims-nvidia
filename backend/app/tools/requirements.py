from typing import Any

from app.tools.gateway import Gateway
from app.workflows import WORKFLOWS


def register(gateway: Gateway) -> None:
    def required_documents(claim_id: str) -> dict[str, Any]:
        """Each entry: any one of `any_of` satisfies it. Table: the workflow."""
        claim = gateway.store.get_claim(claim_id)
        if claim is None:
            raise KeyError(f"unknown claim: {claim_id}")
        workflow = WORKFLOWS.get(claim.type or "")
        if workflow is None:
            return {"documents": [], "note": "no table for this claim type yet"}
        return {"documents": workflow.required_documents(claim.cause)}

    gateway.register("required_documents", required_documents)
