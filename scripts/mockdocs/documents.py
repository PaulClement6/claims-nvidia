"""The documents of the kit scenarios: one function per document.

Every name, company, address, phone number and registration number is invented
(non-negotiable 7). E-mail domains end in `.example`. A function writes the PDF and
returns the content of its `expected.json`. The builders at the top (`_booking`,
`_receipt`, `_repair`, ...) are shared by the functions under them.
"""

from pathlib import Path
from typing import Any

from .legacy import AGENCY, ANNA, BLUE, GREEN
from .pdfdoc import MARGIN, Doc, H, W, money, tamper

Party = tuple[str, list[str], tuple[float, float, float]]

MARCO = ["Marco Bianchi", "Via Nassa 18", "6900 Lugano", "Switzerland"]
LUKAS = ["Lukas Meier", "Hirschengasse 9", "8032 Zurich", "Switzerland"]
UID = "UID CHE-000.000.000"
IBAN = "IBAN CH00 0000 0000 0000 0000 0"

WOLKENPFAD: Party = (*AGENCY, BLUE)  # the agency of claim file 1
# travel agency -> (customer number, agent) printed on its documents
STAFF = {
    "Wolkenpfad Reisen AG": ("K-30418", "S. Lindner"),
    "Sonnenhang Reisen SA": ("K-51270", "M. Rossi"),
}
SONNENHANG: Party = (
    "Sonnenhang Reisen SA",
    [
        "Via Cattedrale 12, 6900 Lugano",
        "Tel. +41 91 555 06 10  |  info@sonnenhang-reisen.example",
        f"{UID}  |  {IBAN}",
    ],
    (0.62, 0.30, 0.12),
)
KOFFERHAUS: Party = (
    "Kofferhaus Limmat AG",
    [
        "Limmatquai 88, 8001 Zurich",
        "Tel. +41 44 555 07 20  |  kasse@kofferhaus-limmat.example",
        UID,
    ],
    (0.35, 0.20, 0.45),
)
BERGFREUND: Party = (
    "Bergfreund Sport AG",
    [
        "Marktgasse 31, 3011 Bern",
        "Tel. +41 31 555 08 40  |  shop@bergfreund.example",
        UID,
    ],
    (0.55, 0.15, 0.15),
)
ROSENGARTEN: Party = (
    "Hardware Rosengarten AG",
    [
        "Murtenstrasse 120, 3008 Bern",
        "Tel. +41 31 555 09 50  |  verkauf@hw-rosengarten.example",
        UID,
    ],
    (0.20, 0.35, 0.20),
)
BAUZENTRUM: Party = (
    "Bauzentrum Wankdorf AG",
    [
        "Papiermuehlestrasse 70, 3014 Bern",
        "Tel. +41 31 555 09 80  |  kasse@bauzentrum-wankdorf.example",
        UID,
    ],
    (0.70, 0.45, 0.05),
)
TUERENWERK: Party = (
    "Tuerenwerk Gantrisch AG",
    [
        "Gewerbestrasse 5, 3600 Thun",
        "Tel. +41 33 555 10 60  |  offerte@tuerenwerk-gantrisch.example",
        f"{UID}  |  {IBAN}",
    ],
    (0.30, 0.25, 0.20),
)
ZANETTI: Party = (
    "Zanetti Impianti SA",
    [
        "Via Industria 8, 6814 Lamone",
        "Tel. +41 91 555 11 70  |  info@zanetti-impianti.example",
        f"{UID}  |  {IBAN}",
    ],
    (0.55, 0.10, 0.10),
)
LIMMATBLICK: Party = (
    "Gesundheitszentrum Limmatblick AG",
    [
        "Badenerstrasse 300, 8004 Zurich",
        "Tel. +41 44 555 12 80  |  rechnung@limmatblick.example",
        f"GLN 0000000000000  |  ZSR Z000000  |  {IBAN}",
    ],
    (0.12, 0.30, 0.55),
)
BRUNNER: Party = (
    "Glas & Fenster Brunner GmbH",
    [
        "Technikumstrasse 14, 8400 Winterthur",
        "Tel. +41 52 555 13 90  |  offerte@brunner-glas.example",
        f"{UID}  |  {IBAN}",
    ],
    (0.15, 0.40, 0.55),
)
ALPENWIND: Party = (
    "Alpenwind Airlines AG",
    [
        "Flughafenstrasse 1, 8058 Zurich-Airport",
        "Baggage service  |  Tel. +41 43 555 14 00  |  baggage@alpenwind.example",
        UID,
    ],
    (0.00, 0.35, 0.60),
)
POLICE: Party = (
    "Regionalpolizei Aare-Nord",
    [
        "Wache Bern-West, Weyermannsstrasse 9, 3008 Bern",
        "Tel. +41 31 555 15 10  |  wache.bern-west@rp-aarenord.example",
        "Report of a crime: copy for the insurer",
    ],
    (0.10, 0.20, 0.45),
)


# ---- shared builders ---------------------------------------------------------


def _booking(
    path: Path,
    agency: Party,
    customer: list[str],
    date: str,
    ref: str,
    destination: str,
    dates: tuple[str, str],
    rows: list[tuple[str, int, float]],
) -> dict[str, Any]:
    no, agent = STAFF[agency[0]]
    d = Doc(path, "Booking confirmation", "booking_confirmation")
    d.letterhead(*agency)
    d.address(customer)
    d.meta([("Date:", date), ("Customer no.:", no), ("Agent:", agent)])
    d.text(MARGIN, H - 240, "Booking confirmation", "Helvetica-Bold", 16)
    for i, (label, value) in enumerate(
        [
            ("Booking reference:", ref),
            ("Traveller:", customer[0]),
            ("Destination:", destination),
            ("Departure:", dates[0]),
            ("Return:", dates[1]),
        ]
    ):
        d.row(H - 262 - 16 * i, label, value)
    head = ["Description", "Qty", "Unit (CHF)", "Amount (CHF)"]
    y, total = d.priced_table(H - 370, head, rows, [MARGIN, 330, 400])
    d.total(y, "Total trip price", f"CHF {money(total)}")
    d.text(
        MARGIN,
        y - 50,
        f"Payment received in full on {date} by card. "
        "Free cancellation is not included.",
        size=9,
    )
    d.text(MARGIN, 110, f"{agency[0]}, {agency[1][0]}", size=8)
    d.text(MARGIN, 98, "This confirmation is valid without signature.", size=8)
    d.field("booking_reference", ref, ref)
    d.field("departure_date", dates[0], dates[0])
    d.field("trip_price", total, f"CHF {money(total)}")
    d.field("traveller_name", customer[0], customer[0])
    return d.save()


def _cancellation(
    path: Path,
    agency: Party,
    customer: list[str],
    salutation: str,
    date: str,
    ref: str,
    reason: str,
    price: float,
    fees: float,
) -> dict[str, Any]:
    refund = round(price - fees, 2)
    no, agent = STAFF[agency[0]]
    d = Doc(path, "Cancellation confirmation", "cancellation_confirmation")
    d.letterhead(*agency)
    d.address(customer)
    d.meta([("Date:", date), ("Customer no.:", no), ("Agent:", agent)])
    d.text(MARGIN, H - 240, "Cancellation confirmation", "Helvetica-Bold", 16)
    d.row(H - 262, "Booking reference:", ref)
    d.row(H - 278, "Cancellation date:", date)
    d.row(H - 294, "Reason given:", reason)
    d.text(
        MARGIN,
        H - 322,
        f"Dear {salutation}, we confirm the cancellation of the trip above.",
    )
    rows = [
        ("Trip price paid", money(price)),
        ("Cancellation fees (terms, art. 4)", f"-{money(fees)}"),
        ("Refund to your card", money(refund)),
    ]
    y = d.table(H - 360, ["Item", "Amount (CHF)"], [list(r) for r in rows], [MARGIN])
    d.line_items = [
        {"description": a, "amount": float(m.replace(",", ""))} for a, m in rows
    ]
    d.text(
        MARGIN,
        y - 40,
        f"The refund of CHF {money(refund)} will be credited within 10 working days.",
        size=9,
    )
    d.text(
        MARGIN, y - 54, "Please keep this document for your travel insurance.", size=9
    )
    d.text(MARGIN, 130, "Kind regards,")
    d.text(MARGIN, 100, agent, "Helvetica-Oblique", 12)
    d.text(MARGIN, 86, f"{agency[0]}, customer service", size=8)
    d.field("booking_reference", ref, ref)
    d.field("cancellation_date", date, date)
    d.field("refund_received", refund, money(refund))
    return d.save()


def _receipt(
    path: Path,
    shop: Party,
    number: str,
    date: str,
    rows: list[tuple[str, int, float]],
) -> dict[str, Any]:
    d = Doc(path, f"Receipt {number}", "receipt")
    d.letterhead(*shop)
    d.meta(
        [("Date:", date), ("Receipt no.:", number), ("Payment:", "Card, contactless")]
    )
    d.text(MARGIN, H - 150, "Receipt", "Helvetica-Bold", 16)
    d.text(MARGIN, H - 170, "Thank you for shopping with us.", size=9)
    head = ["Article", "Qty", "Unit (CHF)", "Amount (CHF)"]
    y, total = d.priced_table(H - 230, head, rows, [MARGIN, 350, 410])
    y = d.total(
        y, "Total paid", f"CHF {money(total)}", f"CHF {total * 0.081 / 1.081:,.2f}"
    )
    d.text(MARGIN, y - 40, "Exchange within 30 days with this receipt.", size=9)
    d.text(MARGIN, 110, f"{shop[0]}, {shop[1][0]}", size=8)
    d.field("merchant", shop[0], shop[0])
    d.field("purchase_date", date, date)
    d.field("item_description", rows[0][0], rows[0][0])
    d.field("amount_paid", total, f"CHF {money(total)}")
    return d.save()


def _repair(
    path: Path,
    kind: str,
    company: Party,
    customer: list[str],
    number: str,
    date: str,
    job: str,
    meta: tuple[str, str],
    rows: list[tuple[str, int, float]],
    shown_total: float | None = None,
    note: str = "",
) -> dict[str, Any]:
    """An invoice or a quote. `shown_total` set: the total printed is not the sum."""
    invoice = kind == "invoice"
    d = Doc(
        path,
        f"{'Invoice' if invoice else 'Quote'} {number}",
        "repair_invoice" if invoice else "repair_quote",
    )
    d.letterhead(*company)
    d.address(customer)
    d.meta(
        [
            ("Date:", date),
            ("Invoice no.:" if invoice else "Quote no.:", number),
            meta,
        ]
    )
    d.text(MARGIN, H - 240, "Invoice" if invoice else "Quote", "Helvetica-Bold", 16)
    d.text(MARGIN, H - 262, job)
    head = ["Description", "Qty", "Unit (CHF)", "Amount (CHF)"]
    y, total = d.priced_table(H - 300, head, rows, [MARGIN, 350, 410])
    printed = total if shown_total is None else shown_total
    vat = f"CHF {total * 0.081 / 1.081:,.2f}"  # a forger changes the total, not the VAT
    d.total(y, "Total (VAT included)", f"CHF {money(printed)}", vat)
    d.text(MARGIN, y - 60, note, size=9)
    d.text(MARGIN, 110, f"{company[0]}, {company[1][0]}", size=8)
    p = "invoice" if invoice else "quote"
    d.field(f"{p}_number", number, number)
    d.field(f"{p}_date", date, date)
    d.field("repair_company", company[0], company[0])
    d.field("total", printed, f"CHF {money(printed)}")
    d.field("customer_name", customer[0], customer[0])
    return d.save()


# ---- kit-anna-luggage --------------------------------------------------------


def booking_confirmation_porto(path: Path) -> dict[str, Any]:
    """Another trip than the one of claim file 1: the luggage was lost on this one."""
    rows = [
        ("Return flights Zurich - Porto", 2, 235.00),
        ("Hotel Ribeira Velha, double room, 7 nights", 1, 760.00),
        ("Airport transfers", 1, 90.00),
    ]
    return _booking(
        path,
        WOLKENPFAD,
        ANNA,
        "2026-03-10",
        "WPR-519207",
        "Porto, Portugal (7 nights)",
        ("2026-05-14", "2026-05-21"),
        rows,
    )


def receipt_trolley(path: Path) -> dict[str, Any]:
    rows = [("Cabin trolley, hard shell, 55 cm", 1, 389.00)]
    return _receipt(path, KOFFERHAUS, "KH-2026-004217", "2026-04-02", rows)


def receipt_jacket_shoes(path: Path) -> dict[str, Any]:
    rows = [("Rain jacket, waterproof", 1, 219.00), ("Walking shoes", 1, 179.00)]
    return _receipt(path, BERGFREUND, "BF-2026-018830", "2026-03-18", rows)


def luggage_report(path: Path) -> dict[str, Any]:
    d = Doc(path, "Baggage irregularity report", "luggage_report")
    d.letterhead(*ALPENWIND)
    d.address(ANNA)
    d.meta(
        [
            ("Date:", "2026-05-14"),
            ("Station:", "Porto (OPO)"),
            ("Desk:", "Baggage service"),
        ]
    )
    d.text(MARGIN, H - 240, "Baggage irregularity report", "Helvetica-Bold", 16)
    rows = [
        ("Report reference:", "AW-OPO-20471"),
        ("Passenger:", "Anna Keller"),
        ("Flight:", "AW 618 Zurich - Porto, 2026-05-14"),
        ("Bag tag:", "AW 481516 (1 checked bag, black hard-shell trolley)"),
        ("Reported:", "2026-05-14, at the arrival hall, Porto"),
        ("Status:", "Bag not delivered. Tracing file open."),
    ]
    for i, (label, value) in enumerate(rows):
        d.row(H - 270 - 16 * i, label, value)
    d.lines(
        H - 390,
        [
            "The passenger reported on arrival that the checked bag did not appear",
            "on the belt. The bag could not be located in the system of the airline.",
            "If the bag is not found within 21 days it is declared lost. Please keep",
            "this report for your insurer and add your receipts.",
        ],
    )
    d.text(MARGIN, 130, "Porto, 2026-05-14", size=10)
    d.text(MARGIN, 100, "J. Carvalho", "Helvetica-Oblique", 13)
    d.text(MARGIN, 86, "Baggage service, Alpenwind Airlines AG, Porto", size=8)
    d.stamp(
        W - 200,
        120,
        ["Alpenwind Airlines", "Baggage service", "Porto OPO"],
        ALPENWIND[2],
    )
    d.field("report_reference", "AW-OPO-20471", "AW-OPO-20471")
    d.field("traveller_name", "Anna Keller", "Anna Keller")
    d.field("incident_date", "2026-05-14", "2026-05-14, at the arrival hall")
    d.field("airline", "Alpenwind Airlines AG", "Alpenwind Airlines AG")
    return d.save()


# ---- kit-anna-theft ----------------------------------------------------------


def police_report(path: Path) -> dict[str, Any]:
    d = Doc(path, "Report of a crime RPA-2026-04417", "police_report")
    d.letterhead(*POLICE)
    d.address(ANNA)
    d.meta(
        [
            ("Date:", "2026-10-09"),
            ("Report no.:", "RPA-2026-04417"),
            ("Officer:", "T. Ammann"),
        ]
    )
    d.text(MARGIN, H - 240, "Report of a crime", "Helvetica-Bold", 16)
    rows = [
        ("Reporting person:", "Anna Keller (born 1988-04-12)"),
        ("Address:", "Rosenweg 7, 3011 Bern"),
        ("Offence:", "Burglary (forced entry)"),
        ("Place:", "Rosenweg 7, 3011 Bern, ground-floor flat, front door"),
        ("Discovered:", "2026-10-09, 07:30, when leaving for work"),
        ("Damage:", "Front door, lock and frame destroyed"),
    ]
    for i, (label, value) in enumerate(rows):
        d.row(H - 270 - 16 * i, label, value)
    d.lines(
        H - 390,
        [
            "Statement: the reporting person found the front door of her flat forced",
            "open at 07:30. The lock cylinder was torn out and the frame is split.",
            "No item is missing at this time; the intruders may have been disturbed.",
            "Traces were secured by the patrol. The flat cannot be locked: the",
            "reporting person asks for the damage to be recorded for her insurer.",
        ],
    )
    d.text(MARGIN, 150, "Bern, 2026-10-09", size=10)
    d.text(MARGIN, 110, "Wm T. Ammann", "Helvetica-Oblique", 13)
    d.stamp(
        W - 200, 128, ["Regionalpolizei", "Aare-Nord", "Wache Bern-West"], POLICE[2]
    )
    d.field("report_number", "RPA-2026-04417", "RPA-2026-04417")
    d.field("incident_date", "2026-10-09", "2026-10-09, 07:30")
    d.field("customer_name", "Anna Keller", "Anna Keller (born 1988-04-12)")
    d.field("offence", "burglary", "Burglary (forced entry)")
    return d.save()


def repair_quote_door(path: Path) -> dict[str, Any]:
    rows = [
        ("Remove the forced door and frame, disposal", 1, 420.00),
        ("Front door, steel and wood, security class RC2, with frame", 1, 1380.00),
        ("Multipoint lock and cylinder", 1, 390.00),
        ("Fitting, sealing and paintwork (hours)", 6, 75.00),
    ]
    return _repair(
        path,
        "quote",
        TUERENWERK,
        ANNA,
        "TWG-Q-2026-0731",
        "2026-10-12",
        "Job: new front door after a burglary, Rosenweg 7, 3011 Bern",
        ("Valid until:", "2026-11-12"),
        rows,
        note="This quote is valid for 30 days. Work starts 10 days after your order.",
    )


def receipt_lock_set(path: Path) -> dict[str, Any]:
    rows = [("Security lock set, 5-point, with cylinder", 1, 318.00)]
    return _receipt(path, ROSENGARTEN, "HR-2025-077031", "2025-05-12", rows)


def receipt_door_frame(path: Path) -> dict[str, Any]:
    rows = [
        ("Steel door frame reinforcement kit", 1, 214.00),
        ("Door handle set, stainless steel", 1, 86.50),
    ]
    return _receipt(path, BAUZENTRUM, "BZW-2025-310442", "2025-06-03", rows)


# ---- kit-marco-travel --------------------------------------------------------


def booking_confirmation_sicily(path: Path) -> dict[str, Any]:
    rows = [
        ("Return flights Zurich - Palermo", 2, 410.00),
        ("Hotel Corallo Azzurro, double room, 9 nights", 1, 2080.00),
        ("Car hire, compact, 5 days", 5, 60.00),
    ]
    return _booking(
        path,
        SONNENHANG,
        MARCO,
        "2026-04-20",
        "SHR-770245",
        "Palermo, Sicily (9 nights)",
        ("2026-08-23", "2026-09-01"),
        rows,
    )


def cancellation_confirmation_sicily(path: Path) -> dict[str, Any]:
    return _cancellation(
        path,
        SONNENHANG,
        MARCO,
        "Mr Bianchi",
        "2026-07-13",
        "SHR-770245",
        "Family emergency of the traveller",
        3200.00,
        2300.00,
    )


# ---- kit-marco-inflated ------------------------------------------------------


def repair_invoice_inflated(path: Path) -> dict[str, Any]:
    """Edited invoice: the lines add up to 1,740, the printed total says 4,740, and
    the PDF was saved by an editing tool two days after its creation."""
    rows = [
        ("Emergency call-out and leak detection", 1, 290.00),
        ("Replace the burst supply pipe in the bathroom wall", 1, 640.00),
        ("Dry-out of walls and floor (dehumidifier, days)", 4, 85.00),
        ("Re-tile the wall and the floor of the bathroom", 1, 470.00),
    ]
    expected = _repair(
        path,
        "invoice",
        ZANETTI,
        MARCO,
        "ZI-2026-0652",
        "2026-11-09",
        "Job: water damage in the bathroom, Via Nassa 18, 6900 Lugano",
        ("Site visits:", "2026-11-04 to 2026-11-07"),
        rows,
        shown_total=4740.00,
        note="Payable within 30 days, without deduction. Thank you for your trust.",
    )
    tamper(path, "D:20261109091500+01'00'", "D:20261111162000+01'00'")
    return expected


# ---- kit-lukas-clean ---------------------------------------------------------


def medical_invoice_clean(path: Path) -> dict[str, Any]:
    d = Doc(path, "Invoice GLB-2026-5521", "medical_invoice")
    d.letterhead(*LIMMATBLICK)
    d.address(LUKAS)
    d.meta(
        [
            ("Date:", "2026-11-09"),
            ("Invoice no.:", "GLB-2026-5521"),
            ("Case:", "Accident"),
            ("Billing:", "Direct to insurer"),
        ]
    )
    d.text(MARGIN, H - 240, "Invoice for medical treatment", "Helvetica-Bold", 16)
    d.row(H - 262, "Patient:", "Lukas Meier")
    d.row(H - 278, "Treating physician:", "Dr. med. Nora Frei, orthopaedics FMH")
    rows = [
        ("Consultation, first visit, 30 min", 1, 180.00),
        ("X-ray of the wrist, 2 views", 1, 160.00),
        ("Wrist support, incl. fitting", 1, 110.00),
        ("Follow-up consultation, 15 min", 1, 90.00),
        ("Physiotherapy session, 30 min", 2, 150.00),
    ]
    head = ["Service", "Qty", "Unit (CHF)", "Amount (CHF)"]
    y, total = d.priced_table(H - 316, head, rows, [MARGIN, 350, 410])
    d.total(y, "Total", f"CHF {money(total)}")
    d.text(330, y - 22, "Medical services are exempt from VAT.", size=8)
    d.text(
        MARGIN,
        y - 60,
        "Payable within 30 days, without deduction. Thank you for your trust.",
        size=9,
    )
    d.text(MARGIN, 110, f"{LIMMATBLICK[0]}, {LIMMATBLICK[1][0]}", size=8)
    d.field("invoice_number", "GLB-2026-5521", "GLB-2026-5521")
    d.field("invoice_date", "2026-11-09", "2026-11-09")
    d.field("provider", LIMMATBLICK[0], LIMMATBLICK[0])
    d.field("patient_name", "Lukas Meier", "Lukas Meier")
    d.field("total", total, f"CHF {money(total)}")
    return d.save()


def medical_report_clean(path: Path) -> dict[str, Any]:
    d = Doc(path, "Medical report", "medical_report")
    d.letterhead(*LIMMATBLICK)
    d.address(LUKAS[:3])
    d.meta([("Zurich,", "2026-11-06"), ("File no.:", "P-2026-7305")])
    d.text(MARGIN, H - 240, "Medical report", "Helvetica-Bold", 16)
    d.text(
        MARGIN,
        H - 270,
        "Patient: Lukas Meier (born 1991-03-08), examined on 2026-11-06.",
        size=11,
    )
    d.text(
        MARGIN,
        H - 292,
        "History: the patient fell from his bicycle on the way home on the",
        size=11,
    )
    d.text(MARGIN, H - 308, "same day (accident) and landed on his left hand.", size=11)
    d.text(
        MARGIN,
        H - 334,
        "Diagnosis: contusion of the left wrist. No fracture on the X-ray.",
        "Helvetica-Bold",
        11,
    )
    d.text(
        MARGIN,
        H - 360,
        "Treatment: wrist support for two weeks, physiotherapy twice a week,",
        size=11,
    )
    d.text(
        MARGIN,
        H - 376,
        "no load on the left hand. Follow-up visit in one week.",
        size=11,
    )
    d.text(MARGIN, 150, "Zurich, 2026-11-06", size=10)
    d.text(MARGIN, 110, "Dr. med. N. Frei", "Helvetica-Oblique", 13)
    d.stamp(W - 190, 128, ["Limmatblick", "Dr. med. Nora Frei", "8004 Zurich"], GREEN)
    d.field("patient_name", "Lukas Meier", "Lukas Meier")
    d.field("diagnosis", "contusion of the left wrist", "contusion of the left wrist")
    d.field("treatment_date", "2026-11-06", "2026-11-06")
    d.field("provider", LIMMATBLICK[0], LIMMATBLICK[0])
    return d.save()


# ---- kit-lukas-household -----------------------------------------------------


def repair_quote_window(path: Path) -> dict[str, Any]:
    rows = [
        ("Site visit and measuring", 1, 95.00),
        ("Insulated glass unit 120 x 140 cm, low-E", 1, 760.00),
        ("Removal and disposal of the broken pane", 1, 120.00),
        ("Fitting, sealing and finishing (hours)", 3, 95.00),
    ]
    return _repair(
        path,
        "quote",
        BRUNNER,
        LUKAS,
        "GFB-Q-2026-0388",
        "2026-09-16",
        "Job: new window pane, living room, Hirschengasse 9, 8032 Zurich",
        ("Valid until:", "2026-10-16"),
        rows,
        note="This quote is valid for 30 days. Glass delivery takes 8 working days.",
    )
