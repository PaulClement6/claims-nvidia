"""MP1, the multi-page set of issue #126: eight documents of two or three pages.

It compares two modes of the document reader: one model call per page, and one call
for the whole document. The documents hold what makes the two differ: a table over a
page break, a subtotal carried forward, a value that is printed on more than one page
(a running header, a stamp), a terms page, a total on the last page, a lure that looks
like a field, an absent field. Six documents are text PDFs and two are scans (every
page is one grey image, no text layer).

An answer key gives each field the first page that prints it. A value that several
pages print as this field also has `accepted_pages` and, for each of them, the text
of that page in `page_snippets`. `line_pages` gives the page of each line item.

Run: cd backend && uv run python ../scripts/generate_mock_documents.py [out_dir]
The set goes to `<out_dir>/mp1`. It is not part of the kit (`demo.NOT_KIT`). The
documents are byte-identical on one machine (see `generate_mock_documents.py`). The
sources of the scans are made in a temporary folder and are not kept.
All names, companies and addresses are invented (non-negotiable 7). The set shares no
name and no number with the kit or with N1.
"""

import hashlib
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .hard import scan_pages
from .n1 import GREY, RUST, TEAL, Absent, absent, write_json
from .paged import PagedDoc
from .pdfdoc import MARGIN, H, W

Maker = Callable[[Path], tuple[PagedDoc, Absent]]
XS: list[float] = [MARGIN, 350, 410]  # the columns of a priced table
REPAIR = ["Work", "Qty", "Unit (CHF)", "Amount (CHF)"]
MEDICAL = ["Service", "Qty", "Unit (CHF)", "Amount (CHF)"]
PAYABLE = "Payable within 30 days."
PURPOSE = (
    "Multi-page set of issue #126: 8 documents of 2 or 3 pages, to compare one model "
    "call per page with one call per document."
)


def title(d: PagedDoc, s: str, y: float = H - 240) -> None:
    d.text(MARGIN, y, s, "Helvetica-Bold", 16)


def mp1_01(path: Path) -> tuple[PagedDoc, Absent]:
    """Repair invoice of three pages: the table runs over two page breaks, with a
    subtotal carried forward. The number is repeated in a running header."""
    cases: Absent = {}
    d = PagedDoc(path, "Invoice", "repair_invoice", 3)
    d.letterhead(
        "Wydler Badbau AG",
        ["Gewerbering 14, 8580 Amriswil", "Tel. +41 71 555 38 17"],
        TEAL,
    )
    d.address(["Melina Zbinden", "Rebhalde 22", "8500 Frauenfeld", "Switzerland"])
    d.meta(
        [
            ("Invoice no.:", "WB-26-0714"),
            ("Date:", "2026-10-08"),
            ("Work done:", "2026-09-28 to 2026-10-02"),
        ]
    )
    title(d, "Invoice")
    y = d.priced_rows(
        H - 280,
        [
            ("Demolition of old bathroom tiles, per m2", 14, 38.00),
            ("Disposal of rubble, per container", 1, 265.00),
            ("Waterproofing membrane, per m2", 14, 26.50),
            ("Floor drain, stainless steel", 1, 189.00),
            ("Plumber, per hour", 11, 98.00),
        ],
        XS,
        REPAIR,
    )
    d.amount_line(y - 6, "Subtotal page 1, carried forward", "2,435.00", 300)
    d.new_page()
    d.header("Invoice no.: WB-26-0714")
    y = d.column_head(H - 80, REPAIR, XS)
    y = d.amount_line(y, "Brought forward from page 1", "2,435.00")
    y = d.priced_rows(
        y,
        [
            ("Wall tiles 30x60, per m2", 22, 54.80),
            ("Tile adhesive, bag 25 kg", 6, 31.90),
            ("Tiler, per hour", 19, 92.00),
            ("Silicone joints, per metre", 28, 4.50),
            ("Shower screen, glass 8 mm", 1, 655.00),
        ],
        XS,
    )
    d.amount_line(y - 6, "Carried forward to page 3", "6,361.00", 300)
    d.new_page()
    d.header("Invoice no.: WB-26-0714")
    y = d.column_head(H - 80, REPAIR, XS)
    y = d.amount_line(y, "Brought forward from page 2", "6,361.00")
    y = d.priced_rows(
        y,
        [
            ("Mixer tap, wall mounted", 1, 327.00),
            ("Travel and small material, flat", 1, 78.00),
        ],
        XS,
    )
    y = d.total(y, "Total", "CHF 6,766.00", "506.98")
    d.text(MARGIN, y - 36, PAYABLE, size=9)
    evidence = {2: "WB-26-0714", 3: "WB-26-0714"}
    d.field("invoice_number", "WB-26-0714", "WB-26-0714", evidence, 1)
    d.field("invoice_date", "2026-10-08", "2026-10-08", page=1)
    d.field("repair_company", "Wydler Badbau AG", "Wydler Badbau AG", page=1)
    d.field("total", 6766.0, "CHF 6,766.00", page=3)
    d.field("customer_name", "Melina Zbinden", "Melina Zbinden", page=1)
    return d, cases


def mp1_02(path: Path) -> tuple[PagedDoc, Absent]:
    """Medical invoice of two pages: page 2 has table rows and the total, and no
    column head. Page 1 prints a cost estimate (a lure for the total)."""
    cases: Absent = {}
    d = PagedDoc(path, "Invoice", "medical_invoice", 2)
    d.letterhead(
        "Rheinau Health Centre AG",
        ["Inselweg 9, 8462 Rheinau", "Tel. +41 52 555 41 26"],
        GREY,
    )
    d.address(["Valentin Egloff", "Kirchgasse 6", "8212 Neuhausen", "Switzerland"])
    d.meta(
        [
            ("Invoice no.:", "RMC-40718"),
            ("Invoice date:", "2026-09-17"),
            ("Patient no.:", "PN-90352"),
            ("Treatment:", "2026-08-31 to 2026-09-11"),
        ]
    )
    title(d, "Invoice")
    d.row(H - 262, "Patient:", "Valentin Egloff")
    d.text(MARGIN, H - 284, "Cost estimate of 2026-08-28: CHF 650.00", size=9)
    y = d.priced_rows(
        H - 316,
        [
            ("General consultation, 30 min", 1, 118.50),
            ("X-ray, left knee, 2 views", 1, 84.20),
            ("Ultrasound, knee joint", 1, 96.80),
            ("Manual therapy, 20 min", 4, 51.75),
        ],
        XS,
        MEDICAL,
    )
    d.text(MARGIN, y - 10, "Continued on page 2", size=9)
    d.new_page()
    y = d.priced_rows(
        H - 70,
        [
            ("Knee brace, size M", 1, 139.00),
            ("Follow-up consultation with written report", 1, 142.50),
        ],
        XS,
    )
    y = d.total(y, "Total", "CHF 788.00")
    d.text(MARGIN, y - 36, PAYABLE, size=9)
    d.field("invoice_number", "RMC-40718", "RMC-40718", page=1)
    d.field("invoice_date", "2026-09-17", "2026-09-17", page=1)
    d.field("provider", "Rheinau Health Centre AG", "Rheinau Health Centre AG", page=1)
    d.field("patient_name", "Valentin Egloff", "Valentin Egloff", page=1)
    d.field("total", 788.0, "CHF 788.00", page=2)
    return d, cases


def mp1_03(path: Path) -> tuple[PagedDoc, Absent]:
    """Cancellation confirmation of three pages (made into a scan): the calculation is
    on page 2 and has no sum line, the refund is on page 3."""
    cases: Absent = {}
    d = PagedDoc(path, "Cancellation confirmation", "cancellation_confirmation", 3)
    d.letterhead(
        "Meridian Journeys SA",
        ["Avenue des Alpes 21, 1820 Montreux", "Tel. +41 21 555 73 08"],
        RUST,
    )
    d.address(["Beatrice Zurfluh", "Weidstrasse 9", "9630 Wattwil", "Switzerland"])
    title(d, "Cancellation confirmation")
    d.labelled(H - 270, "Booking reference:", "MJ-512086")
    d.labelled(H - 288, "Cancelled on:", "2026-10-30")
    d.labelled(H - 306, "Departure was planned:", "2026-12-12")
    d.labelled(H - 324, "Trip:", "Cyclades, 9 nights, 2 persons")
    d.labelled(H - 342, "Booking amount:", "CHF 2,860.00")
    d.text(
        MARGIN, H - 380, "The calculation is on page 2, the refund on page 3.", size=9
    )
    d.new_page()
    d.header("Booking reference: MJ-512086")
    d.text(MARGIN, H - 90, "Calculation", "Helvetica-Bold", 16)
    d.amount_rows(
        H - 130,
        [
            ("Trip price", 2860.00),
            ("Cancellation charge (30%)", -858.00),
            ("Insurance premium, not refundable", -64.00),
            ("Handling fee", -45.00),
        ],
        [MARGIN],
        ["Description", "Amount (CHF)"],
    )
    d.new_page()
    d.header("Booking reference: MJ-512086")
    d.row(H - 90, "Refund:", "CHF 1,893.00")
    d.text(MARGIN, H - 120, "The amount is credited within 10 working days.", size=10)
    d.text(MARGIN, H - 150, "Montreux, 2026-10-31", size=10)
    evidence = {2: "MJ-512086", 3: "MJ-512086"}
    d.field("booking_reference", "MJ-512086", "MJ-512086", evidence, 1)
    d.field("cancellation_date", "2026-10-30", "2026-10-30", page=1)
    d.field("refund_received", 1893.0, "CHF 1,893.00", page=3)
    return d, cases


def mp1_04(path: Path) -> tuple[PagedDoc, Absent]:
    """Repair quote of three pages: the total is on page 2 with a total with options
    (a lure), page 3 holds terms. No quote date: three other dates are printed."""
    cases: Absent = {}
    d = PagedDoc(path, "Quote", "repair_quote", 3)
    d.letterhead(
        "Stalder Fassaden GmbH",
        ["Schuetzenweg 8, 6210 Sursee", "Tel. +41 41 555 62 91"],
        RUST,
    )
    d.address(
        ["Gottfried Wirz", "Eichmattstrasse 4", "6020 Emmenbruecke", "Switzerland"]
    )
    d.meta([("Quote no.:", "FA/26/0451"), ("Site visit:", "2026-10-06")])
    title(d, "Quote")
    y = d.priced_rows(
        H - 280,
        [
            ("Scaffolding, erect and remove, per m2", 180, 14.20),
            ("High-pressure cleaning of facade, per m2", 165, 6.80),
            ("Crack repair, per metre", 42, 17.50),
            ("Base coat, mineral, per m2", 165, 9.40),
            ("Top coat, silicate, per m2", 165, 15.30),
        ],
        XS,
        REPAIR,
    )
    d.text(MARGIN, y - 10, "Total and options: see page 2", size=9)
    d.new_page()
    d.header("Quote no.: FA/26/0451")
    y = H - 90
    d.text(MARGIN, y, "Total of the quote", "Helvetica-Bold")
    d.right(W - MARGIN, y, "CHF 8,488.50", "Helvetica-Bold")
    d.text(MARGIN, y - 36, "Optional work, not in the total above", size=9)
    y = d.amount_line(y - 58, "Replace window sills, 12 pieces", "1,275.00")
    d.text(MARGIN, y - 4, "Total with options")
    d.right(W - MARGIN, y - 4, "CHF 9,763.50")
    d.text(MARGIN, y - 50, "Valid until 2026-12-18")
    d.text(MARGIN, y - 70, "Accepted by the customer: Gottfried Wirz")
    d.new_page()
    d.header("Quote no.: FA/26/0451")
    d.text(
        MARGIN,
        H - 90,
        "General terms of business (edition 2026-02-15)",
        "Helvetica-Bold",
        12,
    )
    d.lines(
        H - 120,
        [
            "1. A deposit of 30% is due within 10 days of the order.",
            "2. Reminder fee: CHF 30.00. Interest on late payment: 5% per year.",
            "3. Price changes above CHF 500.00 or 5% need a written agreement.",
            "4. Liability is limited to CHF 50,000.00 per event.",
            "5. Complaints: +41 41 555 62 91, reference AGB-2026-03.",
        ],
        gap=20,
    )
    evidence = {2: "FA/26/0451", 3: "FA/26/0451"}
    d.field("quote_number", "FA/26/0451", "FA/26/0451", evidence, 1)
    absent(d, cases, "quote_date", "lure")
    d.field("repair_company", "Stalder Fassaden GmbH", "Stalder Fassaden GmbH", page=1)
    d.field("total", 8488.5, "CHF 8,488.50", page=2)
    d.field(
        "customer_name", "Gottfried Wirz", "Gottfried Wirz", {2: "Gottfried Wirz"}, 1
    )
    return d, cases


def mp1_05(path: Path) -> tuple[PagedDoc, Absent]:
    """Repair invoice of two pages made of service rows: a running header on page 2
    holds the company, the number and the date."""
    cases: Absent = {}
    head = (
        "Kessler & Brunold Heizservice AG | Invoice no.: KB-26-1893 | Date: 2026-11-05"
    )
    d = PagedDoc(path, "Invoice", "repair_invoice", 2)
    d.letterhead(
        "Kessler & Brunold Heizservice AG",
        ["Werkhofstrasse 6, 8610 Uster", "Tel. +41 44 555 29 64"],
        TEAL,
    )
    d.address(["Bruno Steinemann", "Hofackerweg 3", "8620 Wetzikon", "Switzerland"])
    d.meta([("Invoice no.:", "KB-26-1893"), ("Date:", "2026-11-05")])
    title(d, "Invoice")
    flats = ["1.01", "1.02", "1.03", "1.04", "1.05", "1.06", "2.01"]
    y = d.priced_rows(
        H - 280, [(f"Boiler service, flat {f}", 1, 148.00) for f in flats], XS, REPAIR
    )
    d.text(MARGIN, y - 10, "Continued on page 2", size=9)
    d.new_page()
    d.header(head)
    y = d.priced_rows(
        H - 80,
        [
            ("Boiler service, flat 2.02", 1, 148.00),
            ("Boiler service, flat 2.03, with new circulation pump", 1, 312.50),
            ("Boiler service, flat 2.04", 1, 148.00),
            ("Boiler service, flat 2.05", 1, 148.00),
            ("Call-out fee, second visit", 2, 46.50),
        ],
        XS,
        REPAIR,
    )
    d.total(y, "Total", "CHF 1,885.50")
    d.field("invoice_number", "KB-26-1893", "KB-26-1893", {2: "KB-26-1893"}, 1)
    d.field("invoice_date", "2026-11-05", "2026-11-05", {2: "2026-11-05"}, 1)
    company = "Kessler & Brunold Heizservice AG"
    d.field("repair_company", company, company, {2: company}, 1)
    d.field("total", 1885.5, "CHF 1,885.50", page=2)
    d.field("customer_name", "Bruno Steinemann", "Bruno Steinemann", page=1)
    return d, cases


def mp1_06(path: Path) -> tuple[PagedDoc, Absent]:
    """Medical report of two pages (made into a scan): the patient is in the running
    header of page 2, the clinic is on the stamp."""
    cases: Absent = {}
    d = PagedDoc(path, "Medical report", "medical_report", 2)
    d.letterhead(
        "Thurauen Health Centre AG",
        ["Seeblickweg 2, 9620 Lichtensteig", "Tel. +41 71 555 90 13"],
        GREY,
    )
    title(d, "Medical report", H - 150)
    d.row(H - 180, "Patient:", "Mauro Tresch (born 1991-06-02)")
    d.row(H - 198, "Examined on:", "2026-10-27")
    d.row(H - 216, "Referred by:", "Dr. med. Flurin Camenzind on 2026-10-13")
    d.lines(
        H - 256,
        [
            "History: low back pain for three weeks, radiating into the left leg.",
            "Findings: reduced flexion of the lumbar spine, straight leg raise "
            "positive on the left.",
        ],
        gap=20,
    )
    d.text(MARGIN, H - 310, "Continued on page 2", size=9)
    d.new_page()
    d.header("Medical report | Patient: Mauro Tresch")
    d.row(H - 90, "Diagnosis:", "lumbar disc herniation")
    d.text(
        MARGIN,
        H - 120,
        "Therapy: physiotherapy, nine sessions, and pain medication for ten days.",
    )
    d.text(MARGIN, H - 170, "Lichtensteig, 2026-10-29")
    d.text(MARGIN, H - 190, "Dr. med. Sonja Kappeler, specialist in rheumatology")
    d.stamp(MARGIN + 30, H - 250, ["Thurauen Health Centre AG"], TEAL)
    d.field("patient_name", "Mauro Tresch", "Mauro Tresch", {2: "Mauro Tresch"}, 1)
    d.field("diagnosis", "lumbar disc herniation", "lumbar disc herniation", page=2)
    d.field("treatment_date", "2026-10-27", "2026-10-27", page=1)
    clinic = "Thurauen Health Centre AG"
    d.field("provider", clinic, clinic, {2: clinic}, 1)
    return d, cases


def mp1_07(path: Path) -> tuple[PagedDoc, Absent]:
    """Booking confirmation of two pages with no total price: page 2 holds a deposit
    and an instalment (lures for the price)."""
    cases: Absent = {}
    d = PagedDoc(path, "Booking confirmation", "booking_confirmation", 2)
    d.letterhead(
        "Firnblick Travel AG",
        ["Pilatusweg 11, 6370 Stans", "Tel. +41 41 555 17 82"],
        TEAL,
    )
    d.address(["Ottilie Burkhalter", "Schwandenweg 5", "6060 Sarnen", "Switzerland"])
    title(d, "Booking confirmation")
    d.row(H - 270, "Booking reference:", "FBT-317405")
    d.row(H - 288, "Booked on:", "2026-09-03")
    d.row(H - 306, "Customer no.:", "C-48271")
    d.row(H - 324, "Traveller:", "Ottilie Burkhalter")
    d.row(H - 342, "Trip:", "Wellness week in the Engadin, 7 nights, half board")
    d.row(H - 360, "Departure:", "2027-01-16")
    d.new_page()
    d.header("Booking reference: FBT-317405")
    d.text(MARGIN, H - 90, "Payments", "Helvetica-Bold", 16)
    d.lines(
        H - 124,
        [
            "Return: 2027-01-23",
            "Deposit received on 2026-09-05: CHF 430.00",
            "Next instalment, due 2026-12-01: CHF 600.00",
            "Further instalments follow. The invoice with the total price follows "
            "by post.",
        ],
        gap=20,
    )
    d.field("booking_reference", "FBT-317405", "FBT-317405", {2: "FBT-317405"}, 1)
    d.field("traveller_name", "Ottilie Burkhalter", "Ottilie Burkhalter", page=1)
    d.field("departure_date", "2027-01-16", "2027-01-16", page=1)
    absent(d, cases, "trip_price", "lure")
    return d, cases


def mp1_08(path: Path) -> tuple[PagedDoc, Absent]:
    """Police report of two pages: the date of the report is on page 1, the date of the
    theft on page 2. The running header repeats the number and the reporting person."""
    cases: Absent = {}
    d = PagedDoc(path, "Police report", "police_report", 2)
    d.letterhead(
        "Linthgebiet Police",
        ["Station Uznach, Rickenstrasse 12, 8730 Uznach", "Tel. +41 55 555 46 30"],
        GREY,
    )
    title(d, "Police report", H - 150)
    d.row(H - 184, "Report no.:", "LPU-2026-04193")
    d.row(H - 202, "Date of report:", "2026-11-11")
    d.row(H - 220, "Officer:", "Sgt. M. Gubler")
    d.row(H - 238, "Reporting person:", "Dominik Haefeli (born 1979-02-23)")
    d.row(H - 256, "Address:", "Mattenweg 18, 8730 Uznach")
    d.text(MARGIN, H - 292, "Reported by telephone on 2026-11-11, 09:15.")
    d.new_page()
    d.header("Report no.: LPU-2026-04193 | Reporting person: Dominik Haefeli")
    d.text(MARGIN, H - 90, "Statement", "Helvetica-Bold", 16)
    d.lines(
        H - 124,
        [
            "The theft took place on 2026-11-10 between 06:15 and 07:20.",
            "Witness: Sibylle Nyffeler saw a blue van at 06:50.",
        ],
        gap=20,
    )
    d.row(H - 170, "Offence:", "Theft")
    d.lines(
        H - 200,
        ["Stolen item: e-bike, value stated CHF 3,190.00", "Signed: Dominik Haefeli"],
        gap=20,
    )
    d.text(MARGIN, H - 250, "Uznach, 2026-11-11")
    d.field(
        "report_number", "LPU-2026-04193", "LPU-2026-04193", {2: "LPU-2026-04193"}, 1
    )
    d.field("incident_date", "2026-11-10", "2026-11-10", page=2)
    d.field(
        "customer_name", "Dominik Haefeli", "Dominik Haefeli", {2: "Dominik Haefeli"}, 1
    )
    d.field("offence", "Theft", "Theft", page=2)
    return d, cases


@dataclass(frozen=True)
class Entry:
    stem: str
    make: Maker
    favour: str  # "document", "page" or "neutral"
    scenarios: list[str]
    scan: tuple[list[int], list[float]] | None = None  # seeds and angles per page


ENTRIES = [
    Entry(
        "mp1-01-repair-invoice-carried-forward",
        mp1_01,
        "document",
        [
            "number_and_date_on_page_1",
            "total_on_last_page",
            "table_over_page_break",
            "repeated_header",
            "carried_forward_subtotal",
            "value_on_two_pages",
        ],
    ),
    Entry(
        "mp1-02-medical-invoice-continued",
        mp1_02,
        "document",
        [
            "number_and_date_on_page_1",
            "total_on_last_page",
            "table_over_page_break",
            "misleading_candidate",
        ],
    ),
    Entry(
        "mp1-03-cancellation-confirmation-scan",
        mp1_03,
        "neutral",
        [
            "scan",
            "number_and_date_on_page_1",
            "total_on_last_page",
            "repeated_header",
            "misleading_candidate",
            "value_on_two_pages",
        ],
        ([3031, 3032, 3033], [0.8, -0.6, 0.4]),
    ),
    Entry(
        "mp1-04-repair-quote-terms",
        mp1_04,
        "page",
        [
            "terms_page",
            "repeated_header",
            "misleading_candidate",
            "absent_field",
            "value_on_two_pages",
        ],
    ),
    Entry(
        "mp1-05-repair-invoice-service-rows",
        mp1_05,
        "page",
        [
            "total_on_last_page",
            "table_over_page_break",
            "repeated_header",
            "value_on_two_pages",
        ],
    ),
    Entry(
        "mp1-06-medical-report-scan",
        mp1_06,
        "page",
        ["scan", "repeated_header", "misleading_candidate", "value_on_two_pages"],
        ([3061, 3062], [-0.5, 0.7]),
    ),
    Entry(
        "mp1-07-booking-confirmation-no-price",
        mp1_07,
        "neutral",
        [
            "repeated_header",
            "terms_page",
            "misleading_candidate",
            "absent_field",
            "value_on_two_pages",
        ],
    ),
    Entry(
        "mp1-08-police-report-two-dates",
        mp1_08,
        "document",
        ["repeated_header", "misleading_candidate", "value_on_two_pages"],
    ),
]


def source_hash(page: list[str]) -> str:
    """The SHA-256 of the strings printed on a page, joined with a line feed."""
    return hashlib.sha256("\n".join(page).encode("utf-8")).hexdigest()


def build(out: Path) -> dict[str, list[list[str]]]:
    """Writes the set into `out`; returns, for each PDF file, the strings printed on
    each page in drawing order (for a scan, the strings of its text source)."""
    out.mkdir(parents=True, exist_ok=True)
    printed: dict[str, list[list[str]]] = {}
    manifest: list[dict[str, Any]] = []
    keys: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as tmp:  # the sources are not kept
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
            keys.append(key)
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
                "expected_to_favour": e.favour,
                "scenarios": e.scenarios,
            }
            if e.scan is not None:
                entry["source_text_sha256"] = [source_hash(p) for p in doc.page_texts]
            manifest.append(entry)
    write_json(
        out / "manifest.json",
        {
            "set": "mp1",
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
    """The numbers of the set, computed from the documents of the manifest."""

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


def generate_mp1(out: Path) -> None:
    """Writes the eight documents, their answer keys and `manifest.json` into `out`
    (flat, no sub-folder)."""
    build(out)
