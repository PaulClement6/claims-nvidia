"""Writes the mock document kit to data/mock/ (the documents, one expected.json each,
the hard versions in hard/, and index.json).

Run: cd backend && uv run python ../scripts/generate_mock_documents.py [out_dir]
Same bytes on every run on one machine: reportlab invariant mode, fixed dates, no
random id; Pillow images with fixed random seeds and no EXIF; the tampered PDFs are
rewritten with pypdf, which keeps the fixed id of reportlab. The images (damage
photos, scans, phone photos) may differ between a Mac and Linux in the JPEG codec:
the tests compare their pixels with a tolerance.
All names, companies and addresses are invented (non-negotiable 7).

The code is in scripts/mockdocs/. To add a scenario, see mockdocs/kit.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mockdocs.kit import generate  # noqa: E402
from mockdocs.pdfdoc import EDITOR  # noqa: E402

__all__ = ["EDITOR", "generate"]

if __name__ == "__main__":
    generate(
        Path(sys.argv[1])
        if len(sys.argv) > 1
        else Path(__file__).resolve().parents[1] / "data" / "mock"
    )
