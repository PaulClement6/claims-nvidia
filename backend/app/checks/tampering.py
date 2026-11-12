"""Signals T001, T002 and T003: is a document edited, and do its numbers hold?

T001 and T002 read the real bytes of the stored PDF with pypdf (its metadata), not
the fields. T003 reads the extracted line items and total. Each code gives at most
one signal per claim; its evidence lists every document it comes from.

A file that is not a PDF, a PDF with no metadata and a PDF pypdf cannot read give no
signal and no crash: the check finds nothing, it does not guess.
"""

import re
from datetime import timedelta
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader

from app.checks.rules import as_number, signal, sources
from app.models import Claim, Document, Signal, Source

# ponytail: a first short list. Real editors are many; add a pattern when a test
# file shows one. A tool that only makes PDFs (Word, ReportLab) is not in it.
EDITING_TOOLS = re.compile(
    r"pdf ?edit|acrobat pro|foxit (phantom|editor)|photoshop|illustrator|gimp"
    r"|inkscape|canva|sejda|smallpdf|ilovepdf|pdfescape|pdffiller|pdfelement"
    r"|pdf-?xchange|nitro pro|pdf24",
    re.I,
)
MAX_EDIT_DELAY = timedelta(days=1)
TOTAL_TOLERANCE = 0.05  # CHF


def _name(doc: Document) -> str:
    return doc.file_metadata.get("original_name") or doc.file


def _page_1(doc: Document, snippet: str) -> Source:
    return Source(document_id=doc.id, page=1, snippet=snippet)


def _pdf_signals(doc: Document, uploads: Path) -> tuple[Signal | None, Signal | None]:
    """(T001, T002) of one stored file."""
    try:
        data = (uploads / doc.file).read_bytes()
        if not data.startswith(b"%PDF-"):
            return None, None
        meta = PdfReader(BytesIO(data)).metadata
        if meta is None:
            return None, None
        made_by = [str(v) for v in (meta.producer, meta.creator) if v]
        created, modified = meta.creation_date, meta.modification_date
    except Exception:  # a broken file: pypdf raises many kinds of errors
        return None, None
    t1 = t2 = None
    tool = next((m for m in made_by if EDITING_TOOLS.search(m)), None)
    if tool:
        t1 = signal(
            "T001",
            f"{_name(doc)} was last written by {tool!r}.",
            [_page_1(doc, f"Producer or creator: {tool}")],
        )
    if created and modified and modified - created > MAX_EDIT_DELAY:
        t2 = signal(
            "T002",
            f"{_name(doc)} was created on {created:%Y-%m-%d} and modified on "
            f"{modified:%Y-%m-%d}.",
            [_page_1(doc, f"Created {created:%Y-%m-%d}, modified {modified:%Y-%m-%d}")],
        )
    return t1, t2


def _merge(found: list[Signal]) -> list[Signal]:
    """One signal per code: the details are joined, the evidence is listed."""
    if not found:
        return []
    return [
        found[0].model_copy(
            update={
                "detail": " ".join(s.detail for s in found),
                "evidence": [e for s in found for e in s.evidence],
            }
        )
    ]


def _t003(claim: Claim) -> list[Signal]:
    found = []
    for doc in claim.documents:
        total = next((f for f in doc.fields if f.name == "total"), None)
        t = as_number(total)
        amounts = [i.get("amount") for i in doc.line_items]
        if t is None or not amounts:
            continue
        if not all(isinstance(a, int | float) for a in amounts):
            continue
        lines = round(sum(a for a in amounts if a is not None), 2)
        if round(abs(lines - t), 2) > TOTAL_TOLERANCE:
            found.append(
                signal(
                    "T003",
                    f"The lines of {_name(doc)} add up to CHF {lines:g}; "
                    f"its total says CHF {t:g}.",
                    sources(total),
                )
            )
    return _merge(found)


def check_tampering(claim: Claim, uploads: Path) -> list[Signal]:
    """The tampering signals of a claim, in the order T001, T002, T003."""
    pairs = [_pdf_signals(doc, uploads) for doc in claim.documents]
    t1 = [p[0] for p in pairs if p[0]]
    t2 = [p[1] for p in pairs if p[1]]
    return [*_merge(t1), *_merge(t2), *_t003(claim)]
