"""Demo mode routes: reset the demo and serve the mock document kit."""

import json
import os
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from app.store import DATA, Store

router = APIRouter(prefix="/demo")
KIT_SUFFIXES = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg"}
HARD = "hard"  # the folder of the scans and phone photos


def _data() -> Path:
    return Path(os.environ.get("CLAIMS_DATA") or DATA)


def _kit() -> dict[tuple[str, str], Path]:
    """Every mock document, keyed by (folder, file name): the scenarios first, then
    the hard versions."""
    found = [
        p
        for p in (_data() / "mock").glob("*/*")
        if p.suffix in KIT_SUFFIXES and p.is_file()
    ]
    found.sort(key=lambda p: (p.parent.name == HARD, p.parent.name, p.name))
    return {(p.parent.name, p.name): p for p in found}


def _index() -> dict[str, Any]:
    """`data/mock/index.json`, made by the generator: scenarios and hard versions."""
    path = _data() / "mock" / "index.json"
    index: dict[str, Any] = json.loads(path.read_text()) if path.is_file() else {}
    return index


@router.get("/kit")
def kit() -> list[dict[str, object]]:
    """Each document with its scenario: folder, title, policy and the story to type.
    A hard version (a scan or a phone photo) belongs to the scenario of its source."""
    index = _index()
    scenarios = {s["folder"]: s for s in index.get("scenarios", [])}
    hard = {h["file"]: h for h in index.get("hard", [])}
    out = []
    for (folder, name), path in _kit().items():
        expected = path.with_suffix(".expected.json")
        doc_type = (
            json.loads(expected.read_text()).get("document_type")
            if expected.is_file()
            else None
        )
        variant = hard.get(f"{folder}/{name}")
        scenario = scenarios.get(
            variant["source_document"].split("/")[0] if variant else folder
        )
        out.append(
            {
                "folder": folder,
                "file": name,
                "document_type": doc_type,
                "size": path.stat().st_size,
                "scenario": scenario["folder"] if scenario else None,
                "title": scenario["title"] if scenario else None,
                "customer_id": scenario["customer_id"] if scenario else None,
                "policy": scenario["policy"] if scenario else None,
                "story": scenario["stories"][0]["text"] if scenario else None,
                "variant": variant["variant"] if variant else None,
                "source_document": variant["source_document"] if variant else None,
            }
        )
    return out


@router.get("/scenarios")
def scenarios(policy: str) -> list[dict[str, object]]:
    """The sample cases of one policy for the customer panel: titles, stories (text
    only) and documents. No signal, no outcome, no cause: the customer must not see
    what the checks find."""
    index = _index()
    hard: dict[str, list[dict[str, str]]] = {}
    for h in index.get("hard", []):
        folder, name = h["file"].split("/", 1)
        hard.setdefault(h["source_document"], []).append(
            {
                "folder": folder,
                "file": name,
                "document_type": h["document_type"],
                "variant": h["variant"],
            }
        )
    return [
        {
            "folder": s["folder"],
            "title": s["title"],
            "stories": [{"text": st["text"]} for st in s["stories"]],
            "documents": [
                {
                    "file": d["file"],
                    "document_type": d["document_type"],
                    "hard": hard.get(f"{s['folder']}/{d['file']}", []),
                }
                for d in s["documents"]
            ],
        }
        for s in index.get("scenarios", [])
        if s["policy"] == policy
    ]


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
    agent = request.app.state.agent
    if hasattr(agent, "reset"):
        agent.reset()  # the checkpoints of the LangGraph agent hold the conversations
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
