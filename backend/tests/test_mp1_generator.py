"""The generator of the MP1 set (issue #126) gives the committed bytes, and the same
bytes on every run, and what it prints is what the answer keys say.

What the tests of the scans can and cannot prove. The two scans are images: nothing
here reads them as text. The ground truth of a scan is its SYNTHETIC SOURCE, the
strings that the generator printed on each page before the page was made into an
image (`build` returns them). The key is held against those strings (a snippet is a
part of one string of its page, each cell of a line is a string), and the SHA-256 of
the manifest is held against them. Then each committed scan page is compared with the
page that the generator makes now, by pixels, with the tolerance of the kit
(`test_mock_documents.py`: the mean grey level of a picture reduced to 1/8, under 3
of 255). A pixel check sees the layout, the page order and a missing block; it does
NOT see one changed word, so a wrong word in the image is found only through the
source text and its hash. Nothing here proves that a reader can read the scan.
"""

import hashlib
import importlib
import json
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageStat
from pypdf import PdfReader

from tests.test_mock_documents import TOLERANCE
from tests.test_mp1_dataset import (
    BY_STEM,
    FOOT,
    MP1,
    SCAN_STEMS,
    SPEC,
    STEMS,
    TEXT_STEMS,
    expected_key,
    flat,
    has,
    key_of,
    manifest,
    mp1_module,
    page_texts,
    source_problems,
)

SCANS = [f"{s}.pdf" for s in SCAN_STEMS]


@pytest.fixture(scope="module")
def runs(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, Path, dict[str, list[list[str]]]]:
    """Two runs: `build` (which also returns the printed strings) and `generate_mp1`."""
    base = tmp_path_factory.mktemp("mp1")
    module = mp1_module()
    printed: dict[str, list[list[str]]] = module.build(base / "a")
    module.generate_mp1(base / "b")
    return base / "a", base / "b", printed


def names(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir())


# ===================================================== 8 the bytes of the files


def test_a_scan_of_several_pages_treats_a_page_as_the_scan_of_the_kit_does(
    tmp_path: Path,
) -> None:
    """`scan_pages` repeats the recipe of `hard.scan` (which the kit and N1 use and
    which must not change). For a PDF of one page, the same seed and the same angle
    give the same bytes: a later change of one recipe and not of the other fails
    here."""
    mp1_module()  # puts `scripts` on the path
    hard = importlib.import_module("mockdocs.hard")
    pdfdoc = importlib.import_module("mockdocs.pdfdoc")
    source = tmp_path / "source.pdf"
    page = pdfdoc.Doc(source, "One page", "receipt")
    page.text(pdfdoc.MARGIN, pdfdoc.H - 100, "A line of text, 1,234.50")
    page.save()
    hard.scan(source, tmp_path / "one.pdf", 4711, 0.7)
    hard.scan_pages(source, tmp_path / "many.pdf", [4711], [0.7])
    assert (tmp_path / "one.pdf").read_bytes() == (tmp_path / "many.pdf").read_bytes()


def test_two_runs_give_the_same_files_with_the_same_bytes(
    runs: tuple[Path, Path, dict[str, list[list[str]]]],
) -> None:
    a, b, _ = runs
    assert names(a) == names(b) == names(MP1)
    for name in names(a):
        assert (a / name).read_bytes() == (b / name).read_bytes(), name


def test_the_text_pdfs_the_keys_and_the_manifest_are_the_committed_bytes(
    runs: tuple[Path, Path, dict[str, list[list[str]]]],
) -> None:
    a, _, _ = runs
    exact = [n for n in names(MP1) if n not in SCANS]
    assert len(exact) == 6 + 8 + 1  # text PDFs, keys of the eight, the manifest
    for name in exact:
        assert (a / name).read_bytes() == (MP1 / name).read_bytes(), name


def picture(pdf: Path, index: int) -> Image.Image:
    """The grey picture of page `index` (from 0) of a scan."""
    img = PdfReader(pdf).pages[index].images[0].image
    assert img is not None
    return img.convert("L")


def distance(a: Image.Image, b: Image.Image) -> float:
    """The mean grey level of the difference of two pictures reduced to 1/8; a
    different size is as far as it can be."""
    if a.size != b.size:
        return 255.0
    small = (a.width // 8, a.height // 8)
    diff = ImageChops.difference(
        a.resize(small, Image.Resampling.BOX), b.resize(small, Image.Resampling.BOX)
    )
    return float(ImageStat.Stat(diff).mean[0])


def scan_problems_against(made: Path, committed: Path) -> list[str]:
    """Page by page: the page count, then each committed page against the same page
    that the generator makes now."""
    ours, theirs = PdfReader(made), PdfReader(committed)
    if len(ours.pages) != len(theirs.pages):
        return [f"{len(ours.pages)} pages made, {len(theirs.pages)} committed"]
    found = []
    for i in range(len(ours.pages)):
        d = distance(picture(made, i), picture(committed, i))
        if d >= TOLERANCE:
            found.append(f"page {i + 1}: the mean difference is {d:.2f}, not under 3")
    return found


@pytest.mark.parametrize("file", SCANS)
def test_a_scan_matches_the_committed_file_page_by_page_up_to_the_jpeg_codec(
    file: str, runs: tuple[Path, Path, dict[str, list[list[str]]]]
) -> None:
    a, _, _ = runs
    assert b"Exif" not in (a / file).read_bytes()
    assert scan_problems_against(a / file, MP1 / file) == []


@pytest.mark.parametrize("file", SCANS)
def test_the_pixel_comparison_of_a_scan_can_fail_on_another_page_or_another_scan(
    file: str, runs: tuple[Path, Path, dict[str, list[list[str]]]]
) -> None:
    a, _, _ = runs
    own = distance(picture(a / file, 0), picture(MP1 / file, 0))
    assert own < TOLERANCE
    # the same scan, the next page: a wrong page order would be seen
    pages = len(PdfReader(MP1 / file).pages)
    for i in range(1, pages):
        assert distance(picture(a / file, 0), picture(MP1 / file, i)) >= TOLERANCE
    # the other scan: a swapped file would be seen
    other = next(f for f in SCANS if f != file)
    assert distance(picture(a / file, 0), picture(MP1 / other, 0)) >= TOLERANCE


def test_the_json_files_have_unix_line_ends_and_hold_only_ascii() -> None:
    paths = sorted(MP1.glob("*.json"))
    assert len(paths) == 8 + 1
    for path in paths:
        data = path.read_bytes()
        assert b"\r" not in data, path.name
        assert data.isascii(), path.name
        assert json.loads(data)  # and it is JSON


# ================================== the printed strings, the ground truth of a scan


def test_build_returns_the_printed_strings_of_each_page_of_each_document(
    runs: tuple[Path, Path, dict[str, list[list[str]]]],
) -> None:
    _, _, printed = runs
    assert list(printed) == [f"{s}.pdf" for s in STEMS]
    for spec in SPEC:
        pages = printed[f"{spec.stem}.pdf"]
        assert len(pages) == spec.pages, spec.stem
        assert all(
            page and all(isinstance(s, str) and s for s in page) for page in pages
        )


def test_two_builds_print_the_same_strings(
    runs: tuple[Path, Path, dict[str, list[list[str]]]], tmp_path: Path
) -> None:
    _, _, printed = runs
    assert mp1_module().build(tmp_path) == printed


@pytest.mark.parametrize("stem", STEMS)
def test_each_page_ends_with_its_foot_line_and_starts_with_its_running_header(
    stem: str, runs: tuple[Path, Path, dict[str, list[list[str]]]]
) -> None:
    _, _, printed = runs
    spec, pages = BY_STEM[stem], printed[f"{stem}.pdf"]
    for n, strings in enumerate(pages, 1):
        assert strings[-1] == FOOT.format(n, spec.pages), (stem, n)  # drawn last
        if n in spec.headers:
            header, one_string = spec.headers[n]
            if one_string:
                assert strings[0] == header, (stem, n)
            else:  # label and value may be two strings
                assert flat("".join(strings[:2])).startswith(flat(header)), (stem, n)
                assert flat(strings[0]) in flat(header)


@pytest.mark.parametrize("stem", STEMS)
def test_the_printed_strings_hold_the_texts_of_the_specification(
    stem: str, runs: tuple[Path, Path, dict[str, list[list[str]]]]
) -> None:
    _, _, printed = runs
    spec = BY_STEM[stem]
    missing = [
        (n, s)
        for n, (wanted, strings) in enumerate(
            zip(spec.printed, printed[f"{stem}.pdf"], strict=True), 1
        )
        for s in wanted
        if not has("\n".join(strings), s)
    ]
    assert missing == []


ONE_STRING = [
    (
        "mp1-02-medical-invoice-continued.pdf",
        0,
        "Cost estimate of 2026-08-28: CHF 650.00",
    ),
    (
        "mp1-04-repair-quote-terms.pdf",
        1,
        "Accepted by the customer: Gottfried Wirz",
    ),
    (
        "mp1-07-booking-confirmation-no-price.pdf",
        1,
        "Deposit received on 2026-09-05: CHF 430.00",
    ),
    (
        "mp1-07-booking-confirmation-no-price.pdf",
        1,
        "Next instalment, due 2026-12-01: CHF 600.00",
    ),
    (
        "mp1-08-police-report-two-dates.pdf",
        1,
        "The theft took place on 2026-11-10 between 06:15 and 07:20.",
    ),
]


@pytest.mark.parametrize(("file", "page", "text"), ONE_STRING)
def test_a_text_of_a_lure_is_printed_as_one_string(
    file: str,
    page: int,
    text: str,
    runs: tuple[Path, Path, dict[str, list[list[str]]]],
) -> None:
    """The specification prints these as one text, so that a snippet or the label
    before a value is one drawn string (a reader sees one line)."""
    _, _, printed = runs
    assert text in printed[file][page]


# ------------------------------------------------ the key against the source text


@pytest.mark.parametrize("stem", STEMS)
def test_the_key_of_each_document_is_true_of_its_printed_strings(
    stem: str, runs: tuple[Path, Path, dict[str, list[list[str]]]]
) -> None:
    _, _, printed = runs
    assert source_problems(key_of(stem), printed[f"{stem}.pdf"]) == []


@pytest.mark.parametrize("stem", SCAN_STEMS)
def test_the_scan_pages_hold_every_value_of_the_key_in_their_source(
    stem: str, runs: tuple[Path, Path, dict[str, list[list[str]]]]
) -> None:
    """Ground truth of a scan: value, key page and accepted pages, from the source."""
    _, _, printed = runs
    pages = printed[f"{stem}.pdf"]
    for f in key_of(stem)["fields"]:
        if f["value"] is None:
            continue
        accepted = f.get("accepted_pages") or [f["page"]]
        holding = [
            n
            for n, strings in enumerate(pages, 1)
            if any(f["snippet"] in s for s in strings)
        ]
        assert holding == accepted, (stem, f["name"])


@pytest.mark.parametrize("stem", TEXT_STEMS)
def test_every_printed_string_of_a_text_pdf_is_in_its_text_layer(
    stem: str, runs: tuple[Path, Path, dict[str, list[list[str]]]]
) -> None:
    _, _, printed = runs
    texts = page_texts(MP1 / f"{stem}.pdf")
    for n, (strings, text) in enumerate(
        zip(printed[f"{stem}.pdf"], texts, strict=True), 1
    ):
        assert [s for s in strings if not has(text, s)] == [], (stem, n)


def hash_problems(pages: list[list[str]], hashes: list[str]) -> list[str]:
    """The SHA-256 of the UTF-8 text of each source page (its strings joined with a
    new line, in drawing order) against the hashes of the manifest."""
    if len(pages) != len(hashes):
        return [f"{len(pages)} source pages, {len(hashes)} hashes"]
    return [
        f"page {n}: the text hashes to another value"
        for n, (strings, h) in enumerate(zip(pages, hashes, strict=True), 1)
        if hashlib.sha256("\n".join(strings).encode("utf-8")).hexdigest() != h
    ]


def test_the_hash_of_the_source_text_of_each_scan_page_is_in_the_manifest(
    runs: tuple[Path, Path, dict[str, list[list[str]]]],
) -> None:
    _, _, printed = runs
    by_file = {d["file"]: d for d in manifest()["documents"]}
    for file in SCANS:
        assert hash_problems(printed[file], by_file[file]["source_text_sha256"]) == []
    assert all("source_text_sha256" not in by_file[f"{s}.pdf"] for s in TEXT_STEMS)


# ----------------------------------------------------------- they can fail


def test_the_key_check_names_a_snippet_a_cell_and_a_page_count_of_a_wrong_source(
    runs: tuple[Path, Path, dict[str, list[list[str]]]],
) -> None:
    _, _, printed = runs
    stem, file = SCAN_STEMS[0], SCANS[0]
    key = expected_key(BY_STEM[stem])
    assert source_problems(key, printed[file]) == []
    # a scan whose source has another word on page 3
    changed = [list(page) for page in printed[file]]
    changed[2] = [s.replace("1,893.00", "1,983.00") for s in changed[2]]
    found = source_problems(key, changed)
    assert any("refund_received" in p and "page 3" in p for p in found), found
    # a line that is not a cell of its page
    wrong = expected_key(BY_STEM[stem])
    wrong["line_items"][1]["amount"] = -859.0
    assert any(
        "not a cell of page 2" in p for p in source_problems(wrong, printed[file])
    )
    # a page missing from the source
    assert any("page count" in p for p in source_problems(key, printed[file][:2]))


def test_the_hash_check_names_a_changed_word_a_swapped_page_and_a_missing_page(
    runs: tuple[Path, Path, dict[str, list[list[str]]]],
) -> None:
    _, _, printed = runs
    file = SCANS[1]  # the medical report, two pages
    hashes = {d["file"]: d for d in manifest()["documents"]}[file]["source_text_sha256"]
    pages = printed[file]
    assert hash_problems(pages, hashes) == []
    one_word = [list(pages[0]), [s.replace("lumbar", "lumbal") for s in pages[1]]]
    assert hash_problems(one_word, hashes) == [
        "page 2: the text hashes to another value"
    ]
    assert len(hash_problems(list(reversed(pages)), hashes)) == 2
    assert hash_problems(pages[:1], hashes) == ["1 source pages, 2 hashes"]
