"""The generator gives the committed bytes, and the same bytes on every run."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageStat
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[2]
HARD_KEYS = {"variant", "source_document"}
spec = importlib.util.spec_from_file_location(
    "generate_mock_documents", ROOT / "scripts" / "generate_mock_documents.py"
)
assert spec and spec.loader
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)


MOCK = ROOT / "data" / "mock"
TOLERANCE = 3  # mean grey level of 255, on a picture reduced to 1/8


def is_image_based(p: Path) -> bool:
    """A photo, a phone photo, a scan: the bytes depend on the JPEG codec of the
    machine (and the scan on the fonts pdfium finds). The tests compare pixels."""
    return p.suffix == ".jpg" or p.parent.name == "hard" and p.suffix == ".pdf"


def exact(folder: Path) -> dict[str, str]:
    """SHA-256 of every file that must be byte-identical on every machine."""
    return {
        str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(folder.glob("**/*"))
        if p.suffix in {".pdf", ".json"}
        and p.name != "policies.json"
        and not is_image_based(p)
    }


def picture(p: Path) -> Image.Image:
    """The grey picture of an image file or of a scan PDF (its one image)."""
    img = PdfReader(p).pages[0].images[0].image if p.suffix == ".pdf" else Image.open(p)
    assert img is not None
    return img.convert("L")


def close(a: Path, b: Path) -> bool:
    x, y = picture(a), picture(b)
    if x.size != y.size:
        return False
    small = (x.width // 8, x.height // 8)
    diff = ImageChops.difference(
        x.resize(small, Image.Resampling.BOX), y.resize(small, Image.Resampling.BOX)
    )
    return bool(ImageStat.Stat(diff).mean[0] < TOLERANCE)


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    base = tmp_path_factory.mktemp("kit")
    gen.generate(base / "a")
    gen.generate(base / "b")
    return base / "a", base / "b"


def test_generator_is_deterministic_and_matches_the_committed_files(
    runs: tuple[Path, Path],
) -> None:
    a, b = runs
    # two runs: every file has the same bytes (this machine, this codec)
    everything = sorted(str(p.relative_to(a)) for p in a.glob("**/*") if p.is_file())
    assert everything == sorted(
        str(p.relative_to(b)) for p in b.glob("**/*") if p.is_file()
    )
    for name in everything:
        assert (a / name).read_bytes() == (b / name).read_bytes(), name
    # against the committed files: the same bytes, except for the image-based ones
    committed = exact(MOCK)
    assert exact(a) == committed
    index = json.loads((MOCK / "index.json").read_text())
    docs = [d for s in index["scenarios"] for d in s["documents"]]
    pdfs = [d for d in docs if d["file"].endswith(".pdf")]
    # PDFs, expected.json of the documents and of the hard versions, index.json
    assert len(committed) == len(pdfs) + len(docs) + len(index["hard"]) + 1
    assert sorted(everything) == sorted(
        str(p.relative_to(MOCK))
        for p in MOCK.glob("**/*")
        if p.is_file() and p.name != "policies.json"
    )


# SHA-256 of the lines "<file>:<sha256>" of the six PDFs and eight expected.json of
# the first three claim folders, as they were before the kit grew (issue #23).
FIRST_THREE = "82b0ab98d09c21a7ef4ac5fceec7036a57b5fa02507c8bb3a6a10a675fc4f71e"


def test_the_three_first_claim_files_keep_their_bytes() -> None:
    lines = [
        f"{k}:{v}" for k, v in sorted(exact(MOCK).items()) if k.startswith("claim-")
    ]
    assert len(lines) == 14
    assert hashlib.sha256("\n".join(lines).encode()).hexdigest() == FIRST_THREE


def test_images_match_the_committed_files_up_to_the_jpeg_codec(
    runs: tuple[Path, Path],
) -> None:
    """The damage photos, the scans and the phone photos: a small pixel tolerance
    against the committed file. The reader uses the committed bytes."""
    a, _ = runs
    images = [p for p in sorted(a.glob("**/*")) if is_image_based(p)]
    assert len(images) == 2 + 2 + 9  # claim-2 photos, new photos, hard versions
    for made in images:
        assert b"Exif" not in made.read_bytes()
        assert close(made, MOCK / made.relative_to(a)), made.name


def test_the_hard_versions_have_the_same_content_in_other_bytes() -> None:
    index = json.loads((MOCK / "index.json").read_text())
    assert [h["variant"] for h in index["hard"]].count("scan") == 6
    assert [h["variant"] for h in index["hard"]].count("photo") == 3
    for h in index["hard"]:
        path = MOCK / h["file"]
        expected = json.loads(path.with_suffix(".expected.json").read_text())
        source = MOCK / h["source_document"]
        original = json.loads(source.with_suffix(".expected.json").read_text())
        assert expected["variant"] == h["variant"]
        assert expected["source_document"] == h["source_document"]
        assert {k: v for k, v in expected.items() if k not in HARD_KEYS} == original
        assert path.read_bytes() != source.read_bytes()
        if h["variant"] == "scan":
            reader = PdfReader(path)
            assert path.suffix == ".pdf" and len(reader.pages) == 1
            assert reader.pages[0].extract_text().strip() == ""  # no text layer
            assert len(reader.pages[0].images) == 1
            assert picture(path).size == (1241, 1754)  # A4 at 150 DPI
            assert reader.metadata is not None and "ReportLab" in str(
                reader.metadata.producer
            )
        else:
            assert path.suffix == ".jpg" and path.read_bytes()[:3] == b"\xff\xd8\xff"
            assert Image.open(path).size == (1200, 1600)


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
