"""Reads a mock document from its expected.json, found by the SHA-256 of the bytes."""

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from app.models import Document, DocumentType, Extraction
from app.store import DATA


class FixtureReader:
    name = "fixture"

    def __init__(self) -> None:
        mock = Path(os.environ.get("CLAIMS_DATA") or DATA) / "mock"
        self.index: dict[str, dict[str, Any]] = {}
        for expected in sorted(mock.glob("**/*.expected.json")):
            stem = expected.name.removesuffix(".expected.json")
            for f in expected.parent.glob(f"{stem}.*"):
                if not f.name.endswith(".json"):
                    digest = hashlib.sha256(f.read_bytes()).hexdigest()
                    self.index[digest] = json.loads(expected.read_text())

    def _find(self, data: bytes) -> dict[str, Any] | None:
        return self.index.get(hashlib.sha256(data).hexdigest())

    def classify(self, document: Document, data: bytes) -> str:
        found = self._find(data)
        return found["document_type"] if found else DocumentType.unknown.value

    def extract(self, document: Document, data: bytes, workflow: str) -> Extraction:
        found = self._find(data)
        if found is None:
            return Extraction(document_type=DocumentType.unknown)
        fields = [
            {
                "name": f["name"],
                "value": f["value"],
                "confidence": f["confidence"],
                "source": {
                    "document_id": document.id,
                    "page": f["page"],
                    "snippet": f["snippet"],
                },
            }
            for f in found["fields"]
        ]
        return Extraction.model_validate(
            {
                "document_type": found["document_type"],
                "fields": fields,
                "line_items": found.get("line_items", []),
            }
        )
