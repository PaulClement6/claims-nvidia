"""The controlled comparison of issue #126, and its pilot.

    cd backend
    uv run python -m app.docintel.experiment dry-run main --out DIR   # a stub
    uv run python -m app.docintel.experiment dry-run pilot --out DIR  # a stub
    uv run python -m app.docintel.experiment freeze --out FILE --method METHOD.md
    uv run python -m app.docintel.experiment replay DIR
    uv run python -m app.docintel.experiment verdict DIR

Live, each with its own authorization text (one line each):

    uv run --env-file ../.env python -m app.docintel.experiment pilot --out DIR
        --freeze FILE --authorize "issue126-pilot-4-requests-cap-8 <who> <date>"
    uv run --env-file ../.env python -m app.docintel.experiment main --out DIR
        --freeze FILE --authorize "issue126-main-129-requests-cap-145 <who> <date>"

The main run reads the set MP1 six times with the settings of #18: one call per page
(the control) and one call per document (the candidate), in the order page, document,
document, page, page, document, each run with a new reader. Only the scope of the
extraction call differs. It sends 27 or 16 requests per run, 129 in all, and never
more than 145 HTTP attempts: every attempt is counted before it is sent.

The pilot is another invocation with another authorization. It sends the two pilot
documents (not in MP1) in the document scope: 2 classify calls and 2 document calls,
8 attempts at most. It says if the endpoint takes such a request. It starts nothing.

A live mode needs the three environment variables of the reader, an authorization
text that names the mode, its requests and its cap, and the file that `freeze` wrote
(the hashes of the code, the data, the method and the rules). Nothing restarts by
itself. Every answer is written at once (`calls.jsonl`); `replay` reads the runs
again from that file with no request and must give the same reports. `verdict`
applies the rules of issue #20 (`acceptance.py`). Nothing here changes a default.
"""

import argparse
import base64
import hashlib
import importlib
import json
import math
import os
import subprocess
import sys
import time
import warnings
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx2 as httpx
from PIL import Image

from app.docintel import acceptance, omni, telemetry
from app.docintel import evaluate as ev
from app.docintel.omni import OmniSettings
from app.docintel.schemas import SCHEMAS
from app.models import Document, DocumentType

BACKEND = Path(__file__).resolve().parents[2]
REPO = BACKEND.parent
SET = "mp1"
ORDER = ("page", "document", "document", "page", "page", "document")
PLANNED = {"main": 129, "pilot": 4}  # logical calls
CAP = {"main": 145, "pilot": 8}  # HTTP attempts, counted before each one is sent
AUTHORIZATION = {
    "main": "issue126-main-129-requests-cap-145",
    "pilot": "issue126-pilot-4-requests-cap-8",
}
LOCK = BACKEND / ".dev-data" / "issue-126" / "experiment.lock"  # one live run at a time
ENV = ("NVIDIA_KEY", "VLM_BASE_URL", "VLM_MODEL")
FROZEN = (  # what a live run must find as `freeze` saw it
    "backend/app/docintel/omni.py",
    "backend/app/docintel/labels.py",
    "backend/app/docintel/schemas.py",
    "backend/app/docintel/evaluate.py",
    "backend/app/docintel/compare.py",
    "backend/app/docintel/telemetry.py",
    "backend/app/docintel/acceptance.py",
    "backend/app/docintel/experiment.py",
    "backend/app/fraud/routing.py",
    "backend/app/models.py",
    "scripts/mockdocs/pilot.py",
    "scripts/mockdocs/paged.py",
    "scripts/mockdocs/hard.py",
    "scripts/mockdocs/pdfdoc.py",
)
VOLATILE = {"created_at", "started_at", "run_id", "command", "seconds"}
VOLATILE |= {"seconds_per_document"}
PILOT_TOKEN_SHARE = 0.75  # of max_tokens: above it the main run would be at risk
Json = dict[str, Any]


def plan() -> list[tuple[str, str]]:
    """The six runs of the main comparison, in order: (tag, scope)."""
    return [(f"r{n}-{scope}", scope) for n, scope in enumerate(ORDER, 1)]


def planned_calls(scope: str) -> int:
    """The requests of one run when every type is right: one classify call per
    document, then one call per page, or one per document of two pages or more."""
    total = 0
    for doc in ev.kit(SET):
        pages = omni._length(doc.path.read_bytes())
        total += 1 + (1 if scope == "document" and pages > 1 else pages)
    return total


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(path: Path, data: Any) -> None:
    text = data if isinstance(data, str) else json.dumps(data, indent=2) + "\n"
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _git(*args: str) -> str:
    done = subprocess.run(["git", *args], capture_output=True, text=True, cwd=REPO)
    return done.stdout.strip() if done.returncode == 0 else ""


def fingerprint(method: Path | None = None) -> dict[str, str]:
    """SHA-256 of every file that must not change between the freeze and a live
    run: the code, the documents and keys of MP1, the method. Line ends do not
    count for a text file."""
    files = {name: REPO / name for name in FROZEN}
    for path in sorted((REPO / "data" / "mock" / SET).iterdir()):
        files[f"data/mock/{SET}/{path.name}"] = path
    if method is not None:
        files["method"] = method
    found = {}
    for name, path in files.items():
        data = path.read_bytes()
        if path.suffix != ".pdf":
            data = data.replace(b"\r\n", b"\n")
        found[name] = _sha(data)
    return found


def freeze(out: Path, method: Path | None) -> Json:
    """Writes what a live run is held to. Made once, before the first live answer."""
    frozen = {
        "created": _now(),
        "git_head": _git("rev-parse", "HEAD"),
        "files": fingerprint(method),
        "method": str(method) if method else None,
        "order": list(ORDER),
        "planned": PLANNED,
        "cap": CAP,
        "config_ids": {s: omni.config_id(OmniSettings(scope=s)) for s in omni.SCOPES},
        "prompt_sha256": {s: omni.prompt_sha256("v1", s) for s in omni.SCOPES},
        "rules": {
            "gain": acceptance.GAIN,
            "supplementary_thresholds": list(acceptance.SENSITIVITY),
            "runs": acceptance.RUNS,
        },
    }
    _write(out, frozen)
    return frozen


def preflight(
    mode: str, out: Path, *, live: bool, frozen: Path | None = None, authorize: str = ""
) -> list[str]:
    """Why this run must not start. An empty list: it may. Nothing is sent and no
    file is written here."""
    problems = []
    if out.exists():
        problems.append(f"{out} exists: a run never writes into an old folder")
    if LOCK.exists():
        problems.append(f"{LOCK} exists: another live run is open, or one ended badly")
    if OmniSettings() != omni.ISSUE_18 or omni.ISSUE_18.scope != "page":
        problems.append("the default settings are not those of #18")
    docs = ev.kit(SET)
    pages = sum(omni._length(d.path.read_bytes()) for d in docs)
    if (len(docs), pages, sum(d.key is None for d in docs)) != (8, 19, 0):
        problems.append(f"the set {SET} is not 8 documents, 19 pages, 8 answer keys")
    made = sum(planned_calls(scope) for scope in ORDER)
    if made != PLANNED["main"]:
        problems.append(f"the plan gives {made} requests, not {PLANNED['main']}")
    if not live:
        return problems
    missing = [name for name in ENV if not os.environ.get(name)]
    if missing:
        problems.append(f"a live run needs {', '.join(missing)}")
    if (
        not authorize.startswith(AUTHORIZATION[mode] + " ")
        or not authorize[len(AUTHORIZATION[mode]) :].strip()
    ):
        problems.append(
            f'a live {mode} run needs --authorize "{AUTHORIZATION[mode]} <who> <date>"'
        )
    if frozen is None or not frozen.is_file():
        problems.append("a live run needs --freeze with the file that `freeze` wrote")
        return problems
    saved = _read(frozen)
    method = Path(saved["method"]) if saved.get("method") else None
    if method is None or not method.is_file():
        problems.append("the freeze names no method file, or the file is gone")
    else:
        now = fingerprint(method)
        changed = sorted(n for n in saved["files"] if now.get(n) != saved["files"][n])
        changed += sorted(n for n in now if n not in saved["files"])
        if changed:
            problems.append(f"changed since the freeze: {', '.join(changed)}")
    if saved.get("git_head") != _git("rev-parse", "HEAD"):
        problems.append("the commit is not the commit of the freeze")
    dirty = _git("status", "--porcelain", "--", *FROZEN, f"data/mock/{SET}")
    if dirty:
        problems.append("a frozen file is not committed")
    if (saved.get("planned"), saved.get("cap"), saved.get("order")) != (
        PLANNED,
        CAP,
        list(ORDER),
    ):
        problems.append("the plan or the caps are not those of the freeze")
    return problems


# ------------------------------------------------------------------ the stub model


def _images(body: Json) -> list[bytes]:
    return [
        base64.b64decode(p["image_url"]["url"].split(",", 1)[1])
        for p in body["messages"][0]["content"]
        if p["type"] == "image_url"
    ]


def page_answers(key: Json) -> list[Json]:
    """The right answer for each page of a document, from its answer key."""
    schema = SCHEMAS[DocumentType(key["document_type"])]
    answers = []
    for number in range(1, key["pages"] + 1):
        values: Json = {n: None for n in schema.model_fields if n != "line_items"}
        snippets: Json = {}
        for f in key["fields"]:
            if f["value"] is not None and f["page"] == number:
                values[f["name"]] = f["value"]
                snippets[f["name"]] = f["snippet"]
        if "line_items" in schema.model_fields:
            pairs = zip(key["line_items"], key["line_pages"], strict=True)
            values["line_items"] = [line for line, page in pairs if page == number]
        answers.append({"values": values, "snippets": snippets})
    return answers


def key_model(folder: Path) -> httpx.MockTransport:
    """A stub of the endpoint for a dry run: it answers each request from the answer
    key of the document that the first image shows. It is a perfect model, so a dry
    run says nothing about a real one. No network."""
    shown: dict[str, tuple[Json, int]] = {}
    for path in sorted(folder.glob("*.pdf")):
        key = _read(path.with_suffix(".expected.json"))
        for number, page in enumerate(omni.pages(path.read_bytes()), 1):
            shown[_sha(page.image)] = (key, number)

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        images = _images(body)
        key, number = shown[_sha(images[0])]
        answers = page_answers(key)
        content: Json
        if telemetry.call_kind(body) == "classify":
            content = {"document_type": key["document_type"]}
        elif telemetry.call_kind(body) == "document":
            content = {f"page_{n}": a for n, a in enumerate(answers, 1)}
        else:
            content = answers[number - 1]
        text = json.dumps(content, indent=2)
        usage = {
            "prompt_tokens": 100 + sum(_image_tokens(i) for i in images),
            "completion_tokens": len(text) // 3,
            "prompt_tokens_details": {"cached_tokens": 0},
            "completion_tokens_details": {"reasoning_tokens": 0},
        }
        choice = {"message": {"content": text}, "finish_reason": "stop"}
        return httpx.Response(200, json={"choices": [choice], "usage": usage})

    return httpx.MockTransport(handle)


def _image_tokens(image: bytes) -> int:
    """The prompt tokens of one image: one per block of 32 x 32 px (measured in
    #20 for images that the endpoint does not make smaller)."""
    with Image.open(BytesIO(image)) as picture:
        width, height = picture.size
    return math.ceil(width / 32) * math.ceil(height / 32)


@contextmanager
def _environment(model: str | None = None) -> Iterator[None]:
    """The variables that the reader needs, for a run that sends nothing (a dry run,
    a replay). A value that is set stays; only the model name can be given, because
    it is part of each request."""
    stand_in = {
        "NVIDIA_KEY": "not-a-key-dry-run",
        "VLM_BASE_URL": "https://dry-run.invalid/v1",
        "VLM_MODEL": model or "dry-run-model",
    }
    before = {name: os.environ.get(name) for name in stand_in}
    os.environ.update(stand_in)
    try:
        yield
    finally:
        for name, value in before.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _secrets() -> list[str]:
    """What must never be written: the key of the model."""
    return [value for name in ("NVIDIA_KEY",) if (value := os.environ.get(name))]


# ------------------------------------------------------------------- the main run


def run_main(
    out: Path,
    transport: httpx.BaseTransport,
    *,
    mode: str,
    authorize: str = "",
    frozen: Path | None = None,
    pace: float = 0.0,
    lock: Path | None = None,
    clock: ev.Clock = time.perf_counter,
    sleep: Callable[[float], None] = time.sleep,
) -> Json:
    """The six runs through `transport`. Returns the meta data (also written). A
    stop ends everything: no run is tried again and nothing restarts."""
    out.mkdir(parents=True)
    meta: Json = {
        "experiment": "issue-126 main",
        "mode": mode,
        "approval": authorize,
        "freeze_sha256": _sha(frozen.read_bytes()) if frozen else None,
        "git_head": _git("rev-parse", "HEAD"),
        "model": os.environ.get("VLM_MODEL"),
        "endpoint": ev._endpoint(os.environ.get("VLM_BASE_URL")),
        "order": list(ORDER),
        "planned": PLANNED["main"],
        "cap": CAP["main"],
        "pace_seconds": pace,
        "started": _now(),
        "runs": [],
    }
    secrets = _secrets()
    status = "complete"
    recorder: telemetry.Recorder | None = None
    try:
        with telemetry.Lock(lock or LOCK):
            recorder = telemetry.Recorder(
                out / "calls.jsonl",
                cap=CAP["main"],
                inner=transport,
                secrets=secrets,
                pace=pace,
                sleep=sleep,
            )
            for tag, scope in plan():
                recorder.begin(tag)
                settings = OmniSettings(scope=scope)
                make, named, meter = ev._omni(
                    transport=recorder.transport, sleep=recorder.sleep, options=settings
                )
                report = ev.evaluate(
                    make,
                    1,
                    settings=named,
                    meter=meter,
                    document_set=SET,
                    command=f"experiment {mode} {tag}",
                    clock=clock,
                )
                _write(out / f"run-{tag}.json", ev.dump(report))
                meta["runs"].append(tag)
    except telemetry.Stop as stop:
        status = f"stopped: {stop.reason}"
    except Exception as error:  # a crash is an incomplete experiment, never a retry
        status = f"error: {type(error).__name__}"
    meta |= {"ended": _now(), "attempts": recorder.attempts if recorder else 0}
    if telemetry.find_secrets(out, secrets):
        status = "stopped: a secret is in a written file"
    meta["status"] = status
    _write(out / "meta.json", meta)
    return meta


def unusable(call: Json) -> bool:
    """Is the final answer of an extraction call a 200 that cannot be used: cut at
    the token limit (also when its text is whole JSON), empty, not JSON, or not in
    the asked form? The asked form is the same in both scopes: a page answer, and
    each block of a document answer, is an object with the objects `values` and
    `snippets`; a document answer has one block for each page sent and no other
    key. (A call with no 200 is counted apart: it has no answer at all.)"""
    last = call["attempts"][-1]
    if call["kind"] == "classify" or last.get("status") != 200:
        return False
    if last.get("finish_reason") == "length":
        return True
    try:
        content = json.loads(last["body"])["choices"][0]["message"]["content"]
    except (ValueError, LookupError, TypeError):
        return True
    if call["kind"] == "document":
        blocks, exact = omni._blocks(content, len(call["images"]))
        return not exact or not all(_formed(block) for block in blocks)
    return not _formed(omni._json(content))


def _formed(answer: Any) -> bool:
    return (
        isinstance(answer, dict)
        and isinstance(answer.get("values"), dict)
        and isinstance(answer.get("snippets"), dict)
    )


def assess(out: Path) -> Json:
    """The verdict of a finished main run, from its files. It checks first that the
    six runs are one valid experiment; a failed check gives "no verdict"."""
    meta = _read(out / "meta.json")
    record = out / "calls.jsonl"
    with warnings.catch_warnings(record=True) as broken:
        warnings.simplefilter("always")
        lines = telemetry.load(record) if record.exists() else []
    summary = telemetry.summary(lines)
    by_call: dict[str, list[Json]] = {}
    for call in telemetry.calls(lines):
        by_call.setdefault(call["tag"], []).append(call)
    integrity = [f"the record of the requests is cut: {w.message}" for w in broken]
    if meta["status"] != "complete":
        integrity.append(f"the experiment did not end: {meta['status']}")
    if summary["attempts"] > meta["cap"]:
        integrity.append("more attempts were sent than the cap allows")
    runs: dict[str, list[acceptance.Run]] = {"page": [], "document": []}
    requests = []
    signatures, codes = set(), set()
    for tag, scope in plan():
        path = out / f"run-{tag}.json"
        if not path.is_file():
            integrity.append(f"the run {tag} has no report")
            continue
        report = _read(path)
        [run] = report["run_details"]
        counted = summary["by_tag"].get(tag, {})
        made = sum(counted.get("calls", {}).values())
        requests.append(
            {
                "run": tag,
                "planned": planned_calls(scope),
                "calls": made,
                "attempts": counted.get("attempts", 0),
                "retries": counted.get("retries", 0),
            }
        )
        if run["http"]["requests"] != counted.get("attempts"):
            integrity.append(f"{tag}: the report and the record count other requests")
        if report["settings"]["config_id"] != omni.config_id(OmniSettings(scope=scope)):
            integrity.append(f"{tag}: the settings are not those of the {scope} scope")
        if report["kit"].get("set") != SET or not report["complete"]:
            integrity.append(f"{tag}: the report is not a complete report of {SET}")
        signatures.add(json.dumps(ev._documents_signature(run), sort_keys=True))
        codes.add(report["reader_code_sha256"])
        cannot_use = sum(unusable(c) for c in by_call.get(tag, []))
        if scope == "page" and cannot_use:
            # Rule 3f judges the candidate. An unusable answer of the control would
            # lower the score of the control and so help the candidate: no verdict.
            integrity.append(
                f"{tag}: {cannot_use} extraction answer(s) of the control are cut "
                "or not in the asked form"
            )
        runs[scope].append(
            acceptance.facts(
                run,
                calls_without_answer=counted.get("calls_without_answer", 0),
                unusable_answers=cannot_use,
                documents=len(ev.kit(SET)),
            )
        )
    if len(signatures) > 1:
        integrity.append("the runs did not read the same documents and answer keys")
    if len(codes) > 1:
        integrity.append("the runs did not use the same reader code")
    verdict = acceptance.judge(runs["page"], runs["document"], integrity)
    verdict |= {"requests": requests, "telemetry": summary, "status": meta["status"]}
    _write(out / "verdict.json", verdict)
    _write(out / "verdict.md", acceptance.text(verdict))
    return verdict


def stable(value: Any) -> Any:
    """A report with no time and no id: what a replay must give again."""
    if isinstance(value, dict):
        return {k: stable(v) for k, v in value.items() if k not in VOLATILE}
    if isinstance(value, list):
        return [stable(v) for v in value]
    return value


def replay(out: Path) -> list[str]:
    """Reads every run again from `calls.jsonl`, through the reader and the harness
    of now, with no request. Returns the differences from the saved reports: an
    empty list says that the saved answers give the saved reports."""
    meta = _read(out / "meta.json")
    lines = telemetry.load(out / "calls.jsonl")
    differences = []
    with _environment(meta.get("model")):
        for tag, scope in plan():
            path = out / f"run-{tag}.json"
            if not path.is_file():
                differences.append(f"{tag}: no saved report")
                continue
            again = telemetry.Replay([line for line in lines if line["tag"] == tag])
            make, named, meter = ev._omni(
                transport=again,
                sleep=lambda seconds: None,
                options=OmniSettings(scope=scope),
            )
            try:
                report = ev.evaluate(
                    make, 1, settings=named, meter=meter, document_set=SET
                )
            except telemetry.Stop as stop:
                differences.append(f"{tag}: {stop.reason}")
                continue
            saved = _read(path)
            for part in ("settings", "run_details", "kit", "reader_code_sha256"):
                if part == "settings":  # the endpoint is a stand-in in a replay
                    a, b = dict(report[part]), dict(saved[part])
                    a.pop("endpoint", None), b.pop("endpoint", None)
                else:
                    a, b = report[part], saved[part]
                if stable(a) != stable(b):
                    differences.append(f"{tag}: {part} differs from the saved report")
            if again.left:
                differences.append(
                    f"{tag}: {again.left} saved attempts were not asked for"
                )
    return differences


# ---------------------------------------------------------------------- the pilot


def pilot_documents(folder: Path) -> list[Path]:
    """Writes the two pilot documents (not in MP1, N1 or the kit) and their keys."""
    scripts = str(REPO / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    importlib.import_module("mockdocs.pilot").generate_pilot(folder)
    return sorted(folder.glob("*.pdf"))


def run_pilot(
    out: Path,
    transport: httpx.BaseTransport | None,
    *,
    mode: str,
    authorize: str = "",
    frozen: Path | None = None,
    pace: float = 0.0,
    lock: Path | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Json:
    """The two pilot documents in the document scope, with one reader. `transport`
    None: a dry run with the stub. Returns the result (also written). A pilot starts
    nothing else, whatever its result."""
    out.mkdir(parents=True)
    paths = pilot_documents(out / "documents")
    inner = transport if transport is not None else key_model(out / "documents")
    secrets = _secrets()
    settings = OmniSettings(scope="document")
    status, read = "complete", []
    recorder: telemetry.Recorder | None = None
    try:
        with telemetry.Lock(lock or LOCK):
            recorder = telemetry.Recorder(
                out / "calls.jsonl",
                cap=CAP["pilot"],
                inner=inner,
                secrets=secrets,
                pace=pace,
                sleep=sleep,
            )
            recorder.begin("pilot")
            client = httpx.Client(
                transport=recorder.transport, timeout=settings.timeout
            )
            reader = omni.OmniReader(
                client=client, sleep=recorder.sleep, settings=settings
            )
            for path in paths:
                data = path.read_bytes()
                document = Document(
                    id=path.stem, file=path.name, pages=omni._length(data)
                )
                kind = reader.classify(document, data)
                extraction = reader.extract(document, data, "unknown")
                read.append((path, kind, extraction))
    except telemetry.Stop as stop:
        status = f"stopped: {stop.reason}"
    except Exception as error:
        status = f"error: {type(error).__name__}"
    if telemetry.find_secrets(out, secrets):
        status = "stopped: a secret is in a written file"
    lines = (
        telemetry.load(out / "calls.jsonl") if (out / "calls.jsonl").exists() else []
    )
    documents = [
        pilot_checks(path, kind, extraction, lines) for path, kind, extraction in read
    ]
    passed = (
        status == "complete"
        and len(documents) == len(paths)
        and all(c["passed"] for d in documents for c in d["checks"])
    )
    result: Json = {
        "experiment": "issue-126 pilot",
        "mode": mode,
        "approval": authorize,
        "freeze_sha256": _sha(frozen.read_bytes()) if frozen else None,
        "model": os.environ.get("VLM_MODEL"),
        "endpoint": ev._endpoint(os.environ.get("VLM_BASE_URL")),
        "config_id": omni.config_id(settings),
        "planned": PLANNED["pilot"],
        "cap": CAP["pilot"],
        "attempts": recorder.attempts if recorder else 0,
        "status": status,
        "technical_checks_passed": passed,
        "next": "Nothing starts by itself. The main run is another invocation with "
        "its own authorization.",
        "documents": documents,
        "telemetry": telemetry.summary(lines),
    }
    _write(out / "pilot.json", result)
    return result


def pilot_checks(path: Path, kind: str, extraction: Any, lines: list[Json]) -> Json:
    """What the answers for one pilot document show. `checks` are technical and
    decide if the endpoint takes the request of the document scope. `observed` is
    what the model did with it: reported, and no reason to pass or fail."""
    key = _read(path.with_suffix(".expected.json"))
    found = omni.pages(path.read_bytes())
    hashes = [_sha(p.image) for p in found]
    mine = [
        c for c in telemetry.calls(lines) if c["images"] and c["images"][0] == hashes[0]
    ]
    whole = [c for c in mine if c["kind"] == "document" and c["images"] == hashes]
    last = whole[-1]["attempts"][-1] if whole else {}
    usage = last.get("usage") or {}
    expected_tokens = sum(
        math.ceil(p.size[0] / 32) * math.ceil(p.size[1] / 32) for p in found
    )
    blocks: list[Any] = []
    exact = False
    if last.get("status") == 200:
        try:
            content = json.loads(last["body"])["choices"][0]["message"]["content"]
            blocks, exact = omni._blocks(content, len(found))
        except (ValueError, LookupError, TypeError):
            pass
    schema = SCHEMAS.get(DocumentType(key["document_type"]))
    valid = (
        bool(blocks)
        and schema is not None
        and all(omni._read(schema, block) is not None for block in blocks)
    )
    prompt, completion = usage.get("prompt"), usage.get("completion")
    limit = PILOT_TOKEN_SHARE * omni.MAX_TOKENS

    def check(name: str, passed: bool, seen: Any) -> Json:
        return {"check": name, "passed": bool(passed), "seen": seen}

    classify = [c for c in mine if c["kind"] == "classify"]
    checks = [
        check(
            "the classify call got a 200",
            bool(classify) and classify[-1]["attempts"][-1].get("status") == 200,
            [a.get("status") for c in classify for a in c["attempts"]],
        ),
        check(
            "one document call with every page image, in page order",
            len(whole) == 1,
            [(c["kind"], len(c["images"])) for c in mine],
        ),
        check(
            "the document call got a 200", last.get("status") == 200, last.get("status")
        ),
        check(
            "the answer ended by itself (finish reason stop)",
            last.get("finish_reason") == "stop",
            last.get("finish_reason"),
        ),
        check(
            "the answer reports its prompt and completion tokens",
            prompt is not None and completion is not None,
            usage or None,
        ),
        check(
            "the prompt tokens count every image at its full size",
            prompt is not None and prompt >= expected_tokens,
            {"prompt": prompt, "images": expected_tokens},
        ),
        check(
            f"the answer uses at most {PILOT_TOKEN_SHARE:.0%} of max_tokens",
            completion is not None and completion <= limit,
            {"completion": completion, "limit": limit},
        ),
        check(
            "the answer is JSON with exactly one block for each page",
            exact and len(blocks) == len(found),
            exact,
        ),
        check("every block is a valid page answer", valid, valid),
    ]
    present = {f["name"]: f for f in key["fields"] if f["value"] is not None}
    stored = {f.name: f for f in extraction.fields}
    return {
        "file": path.name,
        "pages": len(found),
        "kind": key.get("variant", "text"),
        "checks": checks,
        "observed": {
            "type": kind,
            "type_right": kind == key["document_type"],
            "fields_present_in_the_key": len(present),
            "fields_stored": len(stored),
            "fields_on_an_accepted_page": sum(
                f.source.page in present[n].get("accepted_pages", [present[n]["page"]])
                for n, f in stored.items()
                if n in present
            ),
            "line_items_stored": len(extraction.line_items),
            "cached_prompt_tokens": usage.get("cached"),
            "seconds": last.get("seconds"),
        },
    }


# ------------------------------------------------------------------------ the CLI


def _live_transport() -> httpx.BaseTransport:
    """The only place that can reach the network. `main()` calls it for a live mode
    and only after the preflight found nothing."""
    return httpx.HTTPTransport()


def main(argv: Sequence[str] | None = None) -> int:
    """Exit code 0: done. 2: the preflight refused the run, nothing was sent. 3: the
    run stopped or its files do not give a valid experiment."""
    parser = argparse.ArgumentParser(prog="python -m app.docintel.experiment")
    commands = parser.add_subparsers(dest="command", required=True)
    dry = commands.add_parser("dry-run", help="the plan with a stub: no request")
    dry.add_argument("plan", choices=("main", "pilot"))
    dry.add_argument("--out", type=Path, required=True)
    for name in ("pilot", "main"):
        live = commands.add_parser(name, help=f"LIVE: the {name} run")
        live.add_argument("--out", type=Path, required=True)
        live.add_argument("--freeze", type=Path, required=True)
        live.add_argument("--authorize", required=True)
        live.add_argument("--pace", type=float, default=0.0)
    frozen = commands.add_parser("freeze", help="write the file that a live run needs")
    frozen.add_argument("--out", type=Path, required=True)
    frozen.add_argument("--method", type=Path, required=True)
    for name in ("replay", "verdict"):
        commands.add_parser(name).add_argument("folder", type=Path)
    args = parser.parse_args(argv)

    if args.command == "freeze":
        freeze(args.out, args.method)
        print(f"frozen: {args.out}")
        return 0
    if args.command == "verdict":
        print(acceptance.text(assess(args.folder)))
        return 0
    if args.command == "replay":
        differences = replay(args.folder)
        print("\n".join(differences) or "the saved answers give the saved reports")
        return 3 if differences else 0

    live_mode = args.command in ("pilot", "main")
    mode = args.command if live_mode else args.plan
    problems = preflight(
        mode,
        args.out,
        live=live_mode,
        frozen=getattr(args, "freeze", None),
        authorize=getattr(args, "authorize", ""),
    )
    if problems:
        print("not started:\n- " + "\n- ".join(problems))
        return 2
    label = "live" if live_mode else "dry-run"
    if live_mode:
        return _finish(mode, label, args, _live_transport())
    with _environment():
        stub = None if mode == "pilot" else key_model(REPO / "data" / "mock" / SET)
        return _finish(mode, label, args, stub)


def _finish(
    mode: str, label: str, args: Any, transport: httpx.BaseTransport | None
) -> int:
    authorize: str = getattr(args, "authorize", "")
    frozen: Path | None = getattr(args, "freeze", None)
    pace: float = getattr(args, "pace", 0.0)
    if mode == "pilot":
        result = run_pilot(
            args.out,
            transport,
            mode=label,
            authorize=authorize,
            frozen=frozen,
            pace=pace,
        )
        passed = result["technical_checks_passed"]
        print(
            f"pilot {label}: {result['status']}, {result['attempts']} attempts, "
            f"technical checks {'passed' if passed else 'NOT passed'}. {result['next']}"
        )
        return 0 if passed else 3
    assert transport is not None
    meta = run_main(
        args.out, transport, mode=label, authorize=authorize, frozen=frozen, pace=pace
    )
    verdict = assess(args.out)
    print(
        f"main {label}: {meta['status']}, {meta['attempts']} attempts, "
        f"{meta['cap']} allowed"
    )
    print(acceptance.text(verdict))
    return 0 if verdict["procedure"]["valid"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
