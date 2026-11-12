"""Demo mode routes: reset the demo and serve the mock document kit."""

import json
import os
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from app.store import DATA, Store

router = APIRouter(prefix="/demo")
KIT_SUFFIXES = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg"}


def _data() -> Path:
    return Path(os.environ.get("CLAIMS_DATA") or DATA)


def _kit() -> dict[tuple[str, str], Path]:
    """Every mock document, keyed by (claim folder, file name)."""
    return {
        (p.parent.name, p.name): p
        for p in sorted((_data() / "mock").glob("claim-*/*"))
        if p.suffix in KIT_SUFFIXES and p.is_file()
    }


@router.get("/kit")
def kit() -> list[dict[str, object]]:
    out = []
    for (folder, name), path in _kit().items():
        expected = path.with_suffix(".expected.json")
        doc_type = (
            json.loads(expected.read_text()).get("document_type")
            if expected.is_file()
            else None
        )
        out.append(
            {
                "folder": folder,
                "file": name,
                "document_type": doc_type,
                "size": path.stat().st_size,
            }
        )
    return out


@router.get("/kit/{folder}/{file}")
def kit_file(folder: str, file: str) -> FileResponse:
    # the path comes from the listing, never from the request
    path = _kit().get((folder, file))
    if path is None:
        raise HTTPException(404, "unknown document")
    return FileResponse(
        path,
        media_type=KIT_SUFFIXES[path.suffix],
        headers={"X-Content-Type-Options": "nosniff"},
    )


def _safe_uploads(uploads: Path) -> Path:
    """Refuses a folder where a wipe would do harm: /, home, the repository."""
    root = uploads.resolve()
    repo = Path(__file__).resolve().parents[2]
    if root in {Path(root.anchor), Path.home().resolve(), repo, *repo.parents}:
        raise HTTPException(409, "refusing to empty this folder")
    return root


@router.post("/reset")
def reset(request: Request) -> dict[str, int]:
    store: Store = request.app.state.store
    root = _safe_uploads(request.app.state.uploads)
    with store.lock:
        counts = store.reset()
        files = 0
        if root.is_dir():
            for child in root.iterdir():
                if child.is_dir():
                    files += sum(1 for f in child.rglob("*") if f.is_file())
                    shutil.rmtree(child)
                else:
                    files += 1
                    child.unlink()
    return {**counts, "files": files}
