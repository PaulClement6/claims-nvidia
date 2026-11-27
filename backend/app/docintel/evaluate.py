"""The evaluation harness (issue #19): a reader on every document of the kit.

    cd backend && uv run python -m app.docintel.evaluate --reader fixture
    cd backend && uv run --env-file ../.env python -m app.docintel.evaluate \
        --reader omni --runs 3

For each document of `data/mock/` (the scenarios, then the hard versions) it calls
`classify` and `extract` through the `Reader` contract, compares each field with the
answer key (`expected.json`, rules in `app/docintel/compare.py`) and times the two
calls with a monotonic clock. Each run gets a new reader, so no cache of one run
helps the next. The report goes to `report_dir()/latest.json` (and a copy with the
date): the page "Metrics" reads it through `GET /evaluation/latest`.

It touches no claim, no store, no upload and no gateway: it measures the reader only.
What counts as right, the pass rate and the line items: `docs/EVALUATION.md`.
"""

import argparse
import hashlib
import inspect
import json
import logging
import os
import re
import statistics
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import httpx2 as httpx
from pydantic_core import to_jsonable_python
from pypdf import PdfReader

from app import demo
from app.docintel.compare import (
    BadAnswerKey,
    Kind,
    Verdict,
    check_expected,
    compare_field,
    compare_lines,
    field_kind,
)
from app.docintel.reader import Reader
from app.fraud.routing import LOW_CONFIDENCE
from app.models import Document, DocumentType, Extraction
from app.store import CLAIM_TYPE

if TYPE_CHECKING:  # omni is imported only when its reader runs
    from app.docintel.omni import OmniSettings

SCHEMA_VERSION = 1
KINDS = ("clean", "scan", "photo")  # the order of the tables
TEXT_CHARS = 300  # an error text or a read value is cut there
KNOWN_TYPES = {t.value for t in DocumentType}
Clock = Callable[[], float]


def report_dir() -> Path:
    """Where the reports go. Relative to `backend/`, like the other files of a run
    (`.dev-data/` is not in git): the CLI and the API read the same folder."""
    return Path(os.environ.get("CLAIMS_EVALUATION", ".dev-data/evaluation"))


# ---------------------------------------------------------------- the kit


@dataclass(frozen=True)
class KitDocument:
    id: str  # "folder/file", as the reader bench and the demo kit name it
    path: Path
    kind: str  # clean, scan or photo
    workflow: str  # the claim type of the scenario: what `extract_document` gives
    key: dict[str, Any] | None  # the answer key; None when it cannot be used
    key_error: str | None


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _check_key(key: Any) -> None:
    """Raises BadAnswerKey when the answer key cannot be used to score a reader."""
    if not isinstance(key, dict):
        raise BadAnswerKey("the answer key is not a JSON object")
    doc_type = key.get("document_type")
    if doc_type not in KNOWN_TYPES:
        raise BadAnswerKey(f"unknown document_type {doc_type!r}")
    fields = key.get("fields")
    if not isinstance(fields, list):
        raise BadAnswerKey("`fields` is not a list")
    seen: set[str] = set()
    for f in fields:
        name = f.get("name") if isinstance(f, dict) else None
        if not isinstance(name, str):
            raise BadAnswerKey("a field has no name")
        if name in seen:
            raise BadAnswerKey(f"the field {name} is there twice")
        seen.add(name)
        kind = field_kind(doc_type, name)
        if kind is None:
            raise BadAnswerKey(f"the field {name} is not in the schema of {doc_type}")
        page = f.get("page")
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            raise BadAnswerKey(f"the field {name} has no page of 1 or more")
        if "value" not in f:
            raise BadAnswerKey(f"the field {name} has no value")
        check_expected(kind, f["value"])
    lines = key.get("line_items", [])
    if not isinstance(lines, list) or not all(isinstance(x, dict) for x in lines):
        raise BadAnswerKey("`line_items` is not a list of objects")
    compare_lines(lines, lines)  # a bad value in a line raises BadAnswerKey


def _answer_key(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    expected = path.with_suffix(".expected.json")
    if not expected.is_file():
        return None, "no expected.json next to the document"
    try:
        key = json.loads(expected.read_text(encoding="utf-8"))
        _check_key(key)
    except (OSError, ValueError, TypeError) as e:  # BadAnswerKey is a ValueError
        return None, f"{type(e).__name__}: {e}"[:TEXT_CHARS]
    return key, None


# The standard kit, and the safety sets of data/mock that are not in it (issue #125)
SETS: tuple[str, ...] = ("kit", *sorted(demo.NOT_KIT))
SET_KINDS = {"text": "clean", "scan": "scan", "photo": "photo"}  # manifest -> kind


def kit(document_set: str = "kit") -> list[KitDocument]:
    """Every document of the kit, in the order of the demo kit (`demo._kit`): the
    reader bench, the contract test and this harness see the same documents. Another
    set of `SETS` (N1, issue #125) is read from its folder and its manifest."""
    if document_set != "kit":
        return _safety_set(document_set)
    mock = demo._data() / "mock"
    index = demo._index()
    policies = {
        p.get("number"): p
        for p in _read_json(mock / "policies.json").get("policies", [])
    }
    scenarios = {s["folder"]: s for s in index.get("scenarios", [])}
    hard = {h["file"]: h for h in index.get("hard", [])}
    out = []
    for (folder, name), path in demo._kit().items():
        doc_id = f"{folder}/{name}"
        variant = hard.get(doc_id)
        key, error = _answer_key(path)
        kind = (variant or {}).get("variant") or (key or {}).get("variant") or "clean"
        source = variant["source_document"].split("/")[0] if variant else folder
        scenario = scenarios.get(source)
        policy = policies.get(scenario["policy"]) if scenario else None
        workflow = CLAIM_TYPE.get(policy["line_of_business"]) if policy else None
        out.append(
            KitDocument(doc_id, path, str(kind), workflow or "unknown", key, error)
        )
    return out


def _safety_set(name: str) -> list[KitDocument]:
    """The documents of a safety set (`data/mock/<name>/`), by file name. Its kind
    comes from its manifest; it has no scenario, so no workflow."""
    if name not in demo.NOT_KIT:
        raise ValueError(f"unknown set {name!r}: use one of {', '.join(SETS)}")
    folder = demo._data() / "mock" / name
    manifest = _read_json(folder / "manifest.json").get("documents", [])
    kinds = {d.get("file"): d.get("kind") for d in manifest if isinstance(d, dict)}
    out = []
    for path in sorted(folder.glob("*")):
        if path.suffix not in demo.KIT_SUFFIXES or not path.is_file():
            continue
        key, error = _answer_key(path)
        kind = SET_KINDS.get(str(kinds.get(path.name)), "clean")
        out.append(
            KitDocument(f"{name}/{path.name}", path, kind, "unknown", key, error)
        )
    return out


# ---------------------------------------------------------------- one document


def _jsonable(value: Any) -> Any:
    """A value of a reader as JSON: dates as ISO text, anything else as text."""
    try:
        out = to_jsonable_python(value, fallback=str)
        json.dumps(out, allow_nan=False)
    except (TypeError, ValueError):
        out = str(value)
    if isinstance(out, str) and len(out) > TEXT_CHARS:
        out = out[:TEXT_CHARS] + "..."
    return out


def _timed(call: Callable[[], Any], clock: Clock) -> tuple[Any, float, str | None]:
    """The result, the seconds, and the error text when the reader raises: one
    document that fails must not stop the run."""
    start = clock()
    try:
        out, error = call(), None
    except Exception as e:
        out, error = None, f"{type(e).__name__}: {e}"[:TEXT_CHARS]
    return out, round(clock() - start, 3), error


def _pages(path: Path, data: bytes) -> int:
    if path.suffix != ".pdf":
        return 1
    try:
        return max(1, len(PdfReader(BytesIO(data)).pages))
    except Exception:  # a broken PDF is still read by the reader: one page
        return 1


def _source(field: Any, document: Document) -> tuple[bool, int | None]:
    """Is the source valid (non-negotiable 3: this document, a page that exists, a
    snippet or a box), and its page."""
    source = getattr(field, "source", None)
    page = getattr(source, "page", None)
    if isinstance(page, bool) or not isinstance(page, int):
        page = None
    valid = (
        source is not None
        and getattr(source, "document_id", None) == document.id
        and page is not None
        and 1 <= page <= document.pages
        and bool(getattr(source, "snippet", None) or getattr(source, "box", None))
    )
    return valid, page


def _confidence(field: Any) -> float | None:
    """The confidence that the reader gave a field, or None when it gave none."""
    c = getattr(field, "confidence", None)
    return float(c) if isinstance(c, int | float) and not isinstance(c, bool) else None


def _score_field(
    kind: Kind,
    expected: dict[str, Any],
    got: list[Any],
    document: Document,
    failed: bool,
) -> dict[str, Any]:
    value, expected_page = expected["value"], expected["page"]
    # a null copy of a field stores nothing: the copies with a value decide (a null
    # first must not hide a wrong value, a null after a right one is not "twice")
    got = [g for g in got if getattr(g, "value", None) is not None] or got
    row: dict[str, Any] = {
        "name": expected["name"],
        "kind": kind,
        "expected": _jsonable(value),
        "read": None,
        "expected_page": expected_page,
        "page": None,
        "page_right": None,
        "source": None,
        "confidence": None,  # issue #125: of the value that the status is about
    }
    if not got:
        verdict = compare_field(kind, value, None)
        if verdict.status == "missing":
            verdict = Verdict("missing", "the reader failed" if failed else "absent")
        return row | {"status": verdict.status, "reason": verdict.reason}
    first = got[0]
    verdict = compare_field(kind, value, getattr(first, "value", None))
    if verdict.status == "right" and any(
        compare_field(kind, value, getattr(g, "value", None)).status != "right"
        for g in got[1:]
    ):
        verdict = Verdict("wrong", "given twice with different values")
    row |= {"status": verdict.status, "reason": verdict.reason}
    if verdict.status == "missing":  # null with a source: still nothing read
        return row
    # A wrong status: the highest confidence of the copies that are not right (a
    # field given twice, right at 0.5 and wrong at 0.9, stored a wrong value at 0.9).
    wrong = [
        _confidence(g)
        for g in got
        if getattr(g, "value", None) is not None
        and compare_field(kind, value, getattr(g, "value", None)).status != "right"
    ]
    known = [c for c in wrong if c is not None]
    row["confidence"] = (
        max(known) if verdict.status == "wrong" and known else _confidence(first)
    )
    valid, page = _source(first, document)
    right_page = valid and page == expected_page
    return row | {
        "read": _jsonable(getattr(first, "value", None)),
        "page": page,
        "page_right": right_page,
        "source": "invalid" if not valid else "right" if right_page else "wrong_page",
    }


def _share(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


def _trusted(x: dict[str, Any]) -> bool:
    return bool(x["confidence"] >= LOW_CONFIDENCE)  # routing: below it, a person


def _safety(
    rows: list[dict[str, Any]], extras: list[dict[str, Any]]
) -> dict[str, int | None]:
    """Issue #125: the values stored at LOW_CONFIDENCE or more, counted per field of
    the answer key (a field given twice is one field) and, for the extra fields, per
    stored value. Every stored value needs its confidence: a value of a report of
    #19 or #20 has none, then None ("not available"), never 0. A field with no
    stored value (a right absence, a missing field) needs none."""
    read = [
        r for r in rows if r["status"] in ("right", "wrong") and r["read"] is not None
    ]
    if any(
        isinstance(x.get("confidence"), bool)
        or not isinstance(x.get("confidence"), int | float)
        for x in read + extras
    ):
        return dict.fromkeys(SAFETY)
    wrong = [r for r in read if r["status"] == "wrong" and _trusted(r)]
    absent = sum(r["expected"] is None for r in wrong)
    return {
        "incorrect_trusted": len(wrong),
        "incorrect_trusted_absent": absent,  # a value where the page has none
        "incorrect_trusted_present": len(wrong) - absent,
        "right_trusted": sum(
            r["status"] == "right" and r["expected"] is not None and _trusted(r)
            for r in read
        ),
        # not in the answer key: nobody can say if it is right
        "extra_trusted": sum(_trusted(x) for x in extras),
    }


def _field_counts(
    rows: list[dict[str, Any]], extras: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    extras = extras or []
    right = sum(r["status"] == "right" for r in rows)
    wrong = sum(r["status"] == "wrong" for r in rows)
    absent = [r for r in rows if r["expected"] is None]  # the page holds no value
    page_right = sum(r["page_right"] is True for r in rows)
    # right and on the expected page; a field expected empty has no page to check
    verified = sum(
        r["status"] == "right" and (r["page_right"] is True or r["expected"] is None)
        for r in rows
    )
    return _with_shares(
        {
            "expected": len(rows),
            "right": right,
            "wrong": wrong,
            "missing": sum(r["status"] == "missing" for r in rows),
            "extra": len(extras),
            # values read: they have a source to check (page_right is not None)
            "read": sum(r["page_right"] is not None for r in rows),
            "page_right": page_right,
            "verified": verified,
            # issue #125: a right absence apart from a right value, and the
            # incorrect values at any confidence, where the page has one or not
            "absent": len(absent),
            "right_absent": sum(r["status"] == "right" for r in absent),
            "right_present": right - sum(r["status"] == "right" for r in absent),
            "incorrect_absent": sum(r["status"] == "wrong" for r in absent),
            "incorrect_present": wrong - sum(r["status"] == "wrong" for r in absent),
        }
        | _safety(rows, extras)
    )


def _with_shares(c: dict[str, Any]) -> dict[str, Any]:
    return c | {
        "accuracy": _share(c["right"], c["expected"]),
        "verified_accuracy": _share(c["verified"], c["expected"]),
        "page_accuracy": _share(c["page_right"], c["read"]),
    }


COUNTS: tuple[str, ...] = ("expected", "right", "wrong", "missing", "extra", "read")
COUNTS += ("page_right", "verified")
COUNTS += ("absent", "right_absent", "right_present")
COUNTS += ("incorrect_absent", "incorrect_present")
# issue #125: None when a part has no confidence (a sum with an unknown is unknown)
SAFETY: tuple[str, ...] = ("incorrect_trusted", "incorrect_trusted_absent")
SAFETY += ("incorrect_trusted_present", "right_trusted", "extra_trusted")


def _sum_counts(many: Iterable[dict[str, Any]]) -> dict[str, Any]:
    parts = list(many)
    total: dict[str, Any] = dict.fromkeys(COUNTS, 0)
    for c in parts:
        for k in COUNTS:
            total[k] += c[k]
    for k in SAFETY:
        values = [c.get(k) for c in parts]
        total[k] = None if None in values else sum(v for v in values if v is not None)
    return _with_shares(total)


def _score(
    reader: Reader, doc: KitDocument, number: int, clock: Clock
) -> dict[str, Any]:
    key = doc.key
    assert key is not None  # documents with no usable key are not scored
    data = doc.path.read_bytes()
    document = Document(
        id=f"D-eval-{number}", file=doc.path.name, pages=_pages(doc.path, data)
    )
    classified, t_classify, classify_error = _timed(
        lambda: reader.classify(document, data), clock
    )
    out, t_extract, extract_error = _timed(
        lambda: reader.extract(document, data, doc.workflow), clock
    )
    if classify_error is None and not isinstance(classified, str):
        classify_error, classified = f"returned {type(classified).__name__}", None
    if extract_error is None and not isinstance(out, Extraction):
        extract_error, out = f"returned {type(out).__name__}", None
    error = "; ".join(
        f"{step}: {e}"
        for step, e in (("classify", classify_error), ("extract", extract_error))
        if e
    )

    by_name: dict[str, list[Any]] = {}
    for f in out.fields if out else []:
        by_name.setdefault(str(getattr(f, "name", "")), []).append(f)
    duplicates = [name for name, found in by_name.items() if len(found) > 1]
    rows = []
    for expected in key["fields"]:
        kind = field_kind(key["document_type"], expected["name"])
        assert kind is not None  # checked with the key
        got = by_name.pop(expected["name"], [])
        rows.append(_score_field(kind, expected, got, document, failed=out is None))
    extra = [
        {
            "name": name,
            "value": _jsonable(getattr(f, "value", None)),
            "page": _source(f, document)[1],
            "confidence": _confidence(f),
        }
        for name, found in by_name.items()
        for f in found
    ]
    counts = _field_counts(rows, extra)
    lines = compare_lines(key.get("line_items", []), out.line_items if out else [])
    return {
        "id": doc.id,
        "folder": doc.path.parent.name,
        "file": doc.path.name,
        "document_type": key["document_type"],
        "kind": doc.kind,
        "workflow": doc.workflow,
        "pages": document.pages,
        "classified": classified,
        "classified_right": classified == key["document_type"],
        "extracted_type": out.document_type.value if out else None,
        "seconds": {
            "classify": t_classify,
            "extract": t_extract,
            "total": round(t_classify + t_extract, 3),
        },
        "error": error or None,
        # every expected field right and on its page, and the reader did not raise
        "passed": not error and counts["verified"] == counts["expected"],
        "fields": rows,
        "counts": counts,
        "extra": extra,
        "duplicates": duplicates,
        "line_items": lines._asdict(),
    }


# ---------------------------------------------------------------- totals


def _timing(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "total": 0.0} | dict.fromkeys(
            ("mean", "median", "min", "max")
        )
    return {
        "count": len(values),
        "total": round(sum(values), 3),
        "mean": round(statistics.fmean(values), 3),
        "median": round(statistics.median(values), 3),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
    }


def _aggregate(docs: list[dict[str, Any]]) -> dict[str, Any]:
    with_lines = [
        d["line_items"]
        for d in docs
        if d["line_items"]["expected"] or d["line_items"]["read"]
    ]
    passed = sum(d["passed"] for d in docs)
    classified = sum(d["classified_right"] for d in docs)
    lines_expected = sum(li["expected"] for li in with_lines)
    lines_right = sum(li["right"] for li in with_lines)
    return {
        "documents": {
            "count": len(docs),
            "passed": passed,
            "pass_rate": _share(passed, len(docs)),
            "errors": sum(d["error"] is not None for d in docs),
            "classified_right": classified,
            # the type that `classify` gives: apart from the fields and the pass rate
            "classification_accuracy": _share(classified, len(docs)),
        },
        "fields": _sum_counts(d["counts"] for d in docs),
        "line_items": {  # apart from the fields and the pass rate
            "documents": len(with_lines),
            "documents_right": sum(li["all_right"] for li in with_lines),
            "expected": lines_expected,
            "read": sum(li["read"] for li in with_lines),
            "right": lines_right,
            "accuracy": _share(lines_right, lines_expected),
        },
        "seconds": _timing([d["seconds"]["total"] for d in docs]),
    }


def _by_field(docs: list[dict[str, Any]]) -> dict[str, Any]:
    rows: dict[str, list[dict[str, Any]]] = {}
    extra: dict[str, list[dict[str, Any]]] = {}
    for d in docs:
        for r in d["fields"]:
            rows.setdefault(r["name"], []).append(r)
        for x in d["extra"]:
            extra.setdefault(x["name"], []).append(x)
    names = list(rows) + [n for n in extra if n not in rows]
    return {n: _field_counts(rows.get(n, []), extra.get(n, [])) for n in names}


def _group(docs: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for d in docs:
        groups.setdefault(d[key], []).append(d)
    return groups


def _documents(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One line per document of the kit, over the runs."""
    out = []
    for doc_id, runs in _group(docs, "id").items():
        errors = [d["error"] for d in runs if d["error"]]
        out.append(
            {
                "id": doc_id,
                "document_type": runs[0]["document_type"],
                "kind": runs[0]["kind"],
                "runs": len(runs),
                "passed_runs": sum(d["passed"] for d in runs),
                "classified_right_runs": sum(d["classified_right"] for d in runs),
                "fields": _sum_counts(d["counts"] for d in runs),
                "seconds": _timing([d["seconds"]["total"] for d in runs]),
                "errors": list(dict.fromkeys(errors)),
            }
        )
    return out


def _stat(values: list[float | None]) -> dict[str, float | None]:
    found = [v for v in values if v is not None]
    if not found:
        return dict.fromkeys(("mean", "min", "max", "spread"))
    return {
        "mean": round(statistics.fmean(found), 4),
        "min": min(found),
        "max": max(found),
        "spread": round(max(found) - min(found), 4),
    }


def _summary(overalls: list[dict[str, Any]]) -> dict[str, Any]:
    """Mean, lowest and highest over the runs (docs/NIKOLA.md, section 7)."""
    pick: dict[str, Callable[[dict[str, Any]], float | None]] = {
        "accuracy": lambda o: o["fields"]["accuracy"],
        "verified_accuracy": lambda o: o["fields"]["verified_accuracy"],
        "page_accuracy": lambda o: o["fields"]["page_accuracy"],
        "pass_rate": lambda o: o["documents"]["pass_rate"],
        "classification_accuracy": lambda o: o["documents"]["classification_accuracy"],
        "line_item_accuracy": lambda o: o["line_items"]["accuracy"],
        "seconds_per_document": lambda o: o["seconds"]["mean"],
        "wrong": lambda o: o["fields"]["wrong"],
        "missing": lambda o: o["fields"]["missing"],
        # issue #125: None for a run with no confidence (not counted in the mean)
        "incorrect_trusted": lambda o: o["fields"]["incorrect_trusted"],
        "incorrect_trusted_absent": lambda o: o["fields"]["incorrect_trusted_absent"],
        "incorrect_trusted_present": lambda o: o["fields"]["incorrect_trusted_present"],
        "right_trusted": lambda o: o["fields"]["right_trusted"],
        "errors": lambda o: o["documents"]["errors"],
    }
    out = {name: _stat([get(o) for o in overalls]) for name, get in pick.items()}
    for name in SAFETY_SUMMARY:  # a run with no confidence: no mean, not a mean of 0
        if any(pick[name](o) is None for o in overalls):
            out[name] = _stat([])
    return out


SAFETY_SUMMARY: tuple[str, ...] = ("incorrect_trusted", "incorrect_trusted_absent")
SAFETY_SUMMARY += ("incorrect_trusted_present", "right_trusted")


# ---------------------------------------------------------------- HTTP


class HttpMeter:
    """Counts the HTTP calls of a reader, by status, and notes the model settings
    of each request body. It keeps no header (the key is in a header), no image and
    no answer. Status codes are what the server answered: a 429 retried by the
    reader is counted too."""

    BODY_KEYS = ("model", "temperature", "top_p", "max_tokens", "reasoning_budget")

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport
        self._clients: list[httpx.Client] = []
        self.observed: dict[str, set[Any]] = {}
        self.reset()

    def reset(self) -> None:
        self.requests = 0
        self.by_status: dict[str, int] = {}
        # issue #20: the cost of a setting (tokens) and why the answers ended.
        # `no_usage`: answers (200) with no prompt and completion counts, which add
        # nothing; `no_reasoning`: answers with no reasoning count, so `reasoning`
        # is the sum over the other answers only. A reported 0 is a count.
        self.tokens = dict.fromkeys(
            ("prompt", "completion", "reasoning", "no_usage", "no_reasoning"), 0
        )
        self.finish_reasons: dict[str, int] = {}

    def client(self, timeout: float) -> httpx.Client:
        c = httpx.Client(
            timeout=timeout,
            transport=self._transport,
            event_hooks={"request": [self._request], "response": [self._response]},
        )
        self._clients.append(c)
        return c

    def _request(self, request: httpx.Request) -> None:
        self.requests += 1
        try:
            body = json.loads(request.content)
        except ValueError:
            return
        if not isinstance(body, dict):
            return
        seen = {k: body[k] for k in self.BODY_KEYS if k in body}
        template = body.get("chat_template_kwargs")
        if isinstance(template, dict) and "enable_thinking" in template:
            seen["enable_thinking"] = template["enable_thinking"]
        form = body.get("response_format")
        if isinstance(form, dict) and "type" in form:
            seen["response_format"] = form["type"]
        for k, v in seen.items():
            if isinstance(v, str | int | float | bool):
                self.observed.setdefault(k, set()).add(v)

    def _response(self, response: httpx.Response) -> None:
        code = str(response.status_code)
        self.by_status[code] = self.by_status.get(code, 0) + 1
        if response.status_code == 200:
            self._answer(response)

    def _answer(self, response: httpx.Response) -> None:
        """Adds the token counts and the finish reason of a 200 answer. Only these
        numbers and words are kept: never the content of the answer."""
        try:
            response.read()
            body = response.json()
        except ValueError:
            body = None
        body = body if isinstance(body, dict) else {}
        usage = body.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        details = usage.get("completion_tokens_details")
        details = details if isinstance(details, dict) else {}
        prompt = _count(usage.get("prompt_tokens"))
        completion = _count(usage.get("completion_tokens"))
        reasoning = _count(details.get("reasoning_tokens"))
        if prompt is not None and completion is not None:
            self.tokens["prompt"] += prompt
            self.tokens["completion"] += completion
            if reasoning is not None:
                self.tokens["reasoning"] += reasoning
            else:  # not reported: never taken as 0
                self.tokens["no_reasoning"] += 1
        else:
            self.tokens["no_usage"] += 1
            self.tokens["no_reasoning"] += 1
        choices = body.get("choices")
        first = choices[0] if isinstance(choices, list) and choices else None
        reason = first.get("finish_reason") if isinstance(first, dict) else None
        reason = reason if isinstance(reason, str) and reason else "none"
        self.finish_reasons[reason] = self.finish_reasons.get(reason, 0) + 1

    def snapshot(self) -> dict[str, Any]:
        answered = sum(self.by_status.values())
        return {
            "requests": self.requests,
            "by_status": dict(sorted(self.by_status.items())),
            "transport_errors": self.requests - answered,  # timeouts, refused, ...
            "http_429": self.by_status.get("429", 0),
            "tokens": dict(self.tokens),
            "finish_reasons": dict(sorted(self.finish_reasons.items())),
        }

    def settings(self) -> dict[str, list[Any]]:
        return {k: sorted(v, key=str) for k, v in sorted(self.observed.items())}

    def close(self) -> None:
        for c in self._clients:
            c.close()


def _count(n: Any) -> int | None:
    """A token count of an answer: an int of 0 or more (not a bool), else None."""
    return n if isinstance(n, int) and not isinstance(n, bool) and n >= 0 else None


def _add_http(many: list[dict[str, Any] | None]) -> dict[str, Any] | None:
    found = [h for h in many if h is not None]
    if not found:
        return None
    by_status: dict[str, int] = {}
    for h in found:
        for code, n in h["by_status"].items():
            by_status[code] = by_status.get(code, 0) + n
    total = {
        "requests": sum(h["requests"] for h in found),
        "by_status": dict(sorted(by_status.items())),
        "transport_errors": sum(h["transport_errors"] for h in found),
        "http_429": sum(h["http_429"] for h in found),
    }
    # a report of #19 has no tokens: a total is given only when every run has one
    if all("tokens" in h and "finish_reasons" in h for h in found):
        total["tokens"] = {
            k: sum(h["tokens"].get(k, 0) for h in found) for k in found[0]["tokens"]
        }
        reasons: dict[str, int] = {}
        for h in found:
            for reason, n in h["finish_reasons"].items():
                reasons[reason] = reasons.get(reason, 0) + n
        total["finish_reasons"] = dict(sorted(reasons.items()))
    return total


# ---------------------------------------------------------------- the readers

ReaderSetup = tuple[Callable[[], Reader], dict[str, Any], HttpMeter | None]


def _fixture() -> ReaderSetup:
    from app.docintel.fixture import FixtureReader

    return FixtureReader, {"reader": "fixture"}, None


def _endpoint(url: str | None) -> str | None:
    """The model endpoint with no user, password or query: only where it goes."""
    if not url:
        return None
    parts = urlsplit(url)
    host = parts.netloc.rpartition("@")[2]  # the port stays, a user does not
    return f"{parts.scheme}://{host}{parts.path}"


def _omni(
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
    options: "OmniSettings | None" = None,
) -> ReaderSetup:
    from app.docintel import omni

    meter = HttpMeter(transport)
    o = options or omni.ISSUE_18

    def make() -> Reader:
        client = meter.client(o.timeout)
        return omni.OmniReader(client=client, sleep=sleep, settings=o)

    settings = {
        "reader": "omni",
        "model": os.environ.get("VLM_MODEL"),
        "endpoint": _endpoint(os.environ.get("VLM_BASE_URL")),
        "dpi": o.dpi,
        "max_tokens": o.max_tokens,
        "max_pages": omni.MAX_PAGES,
        "max_pixels": omni.MAX_PIXELS,
        "timeout_seconds": o.timeout,
        "retries": omni.RETRIES,
        # issue #20: the settings that a run can change, and the name of the whole.
        # `--combine` compares `settings`, so it never mixes two configurations.
        "temperature": o.temperature,
        "thinking": o.thinking,
        "reasoning_budget": o.reasoning_budget,
        "prompt": o.prompt,
        "prompt_sha256": omni.prompt_sha256(o.prompt),
        "config_id": omni.config_id(o),
    }
    return make, settings, meter


def _on_off(text: str) -> bool:
    words = {"on": True, "true": True, "off": False, "false": False}
    if text.casefold() not in words:
        raise ValueError(f"{text!r} is not on or off")
    return words[text.casefold()]


def _int_or_none(text: str) -> int | None:
    return None if text.casefold() == "none" else int(text)


# `--omni KEY=VALUE` (issue #20): one entry per field of `OmniSettings`
OPTIONS: dict[str, Callable[[str], Any]] = {
    "dpi": int,
    "temperature": float,
    "max_tokens": int,
    "thinking": _on_off,
    "reasoning_budget": _int_or_none,
    "timeout": float,
    "prompt": str,
}


def omni_options(pairs: list[str]) -> "OmniSettings":
    """The settings of the Omni reader from `KEY=VALUE` texts; the other settings
    stay those of #18. Raises ValueError on an unknown, repeated or bad value."""
    from app.docintel.omni import OmniSettings

    values: dict[str, Any] = {}
    for pair in pairs:
        key, sep, text = pair.partition("=")
        if not sep or key not in OPTIONS:
            raise ValueError(
                f"--omni {pair}: use KEY=VALUE with KEY in {sorted(OPTIONS)}"
            )
        if key in values:
            raise ValueError(f"--omni {key} is given twice")
        try:
            values[key] = OPTIONS[key](text)
        except ValueError as e:
            raise ValueError(f"--omni {pair}: {e}") from None
    return OmniSettings(**values)


READERS: dict[str, Callable[[], ReaderSetup]] = {"fixture": _fixture, "omni": _omni}


# ---------------------------------------------------------------- the report


class ReaderNotStarted(RuntimeError):
    """The reader could not be built (no key, a broken answer key for the fixture)."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def evaluate(
    make_reader: Callable[[], Reader],
    runs: int = 1,
    *,
    settings: dict[str, Any] | None = None,
    meter: HttpMeter | None = None,
    clock: Clock = time.perf_counter,
    command: str = "",
    document_set: str = "kit",
) -> dict[str, Any]:
    """Runs a new reader `runs` times on the kit (or on another set of `SETS`) and
    returns the report (schema version 1, `docs/EVALUATION.md`). A document with no
    usable answer key is not scored: it is listed in `answer_key_errors` and the
    report is not complete."""
    if runs < 1:
        raise ValueError("runs must be 1 or more")
    documents = kit(document_set)
    scored = [d for d in documents if d.key is not None]
    details: list[dict[str, Any]] = []
    name, code = "", None
    try:
        for run in range(1, runs + 1):
            if meter is not None:
                meter.reset()
            try:
                reader = make_reader()
            except Exception as e:  # no reader, no measure: no report at all
                raise ReaderNotStarted(f"{type(e).__name__}: {e}"[:TEXT_CHARS]) from e
            name, code = reader.name, _code_fingerprint(reader)
            started, start = _now(), clock()
            results = [_score(reader, d, i, clock) for i, d in enumerate(scored, 1)]
            details.append(
                {
                    "run": run,
                    "run_id": uuid.uuid4().hex,  # one run is never counted twice
                    "started_at": started,
                    "seconds": round(clock() - start, 3),
                    "http": meter.snapshot() if meter is not None else None,
                    "documents": results,
                }
            )
    finally:
        if meter is not None:
            meter.close()
    kit_kinds: dict[str, int] = {}
    for d in documents:
        kit_kinds[d.kind] = kit_kinds.get(d.kind, 0) + 1
    # what the configuration says, and apart, what the request bodies really held
    sent = {"sent": meter.settings()} if meter is not None else {}
    return _report(
        reader=name,
        command=command,
        settings=(settings or {"reader": name}) | sent,
        code=code,
        kit_info={
            "documents": len(documents),
            "by_kind": {k: kit_kinds[k] for k in sorted(kit_kinds, key=_kind_order)},
            "expected_fields": sum(len(d.key["fields"]) for d in scored if d.key),
            "expected_line_items": sum(
                len(d.key.get("line_items", [])) for d in scored if d.key
            ),
        }
        # only another set is named: a report of the kit stays as #19 wrote it, so
        # old and new reports of the kit still combine
        | ({"set": document_set} if document_set != "kit" else {}),
        key_errors=[
            {"document": d.id, "error": d.key_error} for d in documents if d.key is None
        ],
        details=details,
    )


def _code_fingerprint(reader: Reader) -> str | None:
    """SHA-256 of the source file of the reader class: its prompt, its settings and
    its code. Line ends do not count (a Windows checkout has CRLF)."""
    try:
        source = inspect.getsourcefile(type(reader))
    except TypeError:  # a class with no Python source
        return None
    if not source:
        return None
    data = Path(source).read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def _rescored(doc: dict[str, Any]) -> dict[str, Any]:
    """The counts and the pass of one document, made again from its field rows: a
    total never comes from a number that a file says."""
    counts = _field_counts(doc["fields"], doc["extra"])
    passed = not doc["error"] and counts["verified"] == counts["expected"]
    return doc | {"counts": counts, "passed": passed}


def _report(
    *,
    reader: str,
    command: str,
    settings: dict[str, Any],
    code: str | None,
    kit_info: dict[str, Any],
    key_errors: list[dict[str, Any]],
    details: list[dict[str, Any]],
    more: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The report from the results of each document of each run. Every total is
    made here, from those results: a new run and a combination of saved runs give
    the same numbers for the same results. The runs are numbered 1 to N."""
    details = [
        {"run": i, **{k: v for k, v in r.items() if k not in ("run", "overall")}}
        | {"documents": (docs := [_rescored(d) for d in r["documents"]])}
        | {"overall": _aggregate(docs)}
        for i, r in enumerate(details, 1)
    ]
    every = [d for r in details for d in r["documents"]]
    by_type = {
        t: _aggregate(docs) | {"by_field": _by_field(docs)}
        for t, docs in _group(every, "document_type").items()
    }
    kinds = _group(every, "kind")
    order = [k for k in KINDS if k in kinds] + [k for k in kinds if k not in KINDS]
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": _now(),
        "reader": reader,
        "command": command,
        "settings": settings,
        "reader_code_sha256": code,
        "runs": len(details),
        "complete": not key_errors,
        "kit": kit_info,
        "answer_key_errors": key_errors,
        "summary": _summary([r["overall"] for r in details]),
        # issue #125: the limit of routing, and whether every run has a confidence
        "safety": {
            "threshold": LOW_CONFIDENCE,
            "available": all(
                r["overall"]["fields"]["incorrect_trusted"] is not None for r in details
            ),
        },
        "pooled": _aggregate(every)
        | {
            "by_type": by_type,
            "by_kind": {k: _aggregate(kinds[k]) for k in order},
            "by_field": _by_field(every),
            "per_document": _documents(every),
        },
        "http": _add_http([r["http"] for r in details]),
        "run_details": details,
    } | (more or {})


# ---------------------------------------------------------------- combine saved runs


class BadCombine(ValueError):
    """Saved reports that cannot be combined: the reason names the file."""


TOP_KEYS: tuple[str, ...] = (
    "reader",
    "settings",
    "runs",
    "complete",
    "kit",
    "answer_key_errors",
)
TOP_KEYS += ("run_details",)
DOC_KEYS: tuple[str, ...] = (
    "id",
    "document_type",
    "kind",
    "workflow",
    "pages",
    "classified_right",
)
DOC_KEYS += ("seconds", "error", "passed", "fields", "counts", "extra", "line_items")
# the totals of a run that its documents must give again (raw counts, no share)
TOTALS = {
    "fields": COUNTS + SAFETY,
    "documents": ("count", "passed", "errors", "classified_right"),
    "line_items": ("documents", "documents_right", "expected", "read", "right"),
}


def _load_saved(path: Path) -> tuple[dict[str, Any], str]:
    """A saved report, checked on its own: its form, and each total made again
    from its documents. Returns the report and the SHA-256 of the file."""
    name = path.name
    try:
        raw = path.read_bytes()
    except OSError as e:
        raise BadCombine(f"{name}: cannot be read ({type(e).__name__})") from None
    try:
        report = json.loads(raw.decode("utf-8"))
    except ValueError as e:
        raise BadCombine(f"{name}: not JSON ({e})"[:TEXT_CHARS]) from None
    if not isinstance(report, dict) or report.get("schema_version") != SCHEMA_VERSION:
        raise BadCombine(f"{name}: not an evaluation report of schema version 1")
    for key in TOP_KEYS:
        if key not in report:
            raise BadCombine(f"{name}: `{key}` is missing")
    runs = report["run_details"]
    if not isinstance(runs, list) or not runs:
        raise BadCombine(f"{name}: it holds no run")
    if report["runs"] != len(runs):
        plural = "run" if len(runs) == 1 else "runs"
        raise BadCombine(
            f"{name}: `runs` is {report['runs']} but it holds {len(runs)} {plural}"
        )
    if [r.get("run") if isinstance(r, dict) else None for r in runs] != list(
        range(1, len(runs) + 1)
    ):
        raise BadCombine(f"{name}: the runs are not numbered 1 to {len(runs)}")
    for r in runs:
        where = f"{name}, run {r['run']}"
        docs = r.get("documents")
        try:
            if not isinstance(docs, list) or not docs:
                raise KeyError("documents")
            for d in docs:
                missing = [k for k in DOC_KEYS if k not in d]
                if missing:
                    raise KeyError(", ".join(missing))
                again = _rescored(d)
                if again["passed"] != d["passed"] or any(
                    k in d["counts"] and again["counts"][k] != d["counts"][k]
                    for k in COUNTS + SAFETY
                ):
                    raise BadCombine(
                        f"{where}: {d['id']}: the counts do not match its fields"
                    )
            totals = _aggregate([_rescored(d) for d in docs])
            stored = r.get("overall", {})
        except (KeyError, TypeError, AttributeError) as e:
            raise BadCombine(f"{where}: a document is malformed ({e})") from None
        for part, keys in TOTALS.items():
            for k in keys:
                if k in stored.get(part, {}) and stored[part][k] != totals[part][k]:
                    raise BadCombine(f"{where}: the totals do not match its documents")
    return report, hashlib.sha256(raw).hexdigest()


def _documents_signature(run: dict[str, Any]) -> list[Any]:
    """What makes two runs comparable: the same documents, kinds, workflows and
    pages, and the same answer keys (every expected value and page)."""
    return [
        (
            d["id"],
            d["document_type"],
            d["kind"],
            d["workflow"],
            d["pages"],
            d["line_items"]["expected"],
            [
                (f["name"], f["kind"], json.dumps(f["expected"]), f["expected_page"])
                for f in d["fields"]
            ],
        )
        for d in run["documents"]
    ]


def _run_identity(run: dict[str, Any]) -> str:
    """The id of a run; a report written before the id existed: its content."""
    if isinstance(run.get("run_id"), str):
        return str(run["run_id"])
    content = json.dumps([run.get("started_at"), run["documents"]], sort_keys=True)
    return hashlib.sha256(content.encode()).hexdigest()


def combine(paths: list[Path], command: str = "") -> dict[str, Any]:
    """One report from the runs of saved reports, in the order of the files. No
    reader runs. Raises BadCombine (and writes nothing) when a file cannot be used,
    or when the reports were not made in the same conditions: the same reader,
    settings and reader code, the same kit, documents and answer keys."""
    if len(paths) < 2:
        raise BadCombine("give two reports or more to combine")
    seen_paths: set[Path] = set()
    for p in paths:
        if p.resolve() in seen_paths:
            raise BadCombine(f"{p.name} is given twice")
        seen_paths.add(p.resolve())
    loaded = [(p.name, *_load_saved(p)) for p in paths]
    by_hash: dict[str, str] = {}
    for name, _, digest in loaded:
        if digest in by_hash:
            raise BadCombine(f"{by_hash[digest]} and {name} hold the same report")
        by_hash[digest] = name

    first_name, first, _ = loaded[0]
    signature = _documents_signature(first["run_details"][0])
    codes = [(n, r.get("reader_code_sha256")) for n, r, _ in loaded]
    known = [(n, c) for n, c in codes if c is not None]
    runs_seen: dict[str, str] = {}
    for name, report, _ in loaded:
        if report["reader"] != first["reader"]:
            raise BadCombine(
                f"{name}: the reader is {report['reader']}, not {first['reader']}"
            )
        if report["settings"] != first["settings"]:
            raise BadCombine(f"{name}: the settings differ from {first_name}")
        code = report.get("reader_code_sha256")
        if code is not None and code != known[0][1]:
            raise BadCombine(
                f"{name}: the code of the reader differs from {known[0][0]}"
            )
        if any(report[k] != first[k] for k in ("kit", "complete", "answer_key_errors")):
            raise BadCombine(f"{name}: the kit differs from {first_name}")
        for r in report["run_details"]:
            where = f"run {r['run']} of {name}"
            if _documents_signature(r) != signature:
                raise BadCombine(
                    f"{name}, run {r['run']}: the documents or their answer keys "
                    f"differ from {first_name}, run 1"
                )
            identity = _run_identity(r)
            if identity in runs_seen:
                raise BadCombine(f"{runs_seen[identity]} is also {where}")
            runs_seen[identity] = where

    notes = [
        f"{name} has no fingerprint of the reader code (it was written before the "
        "harness recorded it): its prompt and code are checked by its settings only."
        for name, report, _ in loaded
        if report.get("reader_code_sha256") is None
    ]
    details = [
        r | {"source": {"report": name, "run": r["run"]}}
        for name, report, _ in loaded
        for r in report["run_details"]
    ]
    combined_from = [
        {
            "file": name,
            "sha256": digest,
            "created_at": report.get("created_at"),
            "runs": report["runs"],
            "reader_code_sha256": report.get("reader_code_sha256"),
        }
        for name, report, digest in loaded
    ]
    return _report(
        reader=first["reader"],
        command=command,
        settings=first["settings"],
        code=known[0][1] if known and not notes else None,
        kit_info=first["kit"],
        key_errors=first["answer_key_errors"],
        details=details,
        more={"combined_from": combined_from, "combine_notes": notes},
    )


def _kind_order(kind: str) -> tuple[int, str]:
    return (KINDS.index(kind) if kind in KINDS else len(KINDS), kind)


def _write_atomic(path: Path, text: str) -> None:
    """A reader of the file sees the old report or the new one, never half of one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def dump(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def report_paths(report: dict[str, Any], folder: Path | None = None) -> list[Path]:
    """Where `save_report` writes: a copy named with the reader and the time, then
    `latest.json`. A safety set (N1) has its own: `latest-n1.json`, so `latest.json`
    stays the report of the kit that the page "Business numbers" shows."""
    folder = folder or report_dir()
    stamp = re.sub(r"[^0-9TZ]", "", report["created_at"])
    reader = re.sub(r"[^a-z0-9_-]", "-", str(report["reader"]).lower()) or "reader"
    name = report["kit"].get("set")
    if isinstance(name, str) and name in SETS and name != "kit":
        return [
            folder / f"evaluation-{reader}-{name}-{stamp}.json",
            folder / f"latest-{name}.json",
        ]
    return [folder / f"evaluation-{reader}-{stamp}.json", folder / "latest.json"]


def save_report(report: dict[str, Any], folder: Path | None = None) -> Path:
    """Writes `latest.json` and a copy named with the reader and the time. Returns
    the path of the copy."""
    copy, latest = report_paths(report, folder)
    text = dump(report)
    _write_atomic(copy, text)
    _write_atomic(latest, text)
    return copy


def load_latest(folder: Path | None = None) -> dict[str, Any]:
    """The last report. FileNotFoundError when there is none; ValueError when the
    file is not a report of this schema version."""
    path = (folder or report_dir()) / "latest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("not an evaluation report of schema version 1")
    return data


# ---------------------------------------------------------------- the command


def _percent(share: float | None) -> str:
    return "-" if share is None else f"{share * 100:.1f}%"


def _seconds(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def _row(label: str, a: dict[str, Any]) -> str:
    f, d = a["fields"], a["documents"]
    cells = [
        label,
        d["count"],
        f["right"],
        f["wrong"],
        f["missing"],
        _percent(f["accuracy"]),
        _percent(f["page_accuracy"]),
        f"{d['passed']} ({_percent(d['pass_rate'])})",
        _seconds(a["seconds"]["mean"]),
    ]
    return "| " + " | ".join(str(c) for c in cells) + " |"


HEAD = ["", "Documents", "Right", "Wrong", "Missing", "Accuracy", "Page right"]
HEAD += ["All right", "s/doc"]


def _spread(stat: dict[str, Any]) -> str:
    return (
        f"{_percent(stat['mean'])} "
        f"(runs {_percent(stat['min'])} to {_percent(stat['max'])})"
    )


def _safety_line(report: dict[str, Any]) -> str:
    """Issue #125: the incorrect values that no person would check."""
    f = report["pooled"]["fields"]
    if f["incorrect_trusted"] is None:
        return (
            "- Safety: not available (a run of this report has no confidence per "
            f"field); incorrect at any confidence: {f['wrong']}"
        )
    t, s = LOW_CONFIDENCE, report["summary"]["incorrect_trusted"]
    return (
        f"- Safety (per field of the answer key; extra fields per value): "
        f"incorrect values at confidence >= {t}: {f['incorrect_trusted']} "
        f"(runs {s['min']} to {s['max']}; {f['incorrect_trusted_absent']} where the "
        f"page holds no value, {f['incorrect_trusted_present']} wrong values); "
        f"incorrect at any confidence: {f['wrong']} ({f['incorrect_absent']} + "
        f"{f['incorrect_present']}); right values at >= {t}: {f['right_trusted']} of "
        f"{f['right_present']}; right absences: {f['right_absent']} of {f['absent']}; "
        f"extra fields at >= {t} (not in the answer key): {f['extra_trusted']}"
    )


def summary_text(report: dict[str, Any]) -> str:
    """A markdown summary for the terminal and the pull request. The counts are
    added over the runs."""
    pooled, s = report["pooled"], report["summary"]
    kit_line = ", ".join(f"{k} {n}" for k, n in report["kit"]["by_kind"].items())
    f, d, li = pooled["fields"], pooled["documents"], pooled["line_items"]
    sec = s["seconds_per_document"]
    lines = [
        f"Reader {report['reader']}; {report['runs']} run(s); "
        f"{report['kit']['documents']} documents ({kit_line}); "
        f"{report['kit']['expected_fields']} expected fields per run",
        "Mean over the runs (lowest to highest); counts added over the runs:",
        f"- Field accuracy: {_spread(s['accuracy'])}; {f['right']} right, "
        f"{f['wrong']} wrong, {f['missing']} missing of {f['expected']}",
        f"- Strict document pass rate (every field right and on its page): "
        f"{_spread(s['pass_rate'])}; {d['passed']} of {d['count']}",
        f"- Source page right (of the values read): {_spread(s['page_accuracy'])}; "
        f"{f['page_right']} of {f['read']}",
        f"- Classification (type right, apart): "
        f"{_spread(s['classification_accuracy'])}; "
        f"{d['classified_right']} of {d['count']}",
        f"- Line items (by position, apart): {_spread(s['line_item_accuracy'])}; "
        f"{li['right']} of {li['expected']} lines",
        f"- Seconds per document: {_seconds(sec['mean'])} "
        f"(runs {_seconds(sec['min'])} to {_seconds(sec['max'])})",
        _safety_line(report),
        "",
        "| " + " | ".join(HEAD) + " |",
        "|" + "---|" * len(HEAD),
        _row("**All**", pooled),
        *(_row(f"kind: {k}", a) for k, a in pooled["by_kind"].items()),
        *(_row(t, a) for t, a in pooled["by_type"].items()),
    ]
    lines.append("")
    if report.get("combined_from"):
        files = ", ".join(x["file"] for x in report["combined_from"])
        lines.append(f"Combined from {files}.")
        lines += [f"Note: {n}" for n in report.get("combine_notes", [])]
    if report["http"] is not None:
        h = report["http"]
        lines.append(
            f"HTTP: {h['requests']} requests, by status {h['by_status']}, "
            f"{h['transport_errors']} transport errors, {h['http_429']} answers 429."
        )
    for e in report["answer_key_errors"]:
        lines.append(f"Answer key not used: {e['document']}: {e['error']}")
    return "\n".join(lines)


def _positive(text: str) -> int:
    n = int(text)
    if n < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m app.docintel.evaluate",
        description="Run a reader on every document of the kit and write a report, "
        "or combine the runs of saved reports (no reader runs then).",
    )
    what = ap.add_mutually_exclusive_group(required=True)
    what.add_argument("--reader", choices=sorted(READERS))
    what.add_argument(
        "--combine", nargs="+", type=Path, metavar="FILE", help="saved reports"
    )
    ap.add_argument("--runs", type=_positive, help="new reader per run (default 1)")
    ap.add_argument(
        "--set",
        choices=SETS,
        dest="document_set",
        help="the documents: the kit (default) or a safety set (n1, issue #125)",
    )
    ap.add_argument("--output", type=Path, help="also write the report to this file")
    ap.add_argument(
        "--omni",
        action="append",
        metavar="KEY=VALUE",
        help="a setting of the Omni reader (issue #20), for example dpi=100 or "
        f"prompt=described; keys: {', '.join(OPTIONS)}. The others stay those of #18",
    )
    args = ap.parse_args(argv)
    if args.combine and args.runs is not None:
        ap.error("--runs does not go with --combine: the runs are in the files")
    if args.combine and args.document_set is not None:
        ap.error("--set does not go with --combine: the set is in the files")
    if args.omni and args.reader != "omni":
        ap.error("--omni goes with --reader omni only")
    # pypdf only counts the pages here; its warnings on a PDF it repairs (the
    # line ends of a Windows checkout) are not a result of the reader
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    command = "uv run python -m app.docintel.evaluate " + " ".join(
        sys.argv[1:] if argv is None else argv
    )
    try:
        if args.combine:
            report = combine(args.combine, command)
        else:
            if args.omni:
                make, settings, meter = _omni(options=omni_options(args.omni))
            else:
                make, settings, meter = READERS[args.reader]()
            report = evaluate(
                make,
                args.runs or 1,
                settings=settings,
                meter=meter,
                command=command,
                document_set=args.document_set or "kit",
            )
    # BadCombine: saved reports that cannot be combined; RuntimeError: e.g.
    # OmniReader with no NVIDIA_KEY; OSError, ValueError: a kit folder or an
    # index.json that cannot be read. No report then.
    except (RuntimeError, OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    inputs = {p.resolve() for p in args.combine or []}
    targets = report_paths(report) + ([args.output] if args.output else [])
    for target in targets:
        if target.resolve() in inputs:  # a saved result is never written over
            print(f"error: {target} is an input: nothing written", file=sys.stderr)
            return 2
    copy = save_report(report)
    if args.output:
        try:
            _write_atomic(args.output, dump(report))
        except OSError as e:
            print(
                f"error: the copy {args.output} was not written: {e}", file=sys.stderr
            )
            return 2
    print(summary_text(report))
    print(f"\nReport: {report_paths(report)[1]} (copy: {copy})")
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
