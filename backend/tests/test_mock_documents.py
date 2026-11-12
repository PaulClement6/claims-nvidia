"""The generator gives the committed bytes, and the same bytes on every run."""

import hashlib
import importlib.util
import json
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops, ImageStat
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "generate_mock_documents", ROOT / "scripts" / "generate_mock_documents.py"
)
assert spec and spec.loader
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)


def sha(folder: Path) -> dict[str, str]:
    return {
        str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(folder.glob("**/*"))
        if p.suffix in {".pdf", ".json"} and p.name != "policies.json"
    }


def test_generator_is_deterministic_and_matches_the_committed_files(
    tmp_path: Path,
) -> None:
    gen.generate(tmp_path / "a")
    gen.generate(tmp_path / "b")
    committed = sha(ROOT / "data" / "mock")
    assert sha(tmp_path / "a") == sha(tmp_path / "b") == committed
    assert len(committed) == 14  # six PDFs, eight expected.json


def test_photos_are_deterministic_and_match_the_committed_files_up_to_the_jpeg_codec(
    tmp_path: Path,
) -> None:
    """The JPEG bytes depend on the libjpeg build (CI is Linux, a laptop is a Mac):
    two runs on one machine are byte-identical; against the committed file we
    compare the decoded pixels with a small tolerance. The reader uses the
    committed bytes."""
    gen.generate(tmp_path / "a")
    gen.generate(tmp_path / "b")
    for name in ("damage-photo-1.jpg", "damage-photo-2.jpg"):
        a = (tmp_path / "a" / "claim-2-property" / name).read_bytes()
        assert a == (tmp_path / "b" / "claim-2-property" / name).read_bytes()
        assert b"Exif" not in a
        made = Image.open(BytesIO(a)).convert("L")
        committed = Image.open(ROOT / "data" / "mock" / "claim-2-property" / name)
        diff = ImageChops.difference(made, committed.convert("L"))
        assert made.size == committed.size and ImageStat.Stat(diff).mean[0] < 2


CLAIM3 = ROOT / "data" / "mock" / "claim-3-health"


def test_the_medical_invoice_is_tampered_in_its_bytes() -> None:
    meta = PdfReader(CLAIM3 / "medical-invoice.pdf").metadata
    assert meta is not None
    assert meta.producer == meta.creator == gen.EDITOR
    assert meta.creation_date is not None and meta.modification_date is not None
    assert str(meta.creation_date.date()) == "2026-10-18"
    assert str(meta.modification_date.date()) == "2026-11-02"


def test_the_medical_report_is_clean() -> None:
    meta = PdfReader(CLAIM3 / "medical-report.pdf").metadata
    assert meta is not None and meta.producer is not None
    assert "ReportLab" in meta.producer and meta.creator == "anonymous"
    assert meta.creation_date == meta.modification_date


def test_the_invoice_prints_a_total_that_its_lines_do_not_add_up_to() -> None:
    expected = json.loads((CLAIM3 / "medical-invoice.expected.json").read_text())
    fields = {f["name"]: f["value"] for f in expected["fields"]}
    assert fields["patient_name"] == "Jonas Meier"
    assert fields["invoice_date"] == "2026-10-18" and fields["total"] == 4450.0
    assert sum(i["amount"] for i in expected["line_items"]) == 1450.0
