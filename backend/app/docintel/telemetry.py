"""Telemetry for a controlled run of the Omni reader (issue #126).

A live experiment must say exactly what it sent: how many HTTP attempts, which
answers, which tokens. `Recorder` is a transport that wraps the real one. It counts
each attempt BEFORE it goes out, stops at a hard cap, writes one JSON line per
attempt at once, and never keeps a header, an image, a prompt or a secret. `Replay`
serves the saved answers back to the real reader, so a run can be read again offline
and the requests are checked one by one.

Nothing here runs at import, reads the environment or opens a connection: the only
way out is the `inner` transport that the caller gives. A `Stop` is a BaseException
on purpose: the reader catches only `httpx.TransportError` and the harness catches
`Exception`, so a `Stop` goes through both and ends the run.
"""

import base64
import copy
import hashlib
import json
import os
import re
import threading
import time
import warnings
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any

import httpx2 as httpx

from app.docintel import omni

MIN_SECRET = 8  # a shorter text is no credential and would damage every text
COUNTS = {  # the counts that the endpoint reports, and where
    "prompt": ("usage", "prompt_tokens"),
    "completion": ("usage", "completion_tokens"),
    "reasoning": ("usage", "completion_tokens_details", "reasoning_tokens"),
    "cached": ("usage", "prompt_tokens_details", "cached_tokens"),
}
DATA_URL = re.compile(r"data:[^\s\"']+")
LONG_RUN = re.compile(r"[A-Za-z0-9+/=_-]{100,}")  # base64 of an image, a long token
BEARER = re.compile(r"\bbearer\s+\S+", re.IGNORECASE)
NOT_SENT = "not sent: "


class Stop(BaseException):
    """Nothing more may be sent. The reason is `cap`, `endpoint failing`, `lock: ...`,
    `record: ...` or `replay: ...`."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class BrokenLine(UserWarning):
    """The last line of a record file is cut: a process died while it wrote."""


# ------------------------------------------------------------------ the request


def _images(body: dict[str, Any]) -> list[dict[str, Any]]:
    """The `image_url` objects of a request body, in order."""
    try:
        parts = body["messages"][0]["content"]
        found = [p["image_url"] for p in parts if p["type"] == "image_url"]
    except (KeyError, IndexError, TypeError):
        return []
    return [i for i in found if isinstance(i, dict) and isinstance(i.get("url"), str)]


def _image_sha256(url: str) -> str:
    _, _, encoded = url.partition(",")
    try:
        return hashlib.sha256(base64.b64decode(encoded)).hexdigest()
    except ValueError:  # not base64: the hash of the text still tells images apart
        return hashlib.sha256(url.encode()).hexdigest()


def image_hashes(body: dict[str, Any]) -> list[str]:
    """SHA-256 of the bytes of each image of a request, in order."""
    return [_image_sha256(i["url"]) for i in _images(body)]


def request_sha256(body: dict[str, Any]) -> str:
    """SHA-256 of the request body with each image replaced by the hash of its bytes
    (JSON, sorted keys). The same prompt, schema, settings and images give the same
    hash, and no image has to be kept to prove it."""
    copied = copy.deepcopy(body)
    for image in _images(copied):
        image["url"] = "sha256:" + _image_sha256(image["url"])
    return hashlib.sha256(json.dumps(copied, sort_keys=True).encode()).hexdigest()


def call_kind(body: dict[str, Any]) -> str:
    """`classify` (the first text part is the classify prompt), `document` (a page
    mark stands in front of an image: more than one text part), else `page`."""
    try:
        parts = body["messages"][0]["content"]
        texts = [p for p in parts if p["type"] == "text"]
        if texts and texts[0]["text"] == omni.CLASSIFY:
            return "classify"
        return "document" if len(texts) > 1 else "page"
    except (KeyError, IndexError, TypeError):
        return "page"


def _body(request: httpx.Request) -> dict[str, Any]:
    try:
        body = json.loads(request.content)
    except ValueError:
        body = None
    if isinstance(body, dict):
        return body
    return {"not_json": hashlib.sha256(request.content).hexdigest()}


# ------------------------------------------------------------------ the answer


def _dig(answer: Any, path: Sequence[str]) -> Any:
    for key in path:
        if not isinstance(answer, dict):
            return None
        answer = answer.get(key)
    return answer


def _count(n: Any) -> int | None:
    """A token count: an int of 0 or more, not a bool. A reported 0 is a count."""
    return n if isinstance(n, int) and not isinstance(n, bool) and n >= 0 else None


def usage_of(answer: Any) -> dict[str, int | None]:
    """The counts that the endpoint reports for an answer (`prompt`, `completion`,
    `reasoning`, `cached`). A count that is absent or not a whole number of 0 or
    more is None: a missing count is never taken as 0 and never estimated."""
    return {name: _count(_dig(answer, path)) for name, path in COUNTS.items()}


def _finish(answer: Any) -> str | None:
    choices = _dig(answer, ("choices",))
    reason = _dig(choices[0], ("finish_reason",)) if choices else None
    return reason if isinstance(reason, str) and reason else None


def _parse(text: str) -> Any:
    try:
        return json.loads(text)
    except (ValueError, RecursionError):
        return None


# ------------------------------------------------------------------ the secrets


def _usable(secrets: Sequence[str]) -> list[str]:
    return [s for s in secrets if len(s) >= MIN_SECRET]


def _hide(text: str, secrets: Sequence[str]) -> str:
    """The text with each secret and each data URL replaced. A secret is also taken
    out in its JSON form and by its first 8 characters (an error body may echo a cut
    key), longest first."""
    found = {v for s in _usable(secrets) for v in (s, json.dumps(s)[1:-1], s[:8])}
    for secret in sorted(found, key=len, reverse=True):
        text = text.replace(secret, "<redacted>")
    return DATA_URL.sub("<redacted>", text)


def redact(text: str, secrets: Sequence[str], limit: int = 500) -> str:
    """The start of a text that is safe to keep: no secret, no data URL, no Bearer
    token and no long base64 run. The cut comes last, so it cannot leave half a
    secret."""
    text = LONG_RUN.sub("<redacted>", BEARER.sub("<redacted>", _hide(text, secrets)))
    return text[:limit]


def find_secrets(folder: Path, secrets: Sequence[str]) -> list[str]:
    """The files under `folder` (relative paths) that hold one of the secrets. The
    runner scans its folder at the end: a leak that the recorder missed is found
    before anyone reads the files. A secret under 8 characters is ignored."""
    needles = [v.encode() for s in _usable(secrets) for v in {s, json.dumps(s)[1:-1]}]
    found = []
    for path in sorted(folder.rglob("*")):
        if path.is_file() and any(n in path.read_bytes() for n in needles):
            found.append(path.relative_to(folder).as_posix())
    return found


# ------------------------------------------------------------------ the recorder


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Recorder:
    """Wraps the transport of the reader. For each request, in this order:

    1. After a stop, raise `Stop` again: nothing is sent after a stop.
    2. A request is a RETRY of the open call when `sleep` was called since the last
       attempt and the request hash is the same. Else it is a new call. The reader
       sleeps only between two attempts of one call, so this tells a retry from a
       second call with the same hash (the classify that `extract` sends again
       after a failed one: no sleep between them). Not a retry: a new call, which
       is not sent when the last `failing_calls` calls in a row had no 200.
    3. The cap counts every attempt before it goes out. Attempt `cap + 1` is never
       sent; a line "not sent" says so.
    4. With `pace`, the starts of two attempts are `pace` seconds apart or more.
       The wait is not in `seconds` of the attempt.

    The transport does not close `inner`: the caller made it and closes it.
    One reader at a time: the call of a retry is told by order, not by thread.
    """

    def __init__(
        self,
        path: Path,
        *,
        cap: int,
        inner: httpx.BaseTransport,
        secrets: Sequence[str] = (),
        failing_calls: int = 5,
        pace: float = 0.0,
        clock: Callable[[], float] = time.perf_counter,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], str] = _utc_now,
    ) -> None:
        if cap < 0 or failing_calls < 1 or pace < 0:
            raise ValueError("cap and pace are 0 or more, failing_calls is 1 or more")
        if path.exists() and path.stat().st_size:
            raise Stop(f"record: {path.name} is not empty (one file, one experiment)")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        self.path, self.cap, self.inner, self.secrets = path, cap, inner, secrets
        self.failing_calls, self.pace = failing_calls, pace
        self.attempts = 0  # sent so far
        self.stopped: str | None = None
        self.transport: httpx.BaseTransport = _Transport(self)
        self._clock, self._sleep, self._now = clock, sleep, now
        self._lock = threading.Lock()
        self._tag, self._calls = "", 0
        self._open: dict[str, Any] | None = None  # call, attempt, hash, answered
        self._slept, self._streak = False, 0
        self._last_start: float | None = None

    def sleep(self, seconds: float) -> None:
        """For `OmniReader(sleep=...)`: marks that a retry follows, then waits."""
        self._slept = True
        self._sleep(seconds)

    def begin(self, tag: str) -> None:
        """The run that the next attempts belong to. The next request is a new call."""
        self._close_call()
        self._tag, self._slept = tag, False

    def _close_call(self) -> None:
        if self._open is not None:
            self._streak = 0 if self._open["answered"] else self._streak + 1
        self._open = None

    def _write(self, record: dict[str, Any]) -> None:
        """Appended and closed at once, so a hard stop loses no answer."""
        try:
            with self.path.open("a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(record) + "\n")
        except OSError as e:
            self.stopped = f"record: cannot write ({type(e).__name__})"
            raise Stop(self.stopped) from e

    def _record(self, body: dict[str, Any], /, **found: Any) -> dict[str, Any]:
        assert self._open is not None
        return {
            "tag": self._tag,
            "call": self._open["call"],
            "attempt": self._open["attempt"],
            "kind": call_kind(body),
            "request_sha256": self._open["hash"],
            "images": image_hashes(body),
            "started": self._now(),
            "seconds": None,
            "status": None,
            "error": None,
            "retry_after": None,
            "finish_reason": None,
            "usage": usage_of(None),
        } | found

    def _refuse(self, body: dict[str, Any], reason: str) -> Stop:
        self.stopped = reason
        self._write(self._record(body, error=NOT_SENT + reason))
        return Stop(reason)

    def handle(self, request: httpx.Request) -> httpx.Response:
        with self._lock:
            return self._handle(request)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        if self.stopped is not None:
            raise Stop(self.stopped)
        body = _body(request)
        digest = request_sha256(body)
        if self._open and self._slept and self._open["hash"] == digest:
            self._open["attempt"] += 1
        else:
            self._close_call()
            self._calls += 1
            self._open = {
                "call": self._calls,
                "attempt": 1,
                "hash": digest,
                "answered": False,
            }
            if self._streak >= self.failing_calls:
                raise self._refuse(body, "endpoint failing")
        if self.attempts >= self.cap:
            raise self._refuse(body, "cap")
        if self.pace > 0 and self._last_start is not None:
            wait = self._last_start + self.pace - self._clock()
            if wait > 0:
                self._sleep(wait)
        started = self._last_start = self._clock()
        self.attempts += 1  # counted before it goes out: a failed attempt counts
        self._slept = False
        try:
            response = self.inner.handle_request(request)
            response.read()
        except BaseException as e:
            seconds = round(self._clock() - started, 3)
            self._write(self._record(body, seconds=seconds, error=type(e).__name__))
            raise
        seconds = round(self._clock() - started, 3)
        found: dict[str, Any] = {"seconds": seconds, "status": response.status_code}
        header = response.headers.get("retry-after")
        if header is not None:
            found["retry_after"] = redact(header, self.secrets, 100)
        if response.status_code == 200:
            assert self._open is not None
            self._open["answered"] = True
            answer = _parse(response.text)
            found |= {
                "finish_reason": _finish(answer),
                "usage": usage_of(answer),
                "body": _hide(response.text, self.secrets),
            }
        else:
            found["error_body"] = redact(response.text, self.secrets)
        self._write(self._record(body, **found))
        return response


class _Transport(httpx.BaseTransport):
    def __init__(self, recorder: Recorder) -> None:
        self._recorder = recorder

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return self._recorder.handle(request)

    def close(self) -> None:
        """Nothing: `inner` is shared by the clients of many runs."""


# ------------------------------------------------------------------ replay, lock


def _sent(line: dict[str, Any]) -> bool:
    return not str(line.get("error") or "").startswith(NOT_SENT)


def _transport_error(name: str, request: httpx.Request) -> httpx.TransportError:
    found = getattr(httpx, name, None)
    if isinstance(found, type) and issubclass(found, httpx.TransportError):
        return found("replayed: " + name, request=request)
    return httpx.TransportError("replayed: " + name, request=request)


class Replay(httpx.BaseTransport):
    """Serves the saved attempts of one run to the real reader, in order. A request
    that is not the saved one (another hash), or one after the last saved attempt,
    is a `Stop`: the replay is exact or it says where it is not. Lines "not sent"
    are skipped. A 200 gives its saved body; another status gives that status, the
    body `{}` and the saved Retry-After; a saved transport error is raised again as
    the httpx error of that name, so the reader retries as it did."""

    def __init__(self, lines: list[dict[str, Any]]) -> None:
        self._queue = [line for line in lines if _sent(line)]
        self._next = 0

    @property
    def left(self) -> int:
        """Saved attempts not served yet."""
        return len(self._queue) - self._next

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if not self.left:
            raise Stop("replay: a request after the last saved attempt")
        saved = self._queue[self._next]
        if request_sha256(_body(request)) != saved.get("request_sha256"):
            where = f"call {saved.get('call')}, attempt {saved.get('attempt')}"
            raise Stop(f"replay: the request differs from the saved one ({where})")
        self._next += 1
        status = saved.get("status")
        if status is None:
            raise _transport_error(str(saved.get("error")), request)
        if status == 200:
            body = str(saved.get("body", "")).encode()
            return httpx.Response(200, content=body, request=request)
        headers = (
            {"retry-after": saved["retry_after"]} if saved.get("retry_after") else {}
        )
        return httpx.Response(status, json={}, headers=headers, request=request)


class Lock:
    """`with Lock(path):` one live run at a time. The file is made exclusively (it
    holds the process id and the time) and removed at the end. An existing file is
    a `Stop`, and is never taken over: only a person knows that a run is dead."""

    def __init__(self, path: Path) -> None:
        self.path, self._held = path, False

    def __enter__(self) -> "Lock":
        try:
            with self.path.open("x", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps({"pid": os.getpid(), "time": _utc_now()}) + "\n")
        except FileExistsError:
            raise Stop(
                f"lock: {self.path} exists: another run, or one that died"
            ) from None
        self._held = True
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        if self._held:
            self._held = False
            self.path.unlink(missing_ok=True)


# ------------------------------------------------------------------ the record file


def load(path: Path) -> list[dict[str, Any]]:
    """The lines of a record file. A process that is killed in the middle of a write
    leaves a broken LAST line: it is left out and reported with a `BrokenLine`
    warning (a request may be unaccounted for; the runner treats it as an error
    for a verdict). A broken line anywhere else is a damaged file: ValueError."""
    texts = [t for t in path.read_text(encoding="utf-8").split("\n") if t.strip()]
    lines: list[dict[str, Any]] = []
    for number, text in enumerate(texts, 1):
        line = _parse(text)
        if isinstance(line, dict):
            lines.append(line)
        elif number == len(texts):
            warnings.warn(
                f"{path.name}: line {number} is cut and was left out", BrokenLine, 2
            )
        else:
            raise ValueError(f"{path.name}: line {number} is not a record")
    return lines


def calls(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per logical call, in file order: `tag`, `call`, `kind`,
    `request_sha256`, `images` and `attempts`, the SENT lines of the call in order.
    A line "not sent" is in no call: a call that was only asked is not here."""
    found: dict[tuple[Any, Any], dict[str, Any]] = {}
    for line in lines:
        if _sent(line):
            keys = ("tag", "call", "kind", "request_sha256", "images")
            entry = found.setdefault(
                (line["tag"], line["call"]),
                {k: line[k] for k in keys} | {"attempts": []},
            )
            entry["attempts"].append(line)
    return list(found.values())


def _answer(call: dict[str, Any]) -> dict[str, Any] | None:
    """The final 200 line of a call, or None: a 200 always ends a call."""
    last: dict[str, Any] = call["attempts"][-1]
    return last if last["status"] == 200 else None


def _stats(lines: list[dict[str, Any]]) -> dict[str, Any]:
    made = calls(lines)
    sent = [a for c in made for a in c["attempts"]]
    final = [(c["kind"], a) for c in made if (a := _answer(c))]
    errors = Counter(a["error"] for a in sent if a["status"] is None)
    reasons: dict[str, Counter[str]] = {}
    tokens: dict[str, dict[str, int | None]] = {}
    for name in COUNTS:
        got = [_count((a.get("usage") or {}).get(name)) for _, a in final]
        reported = [n for n in got if n is not None]
        tokens[name] = {
            "sum": sum(reported) if reported else None,
            "reported": len(reported),
            "missing": len(got) - len(reported),
        }
    for kind, answer in final:
        reasons.setdefault(kind, Counter())[answer.get("finish_reason") or "none"] += 1
    return {
        "calls": dict(sorted(Counter(c["kind"] for c in made).items())),
        "attempts": len(sent),
        "retries": len(sent) - len(made),
        "by_status": dict(
            sorted(Counter(str(a["status"]) for a in sent if a["status"]).items())
        ),
        "transport_errors": dict(sorted(errors.items())),
        "timeouts": sum(
            n for name, n in errors.items() if str(name).endswith("Timeout")
        ),
        "http_429": sum(1 for a in sent if a["status"] == 429),
        "not_sent": sum(1 for line in lines if not _sent(line)),
        "calls_without_answer": len(made) - len(final),
        "finish_reasons": {
            k: dict(sorted(v.items())) for k, v in sorted(reasons.items())
        },
        "seconds": round(sum(a["seconds"] or 0 for a in sent), 3),
        "tokens": tokens,
    }


def summary(lines: list[dict[str, Any]]) -> dict[str, Any]:
    """The accounting of a record file, and of each tag (`by_tag`). Calls are the
    logical calls that went out at least once; `retries` are attempts beyond the
    first of a call; `calls_without_answer` are calls whose last attempt is not a
    200. `tokens` are the counts that the endpoint reports, over the final 200
    answer of each call: `sum` is None when no answer reports the count, and an
    estimate would have its own name and never enter these sums."""
    tags = sorted({line["tag"] for line in lines})
    by_tag = {t: _stats([x for x in lines if x["tag"] == t]) for t in tags}
    return _stats(lines) | {"by_tag": by_tag}
