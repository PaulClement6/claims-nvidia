from pathlib import Path
from typing import Any

from app.docintel.reader import Reader
from app.models import Claim, Document
from app.tools.gateway import Gateway


def register(gateway: Gateway, reader: Reader, uploads: Path) -> None:
    """Both tools take a document id only: they load the bytes themselves."""

    def load(document_id: str, claim_id: str | None) -> tuple[Claim, Document, bytes]:
        found = gateway.store.find_document(document_id)
        # a caller that names its claim (the LLM runner) gets its own documents only
        if found is None or (claim_id is not None and found[0].id != claim_id):
            raise KeyError(f"unknown document: {document_id}")
        claim, doc = found
        return claim, doc, (uploads / doc.file).read_bytes()

    def classify_document(
        document_id: str, claim_id: str | None = None
    ) -> dict[str, Any]:
        _, doc, data = load(document_id, claim_id)
        return {"document_type": reader.classify(doc, data)}

    def extract_document(
        document_id: str, claim_id: str | None = None
    ) -> dict[str, Any]:
        claim, doc, data = load(document_id, claim_id)
        result = reader.extract(doc, data, claim.type or "unknown")
        doc.document_type = result.document_type.value
        doc.fields, doc.line_items = result.fields, result.line_items
        gateway.store.save_document(claim.id, doc)
        return result.model_dump(mode="json")

    by_id = {
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "Id of the document."}
        },
        "required": ["document_id"],
        "additionalProperties": False,
    }
    gateway.register(
        "classify_document",
        classify_document,
        model=reader.name,
        description="Find the type of an uploaded document (for example an invoice).",
        parameters=by_id,
    )
    # the agent gets the type only; the event and the claim keep the fields
    gateway.register(
        "extract_document",
        extract_document,
        model=reader.name,
        visible=("document_type",),
        description=(
            "Read an uploaded document and save its data on the claim. "
            "Call it after classify_document. Returns the document type."
        ),
        parameters=by_id,
    )
