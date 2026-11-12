"""Contract every Reader must pass. The intern calls check_reader_contract(his_reader).

It reads the committed mock files of data/mock/ (every PDF and image with an
expected.json) and some unknown bytes. An image has one page.
"""

from io import BytesIO
from pathlib import Path

from pypdf import PdfReader

from app.docintel.reader import Reader
from app.models import Document, DocumentType

MOCK = Path(__file__).resolve().parents[2] / "data" / "mock"
KNOWN = {t.value for t in DocumentType}


def check_reader_contract(reader: Reader) -> None:
    files = sorted(p for e in ("pdf", "jpg", "png") for p in MOCK.glob(f"**/*.{e}"))
    assert any(p.suffix == ".pdf" for p in files), "no mock PDF found"
    assert any(p.suffix != ".pdf" for p in files), "no mock image found"
    for pdf in files:
        data = pdf.read_bytes()
        pages = len(PdfReader(BytesIO(data)).pages) if pdf.suffix == ".pdf" else 1
        doc = Document(id="D-contract", file=pdf.name, pages=pages)
        assert reader.classify(doc, data) in KNOWN
        out = reader.extract(doc, data, "travel")
        assert out.document_type in KNOWN
        for f in out.fields:
            assert f.source.document_id == doc.id
            assert 1 <= f.source.page <= pages, f"{pdf.name}: {f.name} page"
            assert f.source.snippet or f.source.box

    for junk in (b"", b"not a document", b"%PDF-1.4 broken", bytes(range(256))):
        doc = Document(id="D-junk", file="junk")
        assert reader.classify(doc, junk) in KNOWN  # does not raise
        out = reader.extract(doc, junk, "travel")
        assert out.document_type in KNOWN
