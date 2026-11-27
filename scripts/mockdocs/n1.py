"""N1, the safety set of issue #125: nine documents in which some fields are absent.

A reader that is sure of itself writes a value for a field that the page does not
hold (a "null" text, a made-up date, the number of a patient for an invoice number).
N1 has 16 absent fields (an answer key row with `value: null`) and 23 present ones.
An absent field is "plain" (nothing on the page looks like it) or a "lure" (the page
holds a value that looks like it but is not it). Six documents are text PDFs, two
are scans and one is a phone photo.

Run: cd backend && uv run python ../scripts/generate_mock_documents.py [out_dir]
The set goes to `<out_dir>/n1`. It is not part of the kit (`demo.NOT_KIT`): the
demo, the bench and the evaluation harness of the kit do not see it. The documents
are byte-identical on one machine (see `generate_mock_documents.py`). The sources
of the scans are made in a temporary folder and are not kept.
All names, companies and addresses are invented (non-negotiable 7). The set must
share no name and no number with the kit: `backend/tests/test_n1_dataset.py`.
"""

import json
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .hard import photo, scan
from .pdfdoc import MARGIN, Doc, H, money

UID = "UID CHE-000.000.000"  # the placeholders that the kit generator prints
IBAN = "IBAN CH00 0000 0000 0000 0000 0"
GREY = (0.35, 0.38, 0.42)
TEAL = (0.05, 0.45, 0.45)
RUST = (0.62, 0.28, 0.12)

Absent = dict[str, str]  # field -> "plain" or "lure"
Maker = Callable[[Path], tuple[Doc, Absent]]


def absent(d: Doc, cases: Absent, name: str, kind: str) -> None:
    """An absent field: `null` in the key, with page 1 (the harness needs a page)."""
    d.fields.append({"name": name, "value": None, "confidence": 1.0, "page": 1})
    cases[name] = kind


def n1(path: Path) -> tuple[Doc, Absent]:
    """Repair invoice with no invoice number and no date (both plain)."""
    cases: Absent = {}
    d = Doc(path, "Invoice", "repair_invoice")
    d.letterhead(
        "Tobler & Gerig Haustechnik AG",
        [
            "Kornfeldweg 7, 3065 Bolligen",
            "Tel. +41 31 555 47 19  |  buero@tobler-gerig.example",
            f"{UID}  |  {IBAN}",
        ],
        GREY,
    )
    d.address(["Irene Kaelin", "Talackerstrasse 31", "8152 Glattbrugg", "Switzerland"])
    d.text(MARGIN, H - 240, "Invoice", "Helvetica-Bold", 16)
    d.text(MARGIN, H - 262, "Work at Talackerstrasse 31, 8152 Glattbrugg")
    rows = [
        ("Replace the kitchen mixer tap", 1, 284.00),
        ("Descale the boiler, per hour", 2, 96.50),
    ]
    y, total = d.priced_table(
        H - 300, ["Work", "Qty", "Unit (CHF)", "Amount (CHF)"], rows, [MARGIN, 350, 410]
    )
    d.total(y, "Total", f"CHF {money(total)}")
    d.text(MARGIN, y - 50, "Payable within 30 days. Thank you for your order.", size=9)
    d.field(
        "repair_company",
        "Tobler & Gerig Haustechnik AG",
        "Tobler & Gerig Haustechnik AG",
    )
    d.field("customer_name", "Irene Kaelin", "Irene Kaelin")
    d.field("total", total, f"CHF {money(total)}")
    absent(d, cases, "invoice_number", "plain")
    absent(d, cases, "invoice_date", "plain")
    return d, cases


def n2(path: Path) -> tuple[Doc, Absent]:
    """Medical invoice with no invoice number; other numbers lure (patient, insured,
    UID, IBAN)."""
    cases: Absent = {}
    d = Doc(path, "Statement of medical services", "medical_invoice")
    d.letterhead(
        "Hausarztzentrum Seewinkel AG",
        [
            "Seewinkelstrasse 14, 6403 Kuessnacht",
            "Tel. +41 41 555 63 08  |  billing@seewinkel.example",
            # invented: the UID check digit and the IBAN mod-97 are not valid; kept,
            # because the saved model answers of #125 were made from these bytes
            "UID CHE-418.903.276  |  IBAN CH62 0838 7000 4417 2950 3",
        ],
        TEAL,
    )
    d.address(["Ramon Gasser", "Bachtelweg 5", "6340 Baar", "Switzerland"])
    d.meta(
        [
            ("Date:", "2026-08-21"),
            ("Patient no.:", "P-60417"),
            ("Insured no.:", "756.4821.9937.05"),
            ("Billing:", "Patient pays"),
        ]
    )
    d.text(MARGIN, H - 240, "Statement of medical services", "Helvetica-Bold", 16)
    d.row(H - 262, "Patient:", "Ramon Gasser")
    d.row(H - 278, "Doctor:", "Dr. med. Livio Andermatt")
    rows = [
        ("General check-up, 20 min", 1, 74.30),
        ("Blood test, basic panel", 1, 58.90),
    ]
    y, total = d.priced_table(
        H - 316,
        ["Service", "Qty", "Unit (CHF)", "Amount (CHF)"],
        rows,
        [MARGIN, 350, 410],
    )
    d.total(y, "Total", f"CHF {money(total)}")
    d.field("invoice_date", "2026-08-21", "2026-08-21")
    d.field("provider", "Hausarztzentrum Seewinkel AG", "Hausarztzentrum Seewinkel AG")
    d.field("patient_name", "Ramon Gasser", "Ramon Gasser")
    d.field("total", total, f"CHF {money(total)}")
    absent(d, cases, "invoice_number", "lure")
    return d, cases


def n3(path: Path) -> tuple[Doc, Absent]:
    """Repair quote with no total (plain: the lines are not added up) and no quote
    date (lure: a "valid until" date)."""
    cases: Absent = {}
    d = Doc(path, "Quote RB-Q-2731", "repair_quote")
    d.letterhead(
        "Rietmann Bedachungen GmbH",
        [
            "Grubenstrasse 22, 9450 Altstaetten",
            "Tel. +41 71 555 90 34  |  offerte@rietmann-dach.example",
            UID,
        ],
        RUST,
    )
    d.address(["Corinne Baumli", "Hofwiesenweg 3", "9442 Berneck", "Switzerland"])
    d.meta([("Quote no.:", "RB-Q-2731"), ("Valid until:", "2026-12-15")])
    d.text(MARGIN, H - 240, "Quote for a roof repair", "Helvetica-Bold", 16)
    rows = [
        ("Replace roof tiles", 12, 18.40),
        ("Scaffold, one day", 1, 345.00),
        ("Repair the gutter, per metre", 3, 41.70),
    ]
    y, _ = d.priced_table(
        H - 280, ["Work", "Qty", "Unit (CHF)", "Amount (CHF)"], rows, [MARGIN, 350, 410]
    )
    d.text(MARGIN, y - 20, "Final price after inspection on site.", size=10)
    d.field("quote_number", "RB-Q-2731", "RB-Q-2731")
    d.field("repair_company", "Rietmann Bedachungen GmbH", "Rietmann Bedachungen GmbH")
    d.field("customer_name", "Corinne Baumli", "Corinne Baumli")
    absent(d, cases, "total", "plain")
    absent(d, cases, "quote_date", "lure")
    return d, cases


def n4(path: Path) -> tuple[Doc, Absent]:
    """Booking confirmation: the reference is "sent separately" (lure), no price
    (plain), no table."""
    cases: Absent = {}
    d = Doc(path, "Booking confirmation", "booking_confirmation")
    d.letterhead(
        "Nordlicht Expeditions AG",
        [
            "Polarweg 2, 8484 Weisslingen",
            "Tel. +41 52 555 21 76  |  reisen@nordlicht.example",
            UID,
        ],
        TEAL,
    )
    d.address(["Fabienne Rohner", "Ahornstrasse 40", "8604 Volketswil", "Switzerland"])
    d.meta(
        [
            ("Customer no.:", "K-55190"),
            ("Booking reference:", "sent separately by e-mail"),
        ]
    )
    d.text(MARGIN, H - 240, "Booking confirmation", "Helvetica-Bold", 16)
    d.row(H - 262, "Traveller:", "Fabienne Rohner")
    d.row(H - 278, "Trip:", "Tromso, Norway, 5 nights, half board")
    d.row(H - 294, "Departure:", "2027-02-06")
    d.text(MARGIN, H - 330, "Your invoice follows by post. Have a good trip.", size=10)
    d.field("traveller_name", "Fabienne Rohner", "Fabienne Rohner")
    d.field("departure_date", "2027-02-06", "2027-02-06")
    absent(d, cases, "booking_reference", "lure")
    absent(d, cases, "trip_price", "plain")
    return d, cases


def n5(path: Path) -> tuple[Doc, Absent]:
    """Cancellation confirmation: the refund is "under review" (lure: the price and a
    charge are in the table)."""
    cases: Absent = {}
    d = Doc(path, "Cancellation confirmation", "cancellation_confirmation")
    d.letterhead(
        "Sonnenweg Ferien GmbH",
        [
            "Lauenenweg 9, 3800 Interlaken",
            "Tel. +41 33 555 84 12  |  service@sonnenweg.example",
            UID,
        ],
        RUST,
    )
    d.address(["Dario Imhof", "Seeburgstrasse 11", "6006 Luzern", "Switzerland"])
    d.meta([("Booking reference:", "SWT-60382"), ("Cancelled on:", "2026-09-30")])
    d.text(MARGIN, H - 240, "Cancellation confirmation", "Helvetica-Bold", 16)
    d.row(H - 262, "Trip:", "Ljubljana, 7 nights, 2 persons")
    lines = [("Price of the trip", 2145.00), ("Cancellation charge", -320.00)]
    y = d.table(
        H - 300, ["Item", "Amount (CHF)"], [[a, money(m)] for a, m in lines], [MARGIN]
    )
    d.line_items = [{"description": a, "amount": m} for a, m in lines]
    d.text(
        MARGIN, y - 20, "Refund: under review. We will confirm the amount in writing."
    )
    d.field("booking_reference", "SWT-60382", "SWT-60382")
    d.field("cancellation_date", "2026-09-30", "2026-09-30")
    absent(d, cases, "refund_received", "lure")
    return d, cases


def n6(path: Path) -> tuple[Doc, Absent]:
    """Medical report with only the name of the doctor (provider: lure) and no date
    (treatment date: plain)."""
    cases: Absent = {}
    d = Doc(path, "Medical report", "medical_report")
    d.letterhead(
        "Dr. med. Corina Tschudi",
        ["General medicine", "Promenade 88, 7270 Davos Platz"],
        GREY,
    )
    d.text(MARGIN, H - 150, "Medical report", "Helvetica-Bold", 16)
    d.row(H - 175, "Patient:", "Nadine Caflisch")
    d.row(H - 191, "Diagnosis:", "acute bronchitis")
    d.lines(
        H - 220,
        [
            "Findings: dry cough for ten days, no fever, lungs clear on listening.",
            "Advice: rest, inhalation twice a day, control if not better in a week.",
        ],
    )
    d.text(MARGIN, H - 290, "Dr. med. Corina Tschudi", "Helvetica-Bold", 10)
    d.field("patient_name", "Nadine Caflisch", "Nadine Caflisch")
    d.field("diagnosis", "acute bronchitis", "acute bronchitis")
    absent(d, cases, "provider", "lure")
    absent(d, cases, "treatment_date", "plain")
    return d, cases


def n7(path: Path) -> tuple[Doc, Absent]:
    """Booking confirmation (made into a scan): no reference, no price, no date."""
    cases: Absent = {}
    d = Doc(path, "Booking confirmation", "booking_confirmation")
    d.letterhead(
        "Alpstein Touren GmbH",
        ["Saentisweg 6, 9050 Appenzell", "Tel. +41 71 555 37 61", UID],
        TEAL,
    )
    d.address(["Mirjam Eggenberger", "Schachenweg 21", "9100 Herisau", "Switzerland"])
    d.text(MARGIN, H - 240, "Booking confirmation", "Helvetica-Bold", 16)
    d.row(H - 262, "Traveller:", "Mirjam Eggenberger")
    d.row(H - 278, "Trip:", "Hiking week in the Alpstein, 6 nights")
    d.text(
        MARGIN,
        H - 314,
        "We confirm your place. Details follow before the start.",
        size=10,
    )
    d.field("traveller_name", "Mirjam Eggenberger", "Mirjam Eggenberger")
    for name in ("booking_reference", "trip_price", "departure_date"):
        absent(d, cases, name, "plain")
    return d, cases


def n8(path: Path) -> tuple[Doc, Absent]:
    """Repair invoice (made into a phone photo): no invoice number (lure: job and VAT
    numbers) and no customer (plain)."""
    cases: Absent = {}
    d = Doc(path, "Invoice", "repair_invoice")
    d.letterhead(
        "Kuster Elektro AG",
        [
            "Werkstrasse 19, 8890 Flums",
            "Tel. +41 81 555 72 05",
            "VAT no. CHE-275.614.830 MWST",  # invented, the check digit is not valid
        ],
        RUST,
    )
    d.meta([("Date:", "2026-10-14"), ("Job no.:", "J-88213")])
    d.text(MARGIN, H - 240, "Invoice", "Helvetica-Bold", 16)
    rows = [("Replace the fuse box", 1, 412.00), ("Electrician, per hour", 3, 104.00)]
    y, total = d.priced_table(
        H - 280, ["Work", "Qty", "Unit (CHF)", "Amount (CHF)"], rows, [MARGIN, 350, 410]
    )
    d.total(y, "Total", f"CHF {money(total)}")
    d.text(MARGIN, y - 50, "Payment: cash sale, paid at the counter.", size=10)
    d.field("invoice_date", "2026-10-14", "2026-10-14")
    d.field("repair_company", "Kuster Elektro AG", "Kuster Elektro AG")
    d.field("total", total, f"CHF {money(total)}")
    absent(d, cases, "invoice_number", "lure")
    absent(d, cases, "customer_name", "plain")
    return d, cases


def n9(path: Path) -> tuple[Doc, Absent]:
    """Receipt (made into a scan) with no price at all: no amount paid (plain)."""
    cases: Absent = {}
    d = Doc(path, "Pick-up receipt", "receipt")
    d.letterhead(
        "Gartencenter Rosenhalde",
        ["Rosenhaldenweg 30, 5430 Wettingen", "Tel. +41 56 555 18 44", UID],
        GREY,
    )
    d.meta([("Date:", "2026-07-03"), ("Article no.:", "40-7718-2")])
    d.text(MARGIN, H - 240, "Pick-up receipt", "Helvetica-Bold", 16)
    d.row(H - 262, "Item:", "garden bench, teak, 3 seats")
    d.lines(
        H - 290, ["Collected by the customer.", "Payment: on account, invoice follows."]
    )
    d.field("merchant", "Gartencenter Rosenhalde", "Gartencenter Rosenhalde")
    d.field("purchase_date", "2026-07-03", "2026-07-03")
    d.field(
        "item_description", "garden bench, teak, 3 seats", "garden bench, teak, 3 seats"
    )
    absent(d, cases, "amount_paid", "plain")
    return d, cases


TEXT_DOCS: list[tuple[str, Maker]] = [
    ("n1-repair-invoice.pdf", n1),
    ("n2-medical-invoice.pdf", n2),
    ("n3-repair-quote.pdf", n3),
    ("n4-booking-confirmation.pdf", n4),
    ("n5-cancellation-confirmation.pdf", n5),
    ("n6-medical-report.pdf", n6),
]
# (file, maker, name of the source pdf, variant, seed, angle)
IMAGE_DOCS: list[tuple[str, Maker, str, str, int, float]] = [
    (
        "n7-booking-confirmation-scan.pdf",
        n7,
        "n7-booking-confirmation.pdf",
        "scan",
        2071,
        0.8,
    ),
    ("n8-repair-invoice-photo.jpg", n8, "n8-repair-invoice.pdf", "photo", 2081, 0.0),
    ("n9-receipt-scan.pdf", n9, "n9-receipt.pdf", "scan", 2091, -0.6),
]
PURPOSE = (
    "Safety set of issue #125: documents in which some fields are absent. A reader "
    "must give no value for an absent field (value null in the answer key)."
)


def write_json(path: Path, data: dict[str, Any]) -> None:
    """The same text on every system: LF line ends, ASCII."""
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(data, indent=2) + "\n")


def build(out: Path) -> dict[str, list[str]]:
    """Writes the set into `out`; returns the lines printed on each document (the
    test of the set checks that they are new)."""
    out.mkdir(parents=True, exist_ok=True)
    printed: dict[str, list[str]] = {}
    manifest: list[dict[str, Any]] = []
    present = 0

    def keep(
        name: str, kind: str, doc: Doc, key: dict[str, Any], cases: Absent
    ) -> None:
        nonlocal present
        write_json((out / name).with_suffix(".expected.json"), key)
        present += sum(f["value"] is not None for f in key["fields"])
        printed[name] = doc.printed
        manifest.append(
            {
                "file": name,
                "document_type": key["document_type"],
                "kind": kind,
                "absent": cases,
            }
        )

    for name, make in TEXT_DOCS:
        doc, cases = make(out / name)
        keep(name, "text", doc, doc.save(), cases)

    with tempfile.TemporaryDirectory() as tmp:  # the sources are not kept
        for name, make, source_name, variant, seed, angle in IMAGE_DOCS:
            source = Path(tmp) / source_name
            doc, cases = make(source)
            key = doc.save()
            if variant == "scan":
                scan(source, out / name, seed, angle)
            else:
                photo(source, out / name, seed)
            key["variant"] = variant
            keep(name, variant, doc, key, cases)

    write_json(
        out / "manifest.json",
        {
            "set": "n1",
            "version": 1,
            "issue": 125,
            "purpose": PURPOSE,
            "documents": manifest,
            "absent_fields": sum(len(m["absent"]) for m in manifest),
            "present_fields": present,
        },
    )
    return printed


def generate_n1(out: Path) -> None:
    """Writes the nine documents, their answer keys and `manifest.json` into
    `out` (flat, no sub-folder)."""
    build(out)
