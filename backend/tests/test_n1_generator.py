"""The generator of the N1 set (issue #125) gives the committed bytes, and the same
bytes on every run. The text PDFs and the keys are byte-identical to the documents of
the live run of 2026-11-19 (the saved answers of the model were made from them)."""

import importlib.util
from pathlib import Path

import pytest

from tests.test_mock_documents import ROOT, close

N1 = ROOT / "data" / "mock" / "n1"
spec = importlib.util.spec_from_file_location(
    "generate_mock_documents", ROOT / "scripts" / "generate_mock_documents.py"
)
assert spec and spec.loader
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)
IMAGES = {
    "n7-booking-confirmation-scan.pdf",
    "n8-repair-invoice-photo.jpg",
    "n9-receipt-scan.pdf",
}


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    base = tmp_path_factory.mktemp("n1")
    gen.generate_n1(base / "a")
    gen.generate_n1(base / "b")
    return base / "a", base / "b"


def names(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir())


def test_two_runs_give_the_same_files_with_the_same_bytes(
    runs: tuple[Path, Path],
) -> None:
    a, b = runs
    assert names(a) == names(b) == names(N1)
    for name in names(a):
        assert (a / name).read_bytes() == (b / name).read_bytes(), name


def test_the_text_pdfs_the_keys_and_the_manifest_are_the_committed_bytes(
    runs: tuple[Path, Path],
) -> None:
    a, _ = runs
    exact = [n for n in names(N1) if n not in IMAGES]
    assert len(exact) == 6 + 9 + 1  # text PDFs, keys of the nine, the manifest
    for name in exact:
        assert (a / name).read_bytes() == (N1 / name).read_bytes(), name


def test_the_scans_and_the_photo_match_the_committed_files_up_to_the_jpeg_codec(
    runs: tuple[Path, Path],
) -> None:
    a, _ = runs
    for name in sorted(IMAGES):
        assert b"Exif" not in (a / name).read_bytes()
        assert close(a / name, N1 / name), name


def test_the_json_files_have_unix_line_ends() -> None:
    paths = sorted(N1.glob("*.json"))
    assert len(paths) == 9 + 1
    for path in paths:
        assert b"\r" not in path.read_bytes(), path.name
