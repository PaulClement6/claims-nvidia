"""The two pilot documents of issue #126: one text PDF of two pages and one scan of
three pages.

The pilot sends one request per document, with every page image in it, to learn if
the hosted endpoint accepts such a request at all. The documents are not part of MP1,
N1 or the kit: they hold other invented content, so that no answer about a document
of the main run is seen before the freeze. They are NOT written into `data/mock/`:
the runner calls `generate_pilot(<its own folder>)`, so the demo, the reader contract
and the evaluation harness never see them.

Document A is a medical invoice (text PDF): a number that a running header repeats,
a table over a page break (every row whole on one page), a total on the last page,
and an absent invoice date. Document B is a repair quote (a scan: one grey image per
page, no text layer): a number that a running header repeats, the table on page 2,
the total after it and the date of the quote on the last page only.

The key has the format of MP1 (`accepted_pages`, `page_snippets`, `line_pages`,
`pages`, `variant`). Same bytes on every run on one machine, as MP1.
All names and numbers are invented (non-negotiable 7).
"""

import hashlib
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .hard import scan_pages
from .n1 import RUST, TEAL, Absent, absent, write_json
from .paged import PagedDoc
from .pdfdoc import MARGIN, H

Maker = Callable[[Path], tuple[PagedDoc, Absent]]
XS: list[float] = [MARGIN, 350, 410]  # the columns of a priced table
MEDICAL = ["Service", "Qty", "Unit (CHF)", "Amount (CHF)"]
REPAIR = ["Work", "Qty", "Unit (CHF)", "Amount (CHF)"]
PURPOSE = (
    "Pilot of issue #126: 2 documents (a text PDF of 2 pages, a scan of 3 pages) "
    "outside MP1, to see if the endpoint takes one request with every page image."
)


def title(d: PagedDoc, s: str, y: float = H - 240) -> None:
    d.text(MARGIN, y, s, "Helvetica-Bold", 16)


def pilot_01(path: Path) -> tuple[PagedDoc, Absent]:
    """Medical invoice of two pages: the table runs over the page break, the number is
    repeated in a running header, the total is on the last page. No invoice date: the
    treatment period is the only date."""
    cases: Absent = {}
    number = "SRC-61942"
    d = PagedDoc(path, "Invoice", "medical_invoice", 2)
    d.letterhead(
        "Sonnrain Rehabilitation AG",
        ["Talmattweg 31, 9435 Heerbrugg", "Tel. +41 71 555 67 24"],
        TEAL,
    )
    d.address(["Elmar Sigrist", "Haldenstrasse 17", "7320 Sargans", "Switzerland"])
    d.meta([("Invoice no.:", number), ("Treatment:", "2026-09-29 to 2026-10-19")])
    title(d, "Invoice")
    d.row(H - 262, "Patient:", "Elmar Sigrist")
    y = d.priced_rows(
        H - 292,
        [
            ("Gait analysis with report", 1, 145.00),
            ("Shockwave treatment, per session", 3, 78.50),
            ("Kinesiology taping, per application", 4, 19.75),
        ],
        XS,
        MEDICAL,
    )
    d.text(MARGIN, y - 10, "The table goes on overleaf", size=9)
    d.new_page()
    d.header(f"Invoice no.: {number}")
    y = d.priced_rows(
        H - 80,
        [
            ("Resistance training, supervised, 40 min", 6, 34.25),
            ("Insole, custom made, pair", 1, 218.00),
        ],
        XS,
        MEDICAL,
    )
    y = d.total(y, "Total", "CHF 883.00")
    d.text(MARGIN, y - 36, "Invoices are due 30 days after receipt.", size=9)
    d.field("invoice_number", number, number, {2: number}, 1)
    absent(d, cases, "invoice_date", "lure")
    d.field(
        "provider",
        "Sonnrain Rehabilitation AG",
        "Sonnrain Rehabilitation AG",
        page=1,
    )
    d.field("patient_name", "Elmar Sigrist", "Elmar Sigrist", page=1)
    d.field("total", 883.0, "CHF 883.00", page=2)
    return d, cases


def pilot_02(path: Path) -> tuple[PagedDoc, Absent]:
    """Repair quote of three pages (made into a scan): the number is repeated in a
    running header, the table is on page 2 with the total under it, the date of the
    quote is on the last page only. Page 1 prints the date of a site visit."""
    cases: Absent = {}
    number = "ZD/26/0318"
    d = PagedDoc(path, "Quote", "repair_quote", 3)
    d.letterhead(
        "Zelgli Dachbau GmbH",
        ["Rebmattstrasse 5, 5610 Wohlen", "Tel. +41 56 555 83 49"],
        RUST,
    )
    d.address(["Hedwig Marti", "Lindenrain 14", "5620 Bremgarten", "Switzerland"])
    d.meta([("Quote no.:", number), ("Site visit:", "2026-11-08")])
    title(d, "Quote")
    d.text(
        MARGIN,
        H - 270,
        "Flashing and a gutter for the garage roof, both replaced.",
        size=10,
    )
    d.text(MARGIN, H - 288, "The work and the total are on page 2.", size=10)
    d.new_page()
    d.header(f"Quote no.: {number}")
    y = d.priced_rows(
        H - 80,
        [
            ("Take off old edge flashing, per metre", 18, 12.40),
            ("Zinc edge flashing, supply and fit, per metre", 18, 47.60),
            ("Downpipe, zinc, per metre", 14, 36.90),
            ("Roofer, per hour", 9, 94.00),
        ],
        XS,
        REPAIR,
    )
    d.total(y, "Total", "CHF 2,442.60")
    d.new_page()
    d.header(f"Quote no.: {number}")
    d.text(MARGIN, H - 90, "Notes", "Helvetica-Bold", 14)
    d.lines(
        H - 120,
        [
            "1. The work starts six weeks after the order.",
            "2. Ladders and fall protection are included in the prices.",
            "3. Frost or heavy wind can move the date.",
        ],
        gap=20,
    )
    d.row(H - 200, "Quote date:", "2026-11-19")
    d.field("quote_number", number, number, {2: number, 3: number}, 1)
    d.field("quote_date", "2026-11-19", "2026-11-19", page=3)
    d.field("repair_company", "Zelgli Dachbau GmbH", "Zelgli Dachbau GmbH", page=1)
    d.field("total", 2442.6, "CHF 2,442.60", page=2)
    d.field("customer_name", "Hedwig Marti", "Hedwig Marti", page=1)
    return d, cases


@dataclass(frozen=True)
class Entry:
    stem: str
    make: Maker
    scenarios: list[str]
    scan: tuple[list[int], list[float]] | None = None  # seeds and angles per page


ENTRIES = [
    Entry(
        "pilot-01-medical-invoice-two-pages",
        pilot_01,
        [
            "table_over_page_break",
            "repeated_header",
            "value_on_two_pages",
            "total_on_last_page",
            "absent_field",
            "misleading_candidate",
        ],
    ),
    Entry(
        "pilot-02-repair-quote-scan-three-pages",
        pilot_02,
        [
            "scan",
            "repeated_header",
            "value_on_three_pages",
            "value_on_last_page_only",
            "misleading_candidate",
        ],
        ([4021, 4022, 4023], [0.6, -0.5, 0.3]),
    ),
]


def source_hash(page: list[str]) -> str:
    """The SHA-256 of the strings printed on a page, joined with a line feed."""
    return hashlib.sha256("\n".join(page).encode("utf-8")).hexdigest()


def build(out: Path) -> dict[str, list[list[str]]]:
    """Writes the two PDFs, their answer keys and `manifest.json` into `out`; returns,
    for each PDF file, the strings printed on each page in drawing order (for the
    scan, the strings of its text source, which is not kept)."""
    out.mkdir(parents=True, exist_ok=True)
    printed: dict[str, list[list[str]]] = {}
    manifest: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as tmp:  # the source of the scan is not kept
        for e in ENTRIES:
            name = f"{e.stem}.pdf"
            source = out / name if e.scan is None else Path(tmp) / name
            doc, cases = e.make(source)
            key = doc.save()
            if e.scan is not None:
                scan_pages(source, out / name, *e.scan)
                key["variant"] = "scan"
            write_json(out / f"{e.stem}.expected.json", key)
            printed[name] = doc.page_texts
            fields = key["fields"]
            entry: dict[str, Any] = {
                "file": name,
                "document_type": key["document_type"],
                "kind": "text" if e.scan is None else "scan",
                "pages": key["pages"],
                "fields_present": sum(f["value"] is not None for f in fields),
                "fields_absent": sum(f["value"] is None for f in fields),
                "line_items": len(key["line_items"]),
                "repeated": {
                    f["name"]: f["accepted_pages"]
                    for f in fields
                    if "accepted_pages" in f
                },
                "absent": cases,
                "scenarios": e.scenarios,
            }
            if e.scan is not None:
                entry["source_text_sha256"] = [source_hash(p) for p in doc.page_texts]
            manifest.append(entry)
    write_json(
        out / "manifest.json",
        {
            "set": "pilot",
            "version": 1,
            "issue": 126,
            "purpose": PURPOSE,
            "language": "en",
            "documents": manifest,
            "counts": counts(manifest),
        },
    )
    return printed


def counts(manifest: list[dict[str, Any]]) -> dict[str, int]:
    """The numbers of the pilot, computed from the documents of the manifest."""

    def total(field: str, kind: str | None = None) -> int:
        return sum(m[field] for m in manifest if kind in (None, m["kind"]))

    return {
        "documents": len(manifest),
        "pages": total("pages"),
        "text_documents": sum(m["kind"] == "text" for m in manifest),
        "scan_documents": sum(m["kind"] == "scan" for m in manifest),
        "text_pages": total("pages", "text"),
        "scan_pages": total("pages", "scan"),
        "fields_scored": total("fields_present") + total("fields_absent"),
        "fields_present": total("fields_present"),
        "fields_absent": total("fields_absent"),
        "fields_repeated": sum(len(m["repeated"]) for m in manifest),
        "line_items": total("line_items"),
        "line_items_text": total("line_items", "text"),
        "line_items_scan": total("line_items", "scan"),
    }


def generate_pilot(out: Path) -> None:
    """Writes the two documents, their answer keys and `manifest.json` into `out`
    (flat, no sub-folder). The runner calls this with its own output folder."""
    build(out)
