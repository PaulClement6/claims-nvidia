"""A document of several pages, built on `Doc` (which is one page).

`PagedDoc` keeps the printed strings of each page apart, so that the evidence of a
field is checked on the page where it is printed. A page ends with a foot line
`Page n of N`, drawn last. The page breaks are calls (`new_page`), never overflow.
Same bytes on every run, as `Doc`.
"""

from pathlib import Path
from typing import Any

from reportlab.lib import colors

from .pdfdoc import MARGIN, Doc, H, W, money

ROW = 18  # the height of a table row


class PagedDoc(Doc):
    """Several pages. `printed` is the list of the current page."""

    def __init__(self, path: Path, title: str, document_type: str, pages: int) -> None:
        super().__init__(path, title, document_type)
        self.total_pages = pages
        self.page_no = 1
        self.page_texts: list[list[str]] = []  # the pages that are finished
        self.line_pages: list[int] = []
        self._checks: list[tuple[str, int, str]] = []  # (field, page, evidence)

    # ---- pages

    def _foot(self) -> None:
        s = f"Page {self.page_no} of {self.total_pages}"
        width = self.c.stringWidth(s, "Helvetica", 9)
        self.text((W - width) / 2, 30, s, size=9)

    def new_page(self) -> None:
        """Ends the page (foot line last) and starts the next one."""
        self._foot()
        self.c.showPage()
        self.page_texts.append(self.printed)
        self.printed = []
        self.page_no += 1

    def header(self, s: str) -> None:
        """A running header, the first thing drawn on a page after the first."""
        self.c.setFillColorRGB(0.3, 0.3, 0.3)
        self.text(MARGIN, H - 40, s, size=9)
        self.c.setFillColorRGB(0, 0, 0)
        self.c.setStrokeColorRGB(0.6, 0.6, 0.6)
        self.c.line(MARGIN, H - 46, W - MARGIN, H - 46)

    # ---- tables

    def column_head(self, y: float, head: list[str], xs: list[float]) -> float:
        """The shaded column head. Returns the y of the next row."""
        return self.table(y, head, [], xs)

    def _rows(self, y: float, rows: list[list[str]], xs: list[float]) -> float:
        for row in rows:
            for j, cell in enumerate(row):
                if j == len(row) - 1:
                    self.right(W - MARGIN, y, cell)
                else:
                    self.text(xs[j], y, cell)
            y -= ROW
            self.c.setStrokeColor(colors.lightgrey)
            self.c.line(MARGIN - 4, y + 12, W - MARGIN + 4, y + 12)
        return y

    def priced_rows(
        self,
        y: float,
        rows: list[tuple[str, int, float]],
        xs: list[float],
        head: list[str] | None = None,
    ) -> float:
        """Adds line items (description, quantity, unit price; the amount is quantity
        times unit) on the current page, with the column head or without. Returns the
        y of the next row."""
        if head:
            y = self.column_head(y, head, xs)
        amounts = [round(q * u, 2) for _, q, u in rows]
        y = self._rows(
            y,
            [
                [a, str(q), money(u), money(m)]
                for (a, q, u), m in zip(rows, amounts, strict=True)
            ],
            xs,
        )
        for (a, q, u), m in zip(rows, amounts, strict=True):
            self.line_items.append(
                {"description": a, "quantity": q, "unit_price": u, "amount": m}
            )
            self.line_pages.append(self.page_no)
        return y

    def amount_rows(
        self,
        y: float,
        rows: list[tuple[str, float]],
        xs: list[float],
        head: list[str] | None = None,
    ) -> float:
        """Adds line items of a description and a signed amount, as the cancellation
        table of N1 does (no quantity, no unit price)."""
        if head:
            y = self.column_head(y, head, xs)
        y = self._rows(y, [[a, money(m)] for a, m in rows], xs)
        for a, m in rows:
            self.line_items.append({"description": a, "amount": m})
            self.line_pages.append(self.page_no)
        return y

    def amount_line(
        self, y: float, label: str, shown: str, x: float = MARGIN, bold: bool = False
    ) -> float:
        """A label and an amount on one line (not a line item). Returns the next y."""
        font = "Helvetica-Bold" if bold else "Helvetica"
        self.text(x, y, label, font)
        self.right(W - MARGIN, y, shown, font)
        return y - ROW

    def labelled(self, y: float, label: str, value: str, x: float = 190) -> None:
        """Like `row`, with the value at `x` (for a long label)."""
        self.text(MARGIN, y, label, "Helvetica-Bold")
        self.text(x, y, value)

    # ---- fields

    def field(
        self,
        name: str,
        value: Any,
        snippet: str,
        also: dict[int, str] | None = None,
        page: int | None = None,
    ) -> None:
        """A present field. `page` is the current page by default. With `also` (page
        -> evidence text), the value has more than one valid source page. The evidence
        is checked at `save()`, against the strings printed on its own page."""
        page = page or self.page_no
        row: dict[str, Any] = {
            "name": name,
            "value": value,
            "confidence": 1.0,
            "page": page,
            "snippet": snippet,
        }
        self._checks.append((name, page, snippet))
        if also:
            assert page not in also, f"{name}: the key page is not in `also`"
            evidence = {page: snippet, **also}
            row["accepted_pages"] = sorted(evidence)
            row["page_snippets"] = {str(p): evidence[p] for p in sorted(evidence)}
            self._checks += [(name, p, s) for p, s in also.items()]
        self.fields.append(row)

    def save(self) -> dict[str, Any]:
        """Ends the last page and the file; returns the answer key."""
        assert self.page_no == self.total_pages, (
            f"{self.page_no} pages drawn, {self.total_pages} wanted"
        )
        self._foot()
        self.c.showPage()
        self.c.save()
        self.page_texts.append(self.printed)
        for name, page, evidence in self._checks:
            assert any(evidence in s for s in self.page_texts[page - 1]), (
                f"{name}: {evidence!r} is not printed on page {page}"
            )
        assert len(self.line_pages) == len(self.line_items)
        return {
            "document_type": self.document_type,
            "fields": self.fields,
            "line_items": self.line_items,
            "line_pages": self.line_pages,
            "pages": self.total_pages,
        }
