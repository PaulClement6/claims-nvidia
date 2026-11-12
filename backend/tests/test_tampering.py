"""T001, T002, T003: the real bytes of the stored file, and the extracted fields."""

from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfWriter

from app.checks.tampering import check_tampering
from app.models import Claim, Document
from tests.builders import document, travel_claim
from tests.reader_contract import MOCK

CLAIM3 = MOCK / "claim-3-health"


def pdf(**meta: str) -> bytes:
    w = PdfWriter()
    w.add_blank_page(100, 100)
    if meta:
        w.add_metadata({f"/{k}": v for k, v in meta.items()})
    out = BytesIO()
    w.write(out)
    return out.getvalue()


def stored(tmp_path: Path, name: str, data: bytes, **changes: object) -> Claim:
    """A claim with one stored document; `changes` are fields of the Document."""
    (tmp_path / name).write_bytes(data)
    doc = Document.model_validate(
        {"id": "D-1", "file": name, "document_type": "medical_invoice", **changes}
    )
    return travel_claim(documents=[doc])


def codes(claim: Claim, uploads: Path) -> list[str]:
    return [s.code for s in check_tampering(claim, uploads)]


def test_the_edited_invoice_gives_t001_and_t002_with_page_1(tmp_path: Path) -> None:
    claim = stored(tmp_path, "i.pdf", (CLAIM3 / "medical-invoice.pdf").read_bytes())
    t1, t2 = check_tampering(claim, tmp_path)
    assert (t1.code, t1.points) == ("T001", 20) and (t2.code, t2.points) == ("T002", 10)
    for s in (t1, t2):
        assert [(e.document_id, e.page) for e in s.evidence] == [("D-1", 1)]
    assert "PDF Edit Studio" in t1.detail and "2026-11-02" in t2.detail


def test_the_clean_report_gives_nothing(tmp_path: Path) -> None:
    claim = stored(tmp_path, "r.pdf", (CLAIM3 / "medical-report.pdf").read_bytes())
    assert codes(claim, tmp_path) == []


@pytest.mark.parametrize(
    "tool",
    ["Adobe Photoshop 25", "Sejda PDF Editor", "PDF Edit Studio 4.2", "Smallpdf.com"],
)
def test_t001_names_an_editing_tool_as_producer_or_creator(
    tmp_path: Path, tool: str
) -> None:
    for key in ("Producer", "Creator"):
        claim = stored(tmp_path, "a.pdf", pdf(**{key: tool}))
        assert codes(claim, tmp_path) == ["T001"], (key, tool)


@pytest.mark.parametrize("tool", ["Microsoft Word", "ReportLab PDF Library", "pypdf"])
def test_t001_is_silent_for_a_normal_producer(tmp_path: Path, tool: str) -> None:
    assert codes(stored(tmp_path, "a.pdf", pdf(Producer=tool)), tmp_path) == []


@pytest.mark.parametrize(
    ("modified", "expected"),
    [
        ("D:20261018090000Z", []),  # same moment
        ("D:20261019090000Z", []),  # exactly one day
        ("D:20261019090001Z", ["T002"]),
        ("D:20261102143000Z", ["T002"]),
        ("D:20261001090000Z", []),  # modified before the creation: not our signal
    ],
)
def test_t002_needs_more_than_one_day(
    tmp_path: Path, modified: str, expected: list[str]
) -> None:
    data = pdf(CreationDate="D:20261018090000Z", ModDate=modified)
    assert codes(stored(tmp_path, "a.pdf", data), tmp_path) == expected


def test_t002_is_silent_with_one_date_only(tmp_path: Path) -> None:
    claim = stored(tmp_path, "a.pdf", pdf(CreationDate="D:20261018090000Z"))
    assert codes(claim, tmp_path) == []


@pytest.mark.parametrize(
    "data",
    [
        pdf(),  # no metadata of its own
        pdf(CreationDate="yesterday", ModDate="not a date", Producer=""),
        b"%PDF-1.4 broken",
        b"",
        b"\xff\xd8\xff\xe0 a jpeg",
        bytes(range(256)),
    ],
)
def test_no_metadata_a_broken_file_or_an_image_gives_no_signal_and_no_crash(
    tmp_path: Path, data: bytes
) -> None:
    for name in ("a.pdf", "a.jpg", "a.png"):
        assert codes(stored(tmp_path, name, data), tmp_path) == []


def test_a_missing_file_gives_no_signal(tmp_path: Path) -> None:
    claim = travel_claim(documents=[Document(id="D-1", file="nope.pdf")])
    assert codes(claim, tmp_path) == []


ITEMS: list[dict[str, float | str]] = [
    {"description": "a", "amount": 1000.0},
    {"description": "b", "amount": 450.0},
]


def with_total(total: float, items: list[dict[str, float | str]] = ITEMS) -> Claim:
    doc = document("D-inv", "medical_invoice", {"total": total}).model_copy(
        update={"line_items": items}
    )
    return travel_claim(documents=[doc])


def test_t003_lines_1450_total_4450(tmp_path: Path) -> None:
    [s] = check_tampering(with_total(4450.0), tmp_path)
    assert (s.code, s.points, s.severity) == ("T003", 30, "high")
    [e] = s.evidence  # the source of the total field
    assert (e.document_id, e.page, e.snippet) == ("D-inv", 1, "4450.0")
    assert "1450" in s.detail and "4450" in s.detail


@pytest.mark.parametrize(
    ("total", "expected"),
    [
        (1450.0, []),
        (1450.05, []),
        (1449.95, []),
        (1450.06, ["T003"]),
        (1449.9, ["T003"]),
    ],
)
def test_t003_tolerance_is_five_centimes(total: float, expected: list[str]) -> None:
    assert codes(with_total(total), Path()) == expected


@pytest.mark.parametrize("items", [[], [{"description": "no amount"}]])
def test_t003_needs_line_items_with_amounts_and_a_total(
    items: list[dict[str, float | str]],
) -> None:
    assert codes(with_total(4450.0, items), Path()) == []
    no_total = document("D-x", "medical_invoice", {}).model_copy(
        update={"line_items": ITEMS}
    )
    assert codes(travel_claim(documents=[no_total]), Path()) == []


def test_the_documents_of_claims_1_and_2_give_nothing(tmp_path: Path) -> None:
    for pdf_path in sorted(MOCK.glob("claim-[12]-*/*.pdf")):
        claim = stored(tmp_path, "a.pdf", pdf_path.read_bytes())
        assert codes(claim, tmp_path) == [], pdf_path.name
