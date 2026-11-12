"""The generator gives the committed bytes, and the same bytes on every run."""

import hashlib
import importlib.util
from pathlib import Path

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
    assert len(committed) == 6  # three PDFs, three expected.json
