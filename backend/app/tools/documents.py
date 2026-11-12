from pathlib import Path
from typing import Any

from app.docintel.reader import Reader
from app.models import Claim, Document
from app.tools.gateway import Gateway


def register(gateway: Gateway, reader: Reader, uploads: Path) -> None:
    """Both tools take a document id only: they load the bytes themselves."""

    def load(document_id: str) -> tuple[Claim, Document, bytes]:
        found = gateway.store.find_document(document_id)
        if found is None:
            raise KeyError(f"unknown document: {document_id}")
        claim, doc = found
        return claim, doc, (uploads / doc.file).read_bytes()

    def classify_document(document_id: str) -> dict[str, Any]:
        _, doc, data = load(document_id)
        return {"document_type": reader.classify(doc, data)}

    def extract_document(document_id: str) -> dict[str, Any]:
        claim, doc, data = load(document_id)
        result = reader.extract(doc, data, claim.type or "unknown")
        return result.model_dump(mode="json")

    gateway.register("classify_document", classify_document, model=reader.name)
    gateway.register("extract_document", extract_document, model=reader.name)
