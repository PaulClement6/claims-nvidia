"""One A4 page with the text positions of a letter, and the PDF tampering helper.

Same bytes on every run: reportlab invariant mode, fixed dates, no random id.
"""

from pathlib import Path
from typing import Any

from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

W, H = A4
MARGIN = 56


class Doc:
    """One page. Every printed text can become a field (the snippet is the text)."""

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

    def letterhead(
        self, name: str, lines: list[str], rgb: tuple[float, float, float]
    ) -> None:
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
            {
                "name": name,
                "value": value,
                "confidence": 1.0,
                "page": 1,
                "snippet": snippet,
            }
        )

    def save(self) -> dict[str, Any]:
        self.c.showPage()
        self.c.save()
        return {
            "document_type": self.document_type,
            "fields": self.fields,
            "line_items": self.line_items,
        }

    # Helpers of the kit documents (the first three claim files do not use them).

    def row(self, y: float, label: str, value: str) -> None:
        """A bold label at the left margin and its value at x = 170."""
        self.text(MARGIN, y, label, "Helvetica-Bold")
        self.text(170, y, value)

    def lines(
        self, y: float, texts: list[str], size: float = 10, gap: float = 15
    ) -> float:
        for s in texts:
            self.text(MARGIN, y, s, size=size)
            y -= gap
        return y

    def priced_table(
        self,
        y: float,
        head: list[str],
        rows: list[tuple[str, int, float]],
        xs: list[float],
    ) -> tuple[float, float]:
        """A table of (description, quantity, unit price); the amount is quantity times
        unit. Fills `line_items`. Returns the next y and the sum of the amounts."""
        amounts = [round(q * u, 2) for _, q, u in rows]
        self.table(
            y,
            head,
            [
                [a, str(q), money(u), money(m)]
                for (a, q, u), m in zip(rows, amounts, strict=True)
            ],
            xs,
        )
        self.line_items = [
            {"description": a, "quantity": q, "unit_price": u, "amount": m}
            for (a, q, u), m in zip(rows, amounts, strict=True)
        ]
        return y - 18 * (len(rows) + 1), round(sum(amounts), 2)

    def total(self, y: float, label: str, shown: str, vat: str | None = None) -> float:
        """The total line (bold) and an optional VAT line under it."""
        self.text(330, y - 6, label, "Helvetica-Bold")
        self.right(W - MARGIN, y - 6, shown, "Helvetica-Bold")
        if vat:
            self.text(330, y - 22, "of which VAT 8.1%", size=9)
            self.right(W - MARGIN, y - 22, vat)
        return y - 22

    def stamp(
        self, x: float, y: float, lines: list[str], rgb: tuple[float, float, float]
    ) -> None:
        """An oval stamp with up to three short lines."""
        self.c.setStrokeColorRGB(*rgb)
        self.c.ellipse(x - 10, y - 38, x + 120, y + 22, stroke=1, fill=0)
        for i, s in enumerate(lines):
            font = "Helvetica-Bold" if i == 0 else "Helvetica"
            left = x + 55 - self.c.stringWidth(s, font, 8) / 2  # centred in the oval
            self.text(left, y - 11 * i, s, font, 8)


def money(x: float) -> str:
    return f"{x:,.2f}"


# What a PDF editor leaves in the metadata of the file it saved last.
EDITOR = "PDF Edit Studio 4.2"  # invented name, no real product
CREATED = "D:20261018090000+02'00'"
MODIFIED = "D:20261102143000+01'00'"


def tamper(path: Path, created: str = CREATED, modified: str = MODIFIED) -> None:
    """Rewrites the metadata of a finished PDF as an editing tool would have left it.

    PdfWriter(clone_from=...) keeps the /ID of reportlab (fixed in invariant mode),
    so two runs give the same bytes: pypdf makes a random /ID only when there is none.
    """
    writer = PdfWriter(clone_from=PdfReader(str(path)))
    assert writer._ID, "no /ID to keep: pypdf would write a random one"
    writer.add_metadata(
        {
            "/Producer": EDITOR,
            "/Creator": EDITOR,
            "/CreationDate": created,
            "/ModDate": modified,
        }
    )
    with path.open("wb") as f:
        writer.write(f)
