from typing import Any

from app.tools.gateway import Gateway


def register(gateway: Gateway) -> None:
    def required_documents(claim_id: str) -> dict[str, Any]:
        """Each entry: any one of `any_of` satisfies it. Travel only for now."""
        claim = gateway.store.get_claim(claim_id)
        if claim is None:
            raise KeyError(f"unknown claim: {claim_id}")
        if claim.type != "travel":
            return {"documents": [], "note": "no table for this claim type yet"}
        docs = [
            {"label": "a booking confirmation", "any_of": ["booking_confirmation"]},
            {
                "label": "a cancellation confirmation or a receipt",
                "any_of": ["cancellation_confirmation", "receipt"],
            },
        ]
        if claim.cause == "illness":
            docs.append(
                {"label": "a medical certificate", "any_of": ["medical_certificate"]}
            )
        return {"documents": docs}

    gateway.register("required_documents", required_documents)
