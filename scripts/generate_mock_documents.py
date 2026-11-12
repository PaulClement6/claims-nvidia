"""Writes the mock claim files to data/mock/<claim>/ (PDF + expected.json each).

Run: cd backend && uv run python ../scripts/generate_mock_documents.py [out_dir]
Same bytes on every run (reportlab invariant mode, fixed dates, no random id).
All names, companies and addresses are invented (non-negotiable 7).

To add a claim file: write one function per document (see the three below) and
add the claim folder with its documents to CLAIM_FILES.
"""

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

W, H = A4
MARGIN = 56


class Doc:
    """One page. Every printed text can become a field: the snippet is the text itself."""

    def __init__(self, path: Path, title: str, document_type: str) -> None:
        self.c = Canvas(str(path), pagesize=A4, invariant=1, pageCompression=0)
        self.c.setTitle(title)
        self.c.setAuthor("")
        self.document_type = document_type
        self.printed: list[str] = []
        self.fields: list[dict[str, Any]] = []
        self.line_items: list[dict[str, Any]] = []
        self.y = H - MARGIN

    def text(
        self, x: float, y: float, s: str, font: str = "Helvetica", size: float = 10
    ) -> None:
        self.c.setFont(font, size)
        self.c.drawString(x, y, s)
        self.printed.append(s)

    def right(self, x: float, y: float, s: str, font: str = "Helvetica") -> None:
        self.c.setFont(font, 10)
        self.c.drawRightString(x, y, s)
        self.printed.append(s)

    def letterhead(self, name: str, lines: list[str], rgb: tuple[float, float, float]) -> None:
        self.c.setFillColorRGB(*rgb)
        self.c.rect(0, H - 18, W, 18, stroke=0, fill=1)
        self.c.setFillColorRGB(0, 0, 0)
        self.text(MARGIN, H - 56, name, "Helvetica-Bold", 18)
        for i, s in enumerate(lines):
            self.text(MARGIN, H - 72 - 11 * i, s, size=8.5)
        self.c.setStrokeColorRGB(*rgb)
        self.c.line(MARGIN, H - 112, W - MARGIN, H - 112)

    def address(self, lines: list[str]) -> None:
        for i, s in enumerate(lines):
            self.text(MARGIN, H - 150 - 13 * i, s)

    def meta(self, rows: list[tuple[str, str]]) -> None:
        for i, (k, v) in enumerate(rows):
            self.text(330, H - 150 - 13 * i, k, "Helvetica-Bold", 9)
            self.text(420, H - 150 - 13 * i, v, size=9)

    def table(
        self, y: float, head: list[str], rows: list[list[str]], xs: list[float]
    ) -> float:
        """Draws a table, columns left-aligned at xs except the last (right-aligned)."""
        self.c.setFillColor(colors.HexColor("#eeeeee"))
        self.c.rect(MARGIN - 4, y - 5, W - 2 * MARGIN + 8, 17, stroke=0, fill=1)
        self.c.setFillColor(colors.black)
        for row, font in [(head, "Helvetica-Bold")] + [(r, "Helvetica") for r in rows]:
            for j, cell in enumerate(row):
                if j == len(row) - 1:
                    self.right(W - MARGIN, y, cell, font)
                else:
                    self.text(xs[j], y, cell, font)
            y -= 18
            self.c.setStrokeColor(colors.lightgrey)
            self.c.line(MARGIN - 4, y + 12, W - MARGIN + 4, y + 12)
        return y

    def field(self, name: str, value: Any, snippet: str) -> None:
        assert any(snippet in s for s in self.printed), f"{snippet!r} is not printed"
        self.fields.append(
            {"name": name, "value": value, "confidence": 1.0, "page": 1, "snippet": snippet}
        )

    def save(self) -> dict[str, Any]:
        self.c.showPage()
        self.c.save()
        return {
            "document_type": self.document_type,
            "fields": self.fields,
            "line_items": self.line_items,
        }


ANNA = ["Anna Keller", "Rosenweg 7", "3011 Bern", "Switzerland"]
AGENCY = ("Wolkenpfad Reisen AG", ["Bahnhofplatz 4, 8001 Zurich", "Tel. +41 44 555 01 20  |  info@wolkenpfad.example", "UID CHE-000.000.000  |  IBAN CH00 0000 0000 0000 0000 0"])
BLUE = (0.10, 0.30, 0.55)


def booking_confirmation(path: Path) -> dict[str, Any]:
    d = Doc(path, "Booking confirmation", "booking_confirmation")
    d.letterhead(AGENCY[0], AGENCY[1], BLUE)
    d.address(ANNA)
    d.meta([("Date:", "2026-06-02"), ("Customer no.:", "K-30418"), ("Agent:", "S. Lindner")])
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
        {"description": a, "quantity": int(q), "unit_price": float(u), "amount": float(m)}
        for a, q, u, m in items
    ]
    d.text(330, y - 6, "Total trip price", "Helvetica-Bold")
    d.right(W - MARGIN, y - 6, "CHF 1,840.00", "Helvetica-Bold")
    d.text(MARGIN, y - 50, "Payment received in full on 2026-06-02 by card. Free cancellation is not included.", size=9)
    d.text(MARGIN, y - 64, "Cancellation fees apply according to our general terms, article 7.", size=9)
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
    d.meta([("Date:", "2026-08-04"), ("Customer no.:", "K-30418"), ("Agent:", "S. Lindner")])
    d.text(MARGIN, H - 240, "Cancellation confirmation", "Helvetica-Bold", 16)
    d.text(MARGIN, H - 262, "Booking reference:", "Helvetica-Bold")
    d.text(170, H - 262, "WPR-482913")
    d.text(MARGIN, H - 278, "Cancellation date:", "Helvetica-Bold")
    d.text(170, H - 278, "2026-08-04")
    d.text(MARGIN, H - 294, "Reason given:", "Helvetica-Bold")
    d.text(170, H - 294, "Illness of the traveller")
    d.text(MARGIN, H - 322, "Dear Ms Keller, we confirm the cancellation of the trip above.", size=10)
    rows = [
        ("Trip price paid", "1,840.00"),
        ("Cancellation fees (terms, art. 7)", "-1,500.00"),
        ("Refund to your card", "340.00"),
    ]
    y = d.table(H - 360, ["Item", "Amount (CHF)"], [list(r) for r in rows], [MARGIN])
    d.line_items = [{"description": a, "amount": float(m.replace(",", ""))} for a, m in rows]
    d.text(MARGIN, y - 40, "The refund of CHF 340.00 will be credited within 10 working days.", size=9)
    d.text(MARGIN, y - 54, "Please keep this document for your travel insurance.", size=9)
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
    d.text(MARGIN, H - 270, "I certify that I examined the patient Anna Keller (born 1988-04-12)", size=11)
    d.text(MARGIN, H - 286, "on 2026-08-03 and found an acute illness (gastroenteritis with fever).", size=11)
    d.text(MARGIN, H - 312, "The patient is unfit to travel from 2026-08-03 until at least 2026-08-17.", "Helvetica-Bold", 11)
    d.text(MARGIN, H - 338, "Rest and treatment at home are required. This certificate is issued", size=11)
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


# One entry per claim file: folder -> (file name, function). Claim files 2 and 3 come later.
CLAIM_FILES: dict[str, list[tuple[str, Callable[[Path], dict[str, Any]]]]] = {
    "claim-1-travel": [
        ("booking-confirmation", booking_confirmation),
        ("cancellation-confirmation", cancellation_confirmation),
        ("medical-certificate", medical_certificate),
    ],
}


def generate(out: Path) -> None:
    for folder, docs in CLAIM_FILES.items():
        (out / folder).mkdir(parents=True, exist_ok=True)
        for name, fn in docs:
            expected = fn(out / folder / f"{name}.pdf")
            (out / folder / f"{name}.expected.json").write_text(
                json.dumps(expected, indent=2, ensure_ascii=False) + "\n"
            )


if __name__ == "__main__":
    generate(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "data" / "mock")
