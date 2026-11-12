"""The reader contract: the boundary between the base and the intern's pipeline.

A reader is one class with `name`, `classify` and `extract`. `CLAIMS_READER`
picks it (`fixture` is the base). Checks, score, routing and the UI read only
the `Extraction` it returns. The contract test is
`backend/tests/reader_contract.py::check_reader_contract`.
"""

import os
from typing import Protocol

from app.models import Document, Extraction


class Reader(Protocol):
    name: str

    def classify(self, document: Document, data: bytes) -> str:
        """A DocumentType value, or "unknown". Never raises on unknown bytes."""
        ...

    def extract(self, document: Document, data: bytes, workflow: str) -> Extraction:
        """Every field carries a source (document id, page >= 1, snippet or box)."""
        ...


def get_reader() -> Reader:
    name = os.environ.get("CLAIMS_READER", "fixture")
    if name == "fixture":
        from app.docintel.fixture import FixtureReader

        return FixtureReader()
    # the intern adds his reader here, e.g. "omni"
    raise ValueError(f"unknown CLAIMS_READER {name!r} (known: fixture)")
