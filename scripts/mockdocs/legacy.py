"""The documents of the three first claim files, unchanged since they were first made.

Their bytes are checked against the committed files: do not change them.
"""

from pathlib import Path
from typing import Any

from .pdfdoc import MARGIN, Doc, H, W, tamper
from .photos import kitchen_photo

ANNA = ["Anna Keller", "Rosenweg 7", "3011 Bern", "Switzerland"]
AGENCY = (
    "Wolkenpfad Reisen AG",
    [
        "Bahnhofplatz 4, 8001 Zurich",
        "Tel. +41 44 555 01 20  |  info@wolkenpfad.example",
        "UID CHE-000.000.000  |  IBAN CH00 0000 0000 0000 0000 0",
    ],
)
BLUE = (0.10, 0.30, 0.55)


def booking_confirmation(path: Path) -> dict[str, Any]:
    d = Doc(path, "Booking confirmation", "booking_confirmation")
    d.letterhead(AGENCY[0], AGENCY[1], BLUE)
    d.address(ANNA)
    d.meta(
        [
            ("Date:", "2026-06-02"),
            ("Customer no.:", "K-30418"),
            ("Agent:", "S. Lindner"),
        ]
    )
    d.text(MARGIN, H - 240, "Booking confirmation", "Helvetica-Bold", 16)
    d.text(MARGIN, H - 262, "Booking reference:", "Helvetica-Bold")
    d.text(170, H - 262, "WPR-482913")
    d.text(MARGIN, H - 278, "Traveller:", "Helvetica-Bold")
    d.text(170, H - 278, "Anna Keller")
    d.text(MARGIN, H - 294, "Destination:", "Helvetica-Bold")
    d.text(170, H - 294, "Lisbon, Portugal (7 nights)")
    d.text(MARGIN, H - 310, "Departure:", "Helvetica-Bold")
    d.text(170, H - 310, "2026-08-10")
    d.text(MARGIN, H - 326, "Return:", "Helvetica-Bold")
    d.text(170, H - 326, "2026-08-17")
    items = [
        ("Return flights Zurich - Lisbon", "2", "420.00", "840.00"),
        ("Hotel Miradouro, double room, 7 nights", "1", "900.00", "900.00"),
        ("Airport transfers and city card", "1", "100.00", "100.00"),
    ]
    y = d.table(
        H - 370,
        ["Description", "Qty", "Unit (CHF)", "Amount (CHF)"],
        [list(i) for i in items],
        [MARGIN, 330, 400],
    )
    d.line_items = [
        {
            "description": a,
            "quantity": int(q),
            "unit_price": float(u),
            "amount": float(m),
        }
        for a, q, u, m in items
    ]
    d.text(330, y - 6, "Total trip price", "Helvetica-Bold")
    d.right(W - MARGIN, y - 6, "CHF 1,840.00", "Helvetica-Bold")
    d.text(
        MARGIN,
        y - 50,
        "Payment received in full on 2026-06-02 by card. "
        "Free cancellation is not included.",
        size=9,
    )
    d.text(
        MARGIN,
        y - 64,
        "Cancellation fees apply according to our general terms, article 7.",
        size=9,
    )
    d.text(MARGIN, 110, "Wolkenpfad Reisen AG, Bahnhofplatz 4, 8001 Zurich", size=8)
    d.text(MARGIN, 98, "This confirmation is valid without signature.", size=8)
    d.field("booking_reference", "WPR-482913", "WPR-482913")
    d.field("departure_date", "2026-08-10", "2026-08-10")
    d.field("trip_price", 1840.0, "CHF 1,840.00")
    d.field("traveller_name", "Anna Keller", "Anna Keller")
    return d.save()


def cancellation_confirmation(path: Path) -> dict[str, Any]:
    d = Doc(path, "Cancellation confirmation", "cancellation_confirmation")
    d.letterhead(AGENCY[0], AGENCY[1], BLUE)
    d.address(ANNA)
    d.meta(
        [
            ("Date:", "2026-08-04"),
            ("Customer no.:", "K-30418"),
            ("Agent:", "S. Lindner"),
        ]
    )
    d.text(MARGIN, H - 240, "Cancellation confirmation", "Helvetica-Bold", 16)
    d.text(MARGIN, H - 262, "Booking reference:", "Helvetica-Bold")
    d.text(170, H - 262, "WPR-482913")
    d.text(MARGIN, H - 278, "Cancellation date:", "Helvetica-Bold")
    d.text(170, H - 278, "2026-08-04")
    d.text(MARGIN, H - 294, "Reason given:", "Helvetica-Bold")
    d.text(170, H - 294, "Illness of the traveller")
    d.text(
        MARGIN,
        H - 322,
        "Dear Ms Keller, we confirm the cancellation of the trip above.",
        size=10,
    )
    rows = [
        ("Trip price paid", "1,840.00"),
        ("Cancellation fees (terms, art. 7)", "-1,500.00"),
        ("Refund to your card", "340.00"),
    ]
    y = d.table(H - 360, ["Item", "Amount (CHF)"], [list(r) for r in rows], [MARGIN])
    d.line_items = [
        {"description": a, "amount": float(m.replace(",", ""))} for a, m in rows
    ]
    d.text(
        MARGIN,
        y - 40,
        "The refund of CHF 340.00 will be credited within 10 working days.",
        size=9,
    )
    d.text(
        MARGIN, y - 54, "Please keep this document for your travel insurance.", size=9
    )
    d.text(MARGIN, 130, "Kind regards,", size=10)
    d.text(MARGIN, 100, "S. Lindner", "Helvetica-Oblique", 12)
    d.text(MARGIN, 86, "Wolkenpfad Reisen AG, customer service", size=8)
    d.field("booking_reference", "WPR-482913", "WPR-482913")
    d.field("cancellation_date", "2026-08-04", "2026-08-04")
    d.field("refund_received", 340.0, "Refund to your card")
    d.fields[-1]["snippet"] = "340.00"
    return d.save()


def medical_certificate(path: Path) -> dict[str, Any]:
    d = Doc(path, "Medical certificate", "medical_certificate")
    d.letterhead(
        "Praxis am Lindenhof",
        [
            "Dr. med. Beat Zingg, general medicine FMH",
            "Lindenhofweg 15, 3012 Bern  |  Tel. +41 31 555 02 40",
            "GLN 0000000000000  |  ZSR Z000000",
        ],
        (0.15, 0.45, 0.30),
    )
    d.address(["Anna Keller", "Rosenweg 7", "3011 Bern"])
    d.meta([("Bern,", "2026-08-03"), ("File no.:", "P-2026-1187")])
    d.text(MARGIN, H - 240, "Medical certificate", "Helvetica-Bold", 16)
    d.text(
        MARGIN,
        H - 270,
        "I certify that I examined the patient Anna Keller (born 1988-04-12)",
        size=11,
    )
    d.text(
        MARGIN,
        H - 286,
        "on 2026-08-03 and found an acute illness (gastroenteritis with fever).",
        size=11,
    )
    d.text(
        MARGIN,
        H - 312,
        "The patient is unfit to travel from 2026-08-03 until at least 2026-08-17.",
        "Helvetica-Bold",
        11,
    )
    d.text(
        MARGIN,
        H - 338,
        "Rest and treatment at home are required. This certificate is issued",
        size=11,
    )
    d.text(MARGIN, H - 354, "at the request of the patient for her insurer.", size=11)
    d.text(MARGIN, 150, "Bern, 2026-08-03", size=10)
    d.text(MARGIN, 110, "Dr. med. B. Zingg", "Helvetica-Oblique", 13)
    d.c.setStrokeColorRGB(0.15, 0.45, 0.30)
    d.c.ellipse(W - 200, 90, W - 70, 150, stroke=1, fill=0)
    d.text(W - 188, 128, "Praxis am Lindenhof", "Helvetica-Bold", 8)
    d.text(W - 188, 117, "Dr. med. Beat Zingg", size=8)
    d.text(W - 188, 106, "3012 Bern", size=8)
    d.field("patient_name", "Anna Keller", "Anna Keller")
    d.field("cause", "illness", "acute illness")
    d.field("unfit_from", "2026-08-03", "unfit to travel from 2026-08-03")
    return d.save()


def repair_invoice(path: Path) -> dict[str, Any]:
    d = Doc(path, "Invoice BSU-2026-1187", "repair_invoice")
    d.letterhead(
        "Blaubach Sanitaer & Umbau AG",
        [
            "Industriestrasse 22, 6003 Lucerne",
            "Tel. +41 41 555 03 10  |  office@blaubach.example",
            "UID CHE-000.000.000  |  IBAN CH00 0000 0000 0000 0000 0",
        ],
        (0.05, 0.40, 0.45),
    )
    d.address(["Marco Bianchi", "Via Nassa 18", "6900 Lugano", "Switzerland"])
    d.meta(
        [
            ("Date:", "2026-09-22"),
            ("Invoice no.:", "BSU-2026-1187"),
            ("Site visits:", "2026-09-15 to 2026-09-21"),
        ]
    )
    d.text(MARGIN, H - 240, "Invoice", "Helvetica-Bold", 16)
    d.text(
        MARGIN,
        H - 262,
        "Job: water damage in the kitchen, Via Nassa 18, 6900 Lugano",
        size=10,
    )
    items = [
        ("Emergency call-out and leak detection", "1", "380.00", "380.00"),
        ("Replace damaged supply pipe under the sink", "1", "720.00", "720.00"),
        ("Dry-out of floor and cabinets (dehumidifier, days)", "6", "95.00", "570.00"),
        (
            "Replace lower kitchen cabinets and floor covering",
            "1",
            "1,530.00",
            "1,530.00",
        ),
    ]
    y = d.table(
        H - 300,
        ["Description", "Qty", "Unit (CHF)", "Amount (CHF)"],
        [list(i) for i in items],
        [MARGIN, 350, 410],
    )
    d.line_items = [
        {
            "description": a,
            "quantity": int(q),
            "unit_price": float(u.replace(",", "")),
            "amount": float(m.replace(",", "")),
        }
        for a, q, u, m in items
    ]
    assert sum(i["amount"] for i in d.line_items) == 3200.0
    d.text(330, y - 6, "Total (VAT included)", "Helvetica-Bold")
    d.right(W - MARGIN, y - 6, "CHF 3,200.00", "Helvetica-Bold")
    d.text(330, y - 22, "of which VAT 8.1%", size=9)
    d.right(W - MARGIN, y - 22, "CHF 239.78")
    d.text(
        MARGIN,
        y - 60,
        "Payable within 30 days, without deduction. Thank you for your trust.",
        size=9,
    )
    d.text(
        MARGIN,
        110,
        "Blaubach Sanitaer & Umbau AG, Industriestrasse 22, 6003 Lucerne",
        size=8,
    )
    d.field("invoice_number", "BSU-2026-1187", "BSU-2026-1187")
    d.field("invoice_date", "2026-09-22", "2026-09-22")
    d.field(
        "repair_company", "Blaubach Sanitaer & Umbau AG", "Blaubach Sanitaer & Umbau AG"
    )
    d.field("total", 3200.0, "CHF 3,200.00")
    d.field("customer_name", "Marco Bianchi", "Marco Bianchi")
    return d.save()


CLINIC = (
    "Medizinzentrum Seeblick AG",
    [
        "Seestrasse 41, 8800 Thalwil",
        "Tel. +41 44 555 04 30  |  rechnung@seeblick-medizin.example",
        "GLN 0000000000000  |  ZSR Z000000  |  IBAN CH00 0000 0000 0000 0000 0",
    ],
)
GREEN = (0.12, 0.42, 0.40)


def medical_invoice(path: Path) -> dict[str, Any]:
    """Claim file 3: tampered. The lines add up to 1,450, the total says 4,450."""
    d = Doc(path, "Invoice MZS-2026-3317", "medical_invoice")
    d.letterhead(CLINIC[0], CLINIC[1], GREEN)
    d.address(["Jonas Meier", "Hirschengasse 9", "8032 Zurich", "Switzerland"])
    d.meta(
        [
            ("Date:", "2026-10-18"),
            ("Invoice no.:", "MZS-2026-3317"),
            ("Case:", "Accident"),
            ("Billing:", "Direct to insurer"),
        ]
    )
    d.text(MARGIN, H - 240, "Invoice for medical treatment", "Helvetica-Bold", 16)
    d.text(MARGIN, H - 262, "Patient:", "Helvetica-Bold")
    d.text(170, H - 262, "Jonas Meier")
    d.text(MARGIN, H - 278, "Treating physician:", "Helvetica-Bold")
    d.text(170, H - 278, "Dr. med. Reto Haller, sports medicine FMH")
    items = [
        ("Consultation, first visit, 30 min", "1", "180.00", "180.00"),
        ("X-ray of the ankle, 3 views", "1", "220.00", "220.00"),
        ("Ultrasound of the ankle", "1", "150.00", "150.00"),
        ("Functional ankle brace, incl. fitting", "1", "180.00", "180.00"),
        ("Physiotherapy session, 30 min", "6", "120.00", "720.00"),
    ]
    y = d.table(
        H - 316,
        ["Service", "Qty", "Unit (CHF)", "Amount (CHF)"],
        [list(i) for i in items],
        [MARGIN, 350, 410],
    )
    d.line_items = [
        {
            "description": a,
            "quantity": int(q),
            "unit_price": float(u),
            "amount": float(m),
        }
        for a, q, u, m in items
    ]
    assert sum(i["amount"] for i in d.line_items) == 1450.0
    d.text(330, y - 6, "Total", "Helvetica-Bold")
    d.right(W - MARGIN, y - 6, "CHF 4,450.00", "Helvetica-Bold")  # the tampered total
    d.text(330, y - 22, "Medical services are exempt from VAT.", size=8)
    d.text(
        MARGIN,
        y - 60,
        "Payable within 30 days, without deduction. Thank you for your trust.",
        size=9,
    )
    d.text(
        MARGIN, 110, "Medizinzentrum Seeblick AG, Seestrasse 41, 8800 Thalwil", size=8
    )
    d.field("invoice_number", "MZS-2026-3317", "MZS-2026-3317")
    d.field("invoice_date", "2026-10-18", "2026-10-18")
    d.field("provider", "Medizinzentrum Seeblick AG", "Medizinzentrum Seeblick AG")
    d.field("patient_name", "Jonas Meier", "Jonas Meier")
    d.field("total", 4450.0, "CHF 4,450.00")
    expected = d.save()
    tamper(path)
    return expected


def medical_report(path: Path) -> dict[str, Any]:
    """Claim file 3: the clean report. Written by reportlab only, never edited."""
    d = Doc(path, "Medical report", "medical_report")
    d.letterhead(CLINIC[0], CLINIC[1], GREEN)
    d.address(["Lukas Meier", "Hirschengasse 9", "8032 Zurich"])
    d.meta([("Thalwil,", "2026-10-25"), ("File no.:", "P-2026-4410")])
    d.text(MARGIN, H - 240, "Medical report", "Helvetica-Bold", 16)
    d.text(
        MARGIN,
        H - 270,
        "Patient: Lukas Meier (born 1991-03-08), examined on 2026-10-25.",
        size=11,
    )
    d.text(
        MARGIN,
        H - 292,
        "History: the patient twisted his right ankle on the stairs of a station",
        size=11,
    )
    d.text(MARGIN, H - 308, "on the same day (accident).", size=11)
    d.text(
        MARGIN,
        H - 334,
        "Diagnosis: sprained ankle, right, ligament injury grade II. No fracture",
        "Helvetica-Bold",
        11,
    )
    d.text(MARGIN, H - 350, "on the X-ray.", "Helvetica-Bold", 11)
    d.text(
        MARGIN,
        H - 376,
        "Treatment: functional ankle brace, physiotherapy twice a week, no sport",
        size=11,
    )
    d.text(MARGIN, H - 392, "for six weeks. Follow-up visit in two weeks.", size=11)
    d.text(MARGIN, 150, "Thalwil, 2026-10-25", size=10)
    d.text(MARGIN, 110, "Dr. med. R. Haller", "Helvetica-Oblique", 13)
    d.c.setStrokeColorRGB(*GREEN)
    d.c.ellipse(W - 200, 90, W - 70, 150, stroke=1, fill=0)
    d.text(W - 190, 128, "Medizinzentrum Seeblick", "Helvetica-Bold", 7)
    d.text(W - 190, 117, "Dr. med. Reto Haller", size=8)
    d.text(W - 190, 106, "8800 Thalwil", size=8)
    d.field("patient_name", "Lukas Meier", "Lukas Meier")
    d.field("diagnosis", "sprained ankle", "sprained ankle")
    d.field("treatment_date", "2026-10-25", "2026-10-25")
    d.field("provider", "Medizinzentrum Seeblick AG", "Medizinzentrum Seeblick AG")
    return d.save()


def damage_photo_1(path: Path) -> dict[str, Any]:
    return kitchen_photo(path, 1, (150, 455, 560, 570))


def damage_photo_2(path: Path) -> dict[str, Any]:
    return kitchen_photo(path, 2, (320, 470, 700, 585))
