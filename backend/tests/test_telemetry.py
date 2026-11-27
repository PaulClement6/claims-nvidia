"""The telemetry of a controlled run (issue #126): attempts, cap, redaction, replay.

No network: every test gives the recorder a mock transport as `inner`. The retry
rule, the cap and the replay are tested with the real `OmniReader` and, for the stop
and the accounting, with the real harness function `evaluate.evaluate`.
"""

import base64
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest

from app.docintel import evaluate as ev
from app.docintel import omni, telemetry
from app.docintel.omni import OmniReader, OmniSettings
from app.docintel.telemetry import (
    BrokenLine,
    Lock,
    Recorder,
    Replay,
    Stop,
    calls,
    load,
    summary,
)
from app.models import Document, DocumentType, Extraction
from tests.test_mp1_reader import MP1, perfect_model
from tests.test_omni_reader import (
    ANSWER,
    BOOKING,
    CANCELLATION,
    URL,
    doc,
    nothing_on,
    two_pages,
)

KEY = "nvapi-test-secret-0123456789abcdef"
MISSING: Any = object()  # "this key is not in the answer"
BOOKING_TYPE = DocumentType.booking_confirmation
USAGE = {"prompt_tokens": 100, "completion_tokens": 10}
Json = dict[str, Any]


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVIDIA_KEY", KEY)
    monkeypatch.setenv("VLM_BASE_URL", URL)
    monkeypatch.setenv("VLM_MODEL", "omni-test")


# ------------------------------------------------------------------ the doubles


def chat(content: Any, finish: Any = "stop", usage: Any = USAGE) -> httpx.Response:
    """A 200 chat answer; `MISSING` leaves the finish reason or the usage out."""
    text = content if isinstance(content, str) else json.dumps(content)
    choice: Json = {"message": {"content": text}}
    if finish is not MISSING:
        choice["finish_reason"] = finish
    answer: Json = {"choices": [choice]}
    if usage is not MISSING:
        answer["usage"] = usage
    return httpx.Response(200, json=answer)


def respond(reply: Any) -> httpx.Response:
    """An exception is raised, an int is that status, a tuple is (status, headers,
    text), a Response is itself, anything else is the content of a 200 answer."""
    if isinstance(reply, BaseException):
        raise reply
    if isinstance(reply, httpx.Response):
        return reply
    if isinstance(reply, int):
        return httpx.Response(reply, text='{"error": "x"}')
    if isinstance(reply, tuple):
        status, headers, text = reply
        return httpx.Response(status, headers=headers, text=text)
    return chat(reply)


class Endpoint(httpx.BaseTransport):
    """The `inner` transport: the next scripted reply for each request. It counts
    what it receives, and keeps the headers to prove that the key did go out."""

    def __init__(self, *replies: Any, after: Callable[[], None] | None = None) -> None:
        self.replies = iter(replies)
        self.requests: list[Json] = []
        self.headers: list[httpx.Headers] = []
        self.after = after

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        self.headers.append(request.headers)
        if self.after:
            self.after()
        return respond(next(self.replies))


class Flaky(httpx.BaseTransport):
    """`inner`, except that request number n (from 1) gets the scripted reply."""

    def __init__(self, inner: httpx.BaseTransport, plan: dict[int, Any]) -> None:
        self.inner, self.plan, self.count = inner, plan, 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.count += 1
        if self.count in self.plan:
            return respond(self.plan[self.count])
        return self.inner.handle_request(request)


class Clock:
    """A clock that moves only when it is told to or when a sleep is asked."""

    def __init__(self) -> None:
        self.now, self.sleeps = 0.0, list[float]()

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make(
    tmp_path: Path, inner: httpx.BaseTransport, cap: int = 50, **kwargs: Any
) -> tuple[Recorder, list[float]]:
    """A recorder on `calls.jsonl`, and the list of the waits that it was asked for."""
    slept: list[float] = []
    kwargs.setdefault("secrets", [KEY])
    kwargs.setdefault("sleep", slept.append)
    rec = Recorder(tmp_path / "calls.jsonl", cap=cap, inner=inner, **kwargs)
    return rec, slept


def reader_of(rec: Recorder, scope: str = "page", **settings: Any) -> OmniReader:
    """The real reader on the recorder: no change of the reader is needed."""
    client = httpx.Client(transport=rec.transport, timeout=30.0)
    return OmniReader(
        client=client, sleep=rec.sleep, settings=OmniSettings(scope=scope, **settings)
    )


def body_of(kind: str, n: int = 0, images: int = 1) -> Json:
    """A request body of a kind; `n` tells two bodies of one kind apart."""
    parts: list[Json] = [{"type": "text", "text": f"read {n}"}]
    if kind == "classify":
        parts = [{"type": "text", "text": omni.CLASSIFY}]
    for i in range(images):
        if kind == "document":
            parts.append({"type": "text", "text": f"Page {i + 1} of {images}:"})
        raw = base64.b64encode(f"image {n} {i}".encode()).decode()
        url = f"data:image/png;base64,{raw}"
        parts.append({"type": "image_url", "image_url": {"url": url}})
    return {"model": "m", "messages": [{"role": "user", "content": parts}]}


def post(rec: Recorder | Replay, body: Json) -> httpx.Response:
    transport = rec.transport if isinstance(rec, Recorder) else rec
    with httpx.Client(transport=transport) as client:
        return client.post(URL, json=body, headers={"Authorization": f"Bearer {KEY}"})


def lines_of(rec: Recorder) -> list[Json]:
    return load(rec.path)


def line(call: int, attempt: int = 1, **found: Any) -> Json:
    """A record line made by hand, for `calls` and `summary`."""
    record: Json = {
        "tag": "t",
        "call": call,
        "attempt": attempt,
        "kind": "page",
        "request_sha256": f"h{call}",
        "images": [],
        "started": "2026-11-27T10:00:00.000Z",
        "seconds": 1.0,
        "status": 200,
        "error": None,
        "retry_after": None,
        "finish_reason": "stop",
        "usage": {"prompt": 10, "completion": 2, "reasoning": 0, "cached": None},
    }
    return record | found


# ------------------------------------------------------------------ the module


def test_the_module_is_silent_at_import_and_reads_no_environment() -> None:
    backend = Path(__file__).resolve().parents[1]
    env = {k: v for k, v in os.environ.items() if k in ("SYSTEMROOT", "PATH")}
    code = f"import sys; sys.path.insert(0, {str(backend)!r})"
    code += "; import app.docintel.telemetry"
    done = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True
    )
    assert done.returncode == 0 and done.stdout == "" and done.stderr == ""
    source = Path(telemetry.__file__).read_text(encoding="utf-8")
    assert "os.environ" not in source and "getenv" not in source
    assert "dotenv" not in source and '.env"' not in source


def test_the_recorder_refuses_an_old_record_and_bad_limits(tmp_path: Path) -> None:
    old = tmp_path / "old.jsonl"
    old.write_text("{}\n", encoding="utf-8")
    with pytest.raises(Stop, match="record"):
        Recorder(old, cap=1, inner=Endpoint())
    new = tmp_path / "new.jsonl"
    with pytest.raises(ValueError):
        Recorder(new, cap=-1, inner=Endpoint())
    with pytest.raises(ValueError):
        Recorder(new, cap=1, inner=Endpoint(), failing_calls=0)
    with pytest.raises(ValueError):
        Recorder(new, cap=1, inner=Endpoint(), pace=-1.0)


# ------------------------------------------------------------------ the request


def test_the_request_hash_covers_the_image_bytes_and_not_their_form() -> None:
    body = body_of("document", 1, images=2)
    hashes = telemetry.image_hashes(body)
    assert hashes == [
        hashlib.sha256(f"image 1 {i}".encode()).hexdigest() for i in (0, 1)
    ]
    swapped = json.loads(json.dumps(body, sort_keys=True))  # another key order
    assert telemetry.request_sha256(swapped) == telemetry.request_sha256(body)
    expected = json.loads(json.dumps(body))
    for part, digest in zip(
        [p for p in expected["messages"][0]["content"] if p["type"] == "image_url"],
        hashes,
        strict=True,
    ):
        part["image_url"]["url"] = "sha256:" + digest
    assert (
        telemetry.request_sha256(body)
        == hashlib.sha256(json.dumps(expected, sort_keys=True).encode()).hexdigest()
    )
    assert telemetry.request_sha256(body_of("document", 2, images=2)) != (
        telemetry.request_sha256(body)
    )
    assert telemetry.image_hashes({"messages": "not a request"}) == []


def test_the_kind_of_a_call() -> None:
    assert telemetry.call_kind(body_of("classify")) == "classify"
    assert telemetry.call_kind(body_of("page")) == "page"
    assert telemetry.call_kind(body_of("document", images=3)) == "document"
    assert telemetry.call_kind({}) == "page"


def test_the_recorder_gives_each_real_request_its_kind_and_image_hashes(
    tmp_path: Path,
) -> None:
    page = nothing_on(BOOKING_TYPE)
    both = {"page_1": page, "page_2": page}
    rec, _ = make(tmp_path, Endpoint(ANSWER, both))
    reader_of(rec, "document").extract(doc(2), two_pages(), "travel")
    classify, document = lines_of(rec)
    assert (classify["kind"], len(classify["images"])) == ("classify", 1)
    assert (document["kind"], len(document["images"])) == ("document", 2)
    sent = [omni.pages(two_pages())[n].image for n in (0, 1)]
    assert document["images"] == [hashlib.sha256(i).hexdigest() for i in sent]


# ------------------------------------------------------------------ the answer


FULL = {
    "prompt_tokens": 2711,
    "completion_tokens": 267,
    "completion_tokens_details": {"reasoning_tokens": 5},
    "prompt_tokens_details": {"cached_tokens": 64},
}
NONE = {"prompt": None, "completion": None, "reasoning": None, "cached": None}


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (
            {"usage": FULL},
            {"prompt": 2711, "completion": 267, "reasoning": 5, "cached": 64},
        ),
        ({}, NONE),  # no usage
        (None, NONE),
        ("not an answer", NONE),
        ({"usage": "x"}, NONE),
        ({"usage": USAGE}, NONE | {"prompt": 100, "completion": 10}),  # no details
        (
            {"usage": USAGE | {"completion_tokens_details": None}},
            NONE | {"prompt": 100, "completion": 10},
        ),
        ({"usage": {"prompt_tokens": True}}, NONE),  # a bool is not a count
        ({"usage": {"prompt_tokens": "12"}}, NONE),  # a text is not a count
        ({"usage": {"prompt_tokens": 1.5}}, NONE),
        ({"usage": {"prompt_tokens": -3}}, NONE),
        ({"usage": {"prompt_tokens": 0}}, NONE | {"prompt": 0}),  # 0 is a count
        (
            {"usage": {"completion_tokens_details": {"reasoning_tokens": 0}}},
            NONE | {"reasoning": 0},
        ),
        (
            {"usage": {"prompt_tokens_details": {"cached_tokens": 0}}},
            NONE | {"cached": 0},
        ),
    ],
)
def test_usage_of(answer: Any, expected: dict[str, int | None]) -> None:
    assert telemetry.usage_of(answer) == expected


def test_the_summary_sums_only_what_the_endpoint_reports(tmp_path: Path) -> None:
    usages = [
        FULL,
        MISSING,  # no usage at all
        {"prompt_tokens": 20, "completion_tokens": 6},  # usage with no details
        {  # a bool, a text, a negative number and a reported 0
            "prompt_tokens": True,
            "completion_tokens": "7",
            "completion_tokens_details": {"reasoning_tokens": -1},
            "prompt_tokens_details": {"cached_tokens": 0},
        },
    ]
    rec, _ = make(tmp_path, Endpoint(*[chat("{}", usage=u) for u in usages]))
    for n in range(len(usages)):
        post(rec, body_of("page", n))
    tokens = summary(lines_of(rec))["tokens"]
    assert tokens["prompt"] == {"sum": 2731, "reported": 2, "missing": 2}
    assert tokens["completion"] == {"sum": 273, "reported": 2, "missing": 2}
    assert tokens["reasoning"] == {"sum": 5, "reported": 1, "missing": 3}
    assert tokens["cached"] == {"sum": 64, "reported": 2, "missing": 2}  # 64 and a 0


def test_a_count_that_no_answer_reports_has_no_sum(tmp_path: Path) -> None:
    rec, _ = make(tmp_path, Endpoint(chat("{}"), chat("{}", usage={"x": 1})))
    post(rec, body_of("page", 1))
    post(rec, body_of("page", 2))
    tokens = summary(lines_of(rec))["tokens"]
    assert tokens["reasoning"] == {"sum": None, "reported": 0, "missing": 2}
    assert tokens["prompt"]["sum"] == 100 and tokens["prompt"]["reported"] == 1
    assert not any(name.startswith("estimated") for name in tokens)
    zero = [line(1, usage={"prompt": 0, "completion": 0, "reasoning": 0, "cached": 0})]
    assert summary(zero)["tokens"]["cached"] == {"sum": 0, "reported": 1, "missing": 0}


@pytest.mark.parametrize("finish", ["stop", "length", MISSING, None, 7, ""])
def test_the_finish_reason_is_recorded_and_counted_per_kind(
    tmp_path: Path, finish: Any
) -> None:
    rec, _ = make(tmp_path, Endpoint(chat("{}", finish), chat("{}", "stop")))
    post(rec, body_of("document", 1, images=2))
    post(rec, body_of("page", 2))
    saved = lines_of(rec)[0]["finish_reason"]
    word = finish if isinstance(finish, str) and finish else None
    assert saved == word
    reasons = summary(lines_of(rec))["finish_reasons"]
    assert reasons == {"document": {word or "none": 1}, "page": {"stop": 1}}


# ------------------------------------------------------------------ the secrets


def test_redact_removes_secrets_data_urls_and_long_runs() -> None:
    text = f"bad {KEY} Bearer {KEY[:10]} data:image/png;base64,AAAA {'A' * 120} end"
    out = telemetry.redact(text, [KEY])
    assert KEY not in out and "Bearer" not in out and "data:" not in out
    assert "A" * 100 not in out and out.endswith(" end")
    assert telemetry.redact("x " * 450, [KEY]) == ("x " * 450)[:500]
    assert telemetry.redact("abc abc", ["abc"]) == "abc abc"  # a short secret is no key
    assert telemetry.redact(json.dumps('a"b' + KEY), [KEY, 'a"b']) == json.dumps(
        'a"b<redacted>'
    )


def test_find_secrets_finds_a_planted_secret_and_ignores_a_short_one(
    tmp_path: Path,
) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "leak.txt").write_text(f"x {KEY} y", encoding="utf-8")
    (tmp_path / "escaped.json").write_text(json.dumps('q"' + KEY), encoding="utf-8")
    (tmp_path / "clean.txt").write_text("nothing here, only abc", encoding="utf-8")
    assert telemetry.find_secrets(tmp_path, [KEY]) == ["escaped.json", "sub/leak.txt"]
    assert telemetry.find_secrets(tmp_path, ["abc", "short"]) == []
    assert telemetry.find_secrets(tmp_path / "missing", [KEY]) == []


def test_no_secret_header_image_or_prompt_is_ever_written(tmp_path: Path) -> None:
    leaky = f"bad key {KEY}, header Bearer {KEY}, image data:image/png;base64,AAAA"
    replies = [
        chat(f"the model repeats {KEY} and data:image/png;base64,QUJD"),
        (401, {}, json.dumps({"error": leaky})),
        (429, {"Retry-After": KEY}, "{}"),
        chat(ANSWER),
    ]
    inner = Endpoint(*replies)
    rec, _ = make(tmp_path, inner)
    reader = reader_of(rec)
    reader.classify(doc(), BOOKING.read_bytes())  # a 200 that repeats the key
    reader_of(rec).classify(doc(), CANCELLATION.read_bytes())  # a 401, final
    reader_of(rec).classify(doc(), two_pages())  # a 429, then a 200
    assert "Bearer" in inner.headers[0]["authorization"]  # the key did go out
    assert KEY in inner.headers[0]["authorization"]
    text = rec.path.read_text(encoding="utf-8")
    low = text.lower()
    assert KEY not in text and KEY[:8] not in text
    assert (
        "bearer" not in low and "authorization" not in low and "data:image" not in low
    )
    assert "base64" not in low and "Page 1" not in text and "Read the whole" not in text
    assert any(s["status"] == 401 for s in lines_of(rec))
    assert "<redacted>" in text
    assert telemetry.find_secrets(tmp_path, [KEY]) == []


# ------------------------------------------------------------------ retry or new call


def test_a_429_then_a_200_is_one_call_with_two_attempts(tmp_path: Path) -> None:
    rec, slept = make(tmp_path, Endpoint((429, {"Retry-After": "3"}, "{}"), ANSWER))
    assert (
        reader_of(rec).classify(doc(), BOOKING.read_bytes()) == "booking_confirmation"
    )
    first, second = lines_of(rec)
    assert [first["call"], first["attempt"], first["status"]] == [1, 1, 429]
    assert [second["call"], second["attempt"], second["status"]] == [1, 2, 200]
    assert first["retry_after"] == "3" and first["error_body"] == "{}"
    assert "body" not in first and "error_body" not in second
    assert slept == [3.0] and rec.attempts == 2
    [only] = calls(lines_of(rec))
    assert only["attempts"] == [first, second]
    stats = summary(lines_of(rec))
    assert (stats["attempts"], stats["retries"], stats["http_429"]) == (2, 1, 1)


def test_three_503_are_one_call_with_three_attempts_and_no_answer(
    tmp_path: Path,
) -> None:
    rec, slept = make(tmp_path, Endpoint(503, 503, 503))
    assert reader_of(rec).classify(doc(), BOOKING.read_bytes()) == "unknown"
    assert [(x["call"], x["attempt"], x["status"]) for x in lines_of(rec)] == [
        (1, 1, 503),
        (1, 2, 503),
        (1, 3, 503),
    ]
    assert slept == [2.0, 8.0]
    [only] = calls(lines_of(rec))
    assert len(only["attempts"]) == 3 and only["attempts"][-1]["status"] == 503
    stats = summary(lines_of(rec))
    assert stats["calls"] == {"classify": 1} and stats["calls_without_answer"] == 1
    assert stats["by_status"] == {"503": 3} and stats["retries"] == 2


def test_a_timeout_then_a_200_is_one_call(tmp_path: Path) -> None:
    rec, slept = make(tmp_path, Endpoint(httpx.ReadTimeout("slow"), ANSWER))
    assert (
        reader_of(rec).classify(doc(), BOOKING.read_bytes()) == "booking_confirmation"
    )
    first, second = lines_of(rec)
    assert (first["status"], first["error"]) == (None, "ReadTimeout")
    assert first["call"] == second["call"] == 1 and second["attempt"] == 2
    assert slept == [2.0] and rec.attempts == 2
    stats = summary(lines_of(rec))
    assert stats["timeouts"] == 1 and stats["transport_errors"] == {"ReadTimeout": 1}
    assert stats["by_status"] == {"200": 1} and stats["calls_without_answer"] == 0


@pytest.mark.parametrize("failure", [(400,), (503, 503, 503)])
def test_a_failed_classify_and_the_classify_of_extract_are_two_calls(
    tmp_path: Path, failure: tuple[int, ...]
) -> None:
    """Same request, same hash. The reader sleeps between the attempts of a call and
    never after the last one, so nothing separates the two calls but the order."""
    data = BOOKING.read_bytes()
    rec, slept = make(tmp_path, Endpoint(*failure, ANSWER, nothing_on(BOOKING_TYPE)))
    reader = reader_of(rec)
    assert reader.classify(doc(), data) == "unknown"
    assert slept == [2.0, 8.0][: len(failure) - 1]
    reader.extract(doc(), data, "travel")  # classify again, then the page
    found = calls(lines_of(rec))
    assert [c["call"] for c in found] == [1, 2, 3]
    assert [c["kind"] for c in found] == ["classify", "classify", "page"]
    assert found[0]["request_sha256"] == found[1]["request_sha256"]
    assert [len(c["attempts"]) for c in found] == [len(failure), 1, 1]
    assert [c["attempts"][-1]["status"] for c in found] == [failure[-1], 200, 200]
    assert len(slept) == len(failure) - 1  # nothing slept between the two calls


def test_two_documents_with_the_same_first_page_are_two_calls(tmp_path: Path) -> None:
    rec, _ = make(
        tmp_path, Endpoint((429, {}, "{}"), ANSWER, ANSWER, nothing_on(BOOKING_TYPE))
    )
    reader = reader_of(rec)
    reader.classify(doc(), BOOKING.read_bytes())  # a retry inside the first call
    reader.classify(doc(2), two_pages())  # another document, the same first page
    first, second = calls(lines_of(rec))
    assert first["request_sha256"] == second["request_sha256"]
    assert (len(first["attempts"]), len(second["attempts"])) == (2, 1)
    assert (first["call"], second["call"]) == (1, 2)


def test_a_document_call_and_its_retry_are_one_call(tmp_path: Path) -> None:
    page = nothing_on(BOOKING_TYPE)
    rec, slept = make(tmp_path, Endpoint(ANSWER, 503, {"page_1": page, "page_2": page}))
    reader_of(rec, "document").extract(doc(2), two_pages(), "travel")
    classify, document = calls(lines_of(rec))
    first, second = document["attempts"]
    assert classify["kind"] == "classify" and document["kind"] == "document"
    assert first["kind"] == second["kind"] == "document"
    assert len(first["images"]) == len(second["images"]) == 2
    assert (first["call"], second["call"]) == (2, 2)
    assert (first["attempt"], second["attempt"]) == (1, 2) and slept == [2.0]
    assert first["request_sha256"] == second["request_sha256"]


def test_a_sleep_is_a_retry_only_for_the_same_request_and_not_after_begin(
    tmp_path: Path,
) -> None:
    rec, _ = make(tmp_path, Endpoint("{}", "{}", "{}", "{}"))
    post(rec, body_of("page", 1))
    rec.sleep(1.0)
    post(rec, body_of("page", 2))  # slept, but another request: a new call
    rec.sleep(1.0)
    post(rec, body_of("page", 2))  # slept and the same request: a retry
    rec.begin("next")
    rec.sleep(1.0)
    post(rec, body_of("page", 2))  # begin: a new call, whatever happened before
    assert [(x["tag"], x["call"], x["attempt"]) for x in lines_of(rec)] == [
        ("", 1, 1),
        ("", 2, 1),
        ("", 2, 2),
        ("next", 3, 1),
    ]


# ------------------------------------------------------------------ the cap


def test_the_cap_stops_the_real_reader_and_sends_nothing_more(tmp_path: Path) -> None:
    page = nothing_on(BOOKING_TYPE)
    inner = Endpoint(ANSWER, page, page)
    rec, _ = make(tmp_path, inner, cap=2)
    reader = reader_of(rec)
    with pytest.raises(Stop) as stop:
        reader.extract(doc(2), two_pages(), "travel")
    assert stop.value.reason == "cap" and rec.stopped == "cap"
    assert len(inner.requests) == rec.attempts == 2
    *sent, last = lines_of(rec)
    assert len(sent) == 2 and last["status"] is None
    assert last["error"] == "not sent: cap" and last["call"] == 3
    with pytest.raises(Stop, match="cap"):  # the reader tries again: still nothing
        reader.extract(doc(2), two_pages(), "travel")
    assert len(inner.requests) == 2 and len(lines_of(rec)) == 3
    stats = summary(lines_of(rec))
    assert (stats["attempts"], stats["not_sent"]) == (2, 1)
    assert stats["calls"] == {
        "classify": 1,
        "page": 1,
    }  # a call never sent is not a call


def test_a_retry_counts_and_attempt_n_plus_1_is_never_sent(tmp_path: Path) -> None:
    inner = Endpoint(503, 503, ANSWER)
    rec, slept = make(tmp_path, inner, cap=2)
    with pytest.raises(Stop, match="cap"):
        reader_of(rec).classify(doc(), BOOKING.read_bytes())
    assert len(inner.requests) == 2 and rec.attempts == 2 and slept == [2.0, 8.0]
    *sent, last = lines_of(rec)
    assert [x["attempt"] for x in sent] == [1, 2]
    assert (last["call"], last["attempt"], last["error"]) == (1, 3, "not sent: cap")
    stats = summary(lines_of(rec))
    assert (stats["attempts"], stats["retries"], stats["calls_without_answer"]) == (
        2,
        1,
        1,
    )


def test_whatever_the_caller_does_after_the_stop_the_inner_gets_at_most_the_cap(
    tmp_path: Path,
) -> None:
    inner = Endpoint(*["{}"] * 20)
    rec, _ = make(tmp_path, inner, cap=3)
    stops = 0
    for n in range(12):  # a caller that swallows every Stop and tries again
        try:
            post(rec, body_of("page", n))
        except Stop:
            stops += 1
    assert len(inner.requests) == rec.attempts == 3 and stops == 9
    assert [x["error"] for x in lines_of(rec)].count("not sent: cap") == 1


def test_a_cap_of_zero_sends_nothing(tmp_path: Path) -> None:
    inner = Endpoint("{}")
    rec, _ = make(tmp_path, inner, cap=0)
    with pytest.raises(Stop, match="cap"):
        post(rec, body_of("page"))
    assert inner.requests == [] and rec.attempts == 0


def test_five_calls_in_a_row_with_no_200_stop_before_the_sixth(tmp_path: Path) -> None:
    inner = Endpoint(*[400] * 6)
    rec, _ = make(tmp_path, inner)
    for _ in range(5):  # a new reader each time: a failure is not kept
        assert reader_of(rec).classify(doc(), BOOKING.read_bytes()) == "unknown"
    with pytest.raises(Stop, match="endpoint failing"):
        reader_of(rec).classify(doc(), BOOKING.read_bytes())
    assert len(inner.requests) == rec.attempts == 5
    assert rec.stopped == "endpoint failing"
    assert lines_of(rec)[-1]["error"] == "not sent: endpoint failing"
    assert summary(lines_of(rec))["not_sent"] == 1


def test_a_200_between_resets_the_count_of_failing_calls(tmp_path: Path) -> None:
    replies = [400] * 4 + [ANSWER] + [400] * 4 + [ANSWER] + [400] * 4
    inner = Endpoint(*replies)
    rec, _ = make(tmp_path, inner)
    for _ in range(len(replies)):
        reader_of(rec).classify(doc(), BOOKING.read_bytes())  # all are sent
    assert len(inner.requests) == 14 and rec.stopped is None


def test_the_retries_of_one_call_count_as_one_failing_call(tmp_path: Path) -> None:
    inner = Endpoint(503, 503, 503, ANSWER)
    rec, _ = make(tmp_path, inner, failing_calls=2)
    assert reader_of(rec).classify(doc(), BOOKING.read_bytes()) == "unknown"
    assert (
        reader_of(rec).classify(doc(), BOOKING.read_bytes()) == "booking_confirmation"
    )
    assert len(inner.requests) == 4  # 3 attempts, one failing call: not 2 in a row


def test_a_stop_is_sticky_even_after_begin(tmp_path: Path) -> None:
    inner = Endpoint(400, 400)
    rec, _ = make(tmp_path, inner, failing_calls=1)
    post(rec, body_of("page", 1))
    with pytest.raises(Stop):
        post(rec, body_of("page", 2))
    rec.begin("later")
    with pytest.raises(Stop, match="endpoint failing"):
        post(rec, body_of("page", 3))
    assert len(inner.requests) == 1 and len(lines_of(rec)) == 2


# ------------------------------------------------------------------ pace and lines


def test_pace_keeps_the_starts_apart_and_is_not_in_the_seconds(tmp_path: Path) -> None:
    clock = Clock()
    starts: list[float] = []
    inner = Endpoint(*["{}"] * 3)

    def work() -> None:  # each request takes one second
        starts.append(clock.now)
        clock.now += 1.0

    inner.after = work
    rec = Recorder(
        tmp_path / "c.jsonl",
        cap=9,
        inner=inner,
        pace=5.0,
        clock=clock,
        sleep=clock.sleep,
    )
    for n in range(3):
        post(rec, body_of("page", n))
    assert all(b - a >= 5.0 for a, b in zip(starts, starts[1:], strict=False))
    assert clock.sleeps == [4.0, 4.0]
    assert [x["seconds"] for x in lines_of(rec)] == [1.0, 1.0, 1.0]


def test_a_pace_of_zero_never_sleeps(tmp_path: Path) -> None:
    clock = Clock()
    rec = Recorder(
        tmp_path / "c.jsonl",
        cap=9,
        inner=Endpoint("{}", "{}"),
        clock=clock,
        sleep=clock.sleep,
    )
    post(rec, body_of("page", 1))
    post(rec, body_of("page", 2))
    assert clock.sleeps == []


def test_the_retry_wait_of_the_reader_counts_in_the_pace(tmp_path: Path) -> None:
    clock = Clock()
    inner = Endpoint((503, {}, "{}"), ANSWER)
    rec = Recorder(
        tmp_path / "c.jsonl",
        cap=9,
        inner=inner,
        pace=1.5,
        clock=clock,
        sleep=clock.sleep,
    )
    reader_of(rec).classify(doc(), BOOKING.read_bytes())
    assert clock.sleeps == [2.0]  # the reader's 2 s already lie between the starts


def test_every_line_is_on_disk_before_the_transport_returns_or_raises(
    tmp_path: Path,
) -> None:
    seen: list[int] = []

    def count() -> None:
        seen.append(len(rec.path.read_text(encoding="utf-8").splitlines()))

    rec, _ = make(
        tmp_path,
        Endpoint("{}", httpx.ConnectError("down"), "{}", "{}", after=count),
        cap=3,
    )
    post(rec, body_of("page", 1))
    with pytest.raises(httpx.ConnectError):
        post(rec, body_of("page", 2))
    assert len(lines_of(rec)) == 2 and lines_of(rec)[1]["error"] == "ConnectError"
    post(rec, body_of("page", 3))
    with pytest.raises(Stop):
        post(rec, body_of("page", 4))
    assert len(lines_of(rec)) == 4
    assert seen == [0, 1, 2]  # each request found the earlier lines already written


def test_a_non_transport_error_of_inner_is_recorded_and_raised_again(
    tmp_path: Path,
) -> None:
    rec, _ = make(tmp_path, Endpoint(KeyError("boom")))
    with pytest.raises(KeyError):
        post(rec, body_of("page"))
    [only] = lines_of(rec)
    assert only["error"] == "KeyError" and only["status"] is None and rec.attempts == 1


# ------------------------------------------------------------------ stop and accounting


def test_a_stop_leaves_the_real_reader_and_the_real_harness(tmp_path: Path) -> None:
    path = MP1 / "mp1-02-medical-invoice-continued.pdf"
    inner = perfect_model()
    rec, _ = make(tmp_path, inner, cap=2)
    reader = reader_of(rec)
    with pytest.raises(Stop):  # the reader never raises, but a Stop is not an error
        reader.extract(
            Document(id="D", file=path.name, pages=2), path.read_bytes(), "travel"
        )
    assert rec.attempts == 2

    harness, _ = make(tmp_path / "h", perfect_model(), cap=5)
    make_reader, named, meter = ev._omni(
        transport=harness.transport,
        sleep=harness.sleep,
        options=OmniSettings(scope="document"),
    )
    harness.begin("r1-document")
    with pytest.raises(Stop, match="cap"):
        ev.evaluate(make_reader, 1, settings=named, meter=meter, document_set="mp1")
    assert harness.attempts == 5 and harness.stopped == "cap"
    assert len(lines_of(harness)) == 6 and meter is not None and meter.requests == 6


@pytest.mark.parametrize(
    ("scope", "per_kind"),
    [
        ("page", {"classify": 8, "page": 19}),
        ("document", {"classify": 8, "document": 8}),
    ],
)
def test_a_whole_run_through_the_harness_is_accounted_exactly(
    tmp_path: Path, scope: str, per_kind: dict[str, int]
) -> None:
    rec, slept = make(tmp_path, perfect_model())
    make_reader, named, meter = ev._omni(
        transport=rec.transport, sleep=rec.sleep, options=OmniSettings(scope=scope)
    )
    rec.begin(f"r1-{scope}")
    report = ev.evaluate(
        make_reader, 1, settings=named, meter=meter, document_set="mp1"
    )
    stats = summary(lines_of(rec))
    assert stats["calls"] == per_kind and stats["attempts"] == sum(per_kind.values())
    assert stats["retries"] == 0 and stats["calls_without_answer"] == 0
    assert report["run_details"][0]["http"]["requests"] == rec.attempts
    assert list(stats["by_tag"]) == [f"r1-{scope}"] and slept == []
    assert {c["call"] for c in calls(lines_of(rec))} == set(range(1, rec.attempts + 1))


# ------------------------------------------------------------------ replay


def run_mp1(
    rec_or_replay: Recorder | Replay, scope: str, **settings: Any
) -> tuple[Extraction, int]:
    """The first MP1 document through the real reader; the extraction and the
    number of requests that the reader made."""
    path = MP1 / "mp1-01-repair-invoice-carried-forward.pdf"
    made: list[int] = []
    transport = (
        rec_or_replay.transport
        if isinstance(rec_or_replay, Recorder)
        else rec_or_replay
    )
    client = httpx.Client(
        transport=transport, event_hooks={"request": [lambda r: made.append(1)]}
    )
    sleep = (
        rec_or_replay.sleep if isinstance(rec_or_replay, Recorder) else lambda s: None
    )
    reader = OmniReader(
        client=client, sleep=sleep, settings=OmniSettings(scope=scope, **settings)
    )
    document = Document(id="D-mp1", file=path.name, pages=3)
    return reader.extract(document, path.read_bytes(), "travel"), len(made)


PLANS: dict[str, dict[int, Any]] = {
    # page 2 fails once, page 3 fails three times (a page that stays unread)
    "page": {3: 503, 5: 503, 6: (503, {}, "{}"), 7: 503},
    # the document call: a 429 with Retry-After, then a timeout, then the answer
    "document": {2: (429, {"Retry-After": "3"}, "{}"), 3: httpx.ReadTimeout("slow")},
}


@pytest.mark.parametrize("scope", ["page", "document"])
def test_a_replay_gives_the_same_extraction_the_same_requests_and_nothing_left(
    tmp_path: Path, scope: str
) -> None:
    rec, slept = make(tmp_path, Flaky(perfect_model(), PLANS[scope]))
    rec.begin("r1")
    live, live_requests = run_mp1(rec, scope)
    assert live.fields and live_requests == rec.attempts
    stats = summary(lines_of(rec))
    assert stats["retries"] >= 1 and (scope == "page") == (
        stats["calls_without_answer"] == 1
    )
    replay = Replay(lines_of(rec))
    assert replay.left == rec.attempts
    again, again_requests = run_mp1(replay, scope)
    assert again == live and again_requests == live_requests and replay.left == 0


@pytest.mark.parametrize(
    ("change", "served"),
    [({"dpi": 100}, 0), ({"scope": "document"}, 1)],  # the classify is the same
)
def test_a_replay_stops_at_the_first_request_that_is_not_the_saved_one(
    tmp_path: Path, change: dict[str, Any], served: int
) -> None:
    rec, _ = make(tmp_path, perfect_model())
    run_mp1(rec, "page")
    replay = Replay(lines_of(rec))
    total = replay.left
    scope = change.pop("scope", "page")
    with pytest.raises(Stop, match="replay: the request differs") as stop:
        run_mp1(replay, scope, **change)
    assert f"call {served + 1}, attempt 1" in stop.value.reason
    assert replay.left == total - served


def test_a_request_after_the_last_saved_attempt_is_a_stop(tmp_path: Path) -> None:
    rec, _ = make(tmp_path, Endpoint(ANSWER))
    reader_of(rec).classify(doc(), BOOKING.read_bytes())
    replay = Replay(lines_of(rec))
    reader = OmniReader(client=httpx.Client(transport=replay), settings=OmniSettings())
    assert reader.classify(doc(), BOOKING.read_bytes()) == "booking_confirmation"
    assert replay.left == 0
    with pytest.raises(Stop, match="after the last saved attempt"):
        post(replay, body_of("page"))


def test_a_saved_transport_error_is_raised_again_and_the_reader_retries_as_before(
    tmp_path: Path,
) -> None:
    rec, slept = make(tmp_path, Endpoint(httpx.ReadTimeout("slow"), ANSWER))
    reader_of(rec).classify(doc(), BOOKING.read_bytes())
    saved = lines_of(rec)
    replay = Replay(saved)
    waits: list[float] = []
    reader = OmniReader(
        client=httpx.Client(transport=replay),
        sleep=waits.append,
        settings=OmniSettings(),
    )
    assert reader.classify(doc(), BOOKING.read_bytes()) == "booking_confirmation"
    assert waits == slept == [2.0] and replay.left == 0
    # the error itself is the httpx error of that name, or a TransportError
    request = _first_body(tmp_path)
    for name, kind in (
        ("ReadTimeout", httpx.ReadTimeout),
        ("Odd", httpx.TransportError),
    ):
        with pytest.raises(kind) as raised:
            post(Replay([saved[0] | {"error": name}]), request)
        assert type(raised.value) is kind


def test_replay_skips_the_lines_not_sent_and_gives_the_saved_status(
    tmp_path: Path,
) -> None:
    rec, _ = make(tmp_path, Endpoint((429, {"Retry-After": "7"}, "{}")), cap=1)
    with pytest.raises(Stop):  # a 429 (sent), then the retry is not sent
        reader_of(rec).classify(doc(), BOOKING.read_bytes())
    saved = lines_of(rec)
    assert [x["error"] for x in saved] == [None, "not sent: cap"]
    replay = Replay(saved)
    assert replay.left == 1
    request = httpx.Request("POST", URL, json=_first_body(tmp_path))
    answer = replay.handle_request(request)
    assert answer.status_code == 429 and answer.headers["retry-after"] == "7"
    assert answer.json() == {} and replay.left == 0


def _first_body(tmp_path: Path) -> Json:
    """The body of a classify request of the booking, as the reader sends it."""
    seen = Endpoint(ANSWER)
    rec, _ = make(tmp_path / "again", seen)
    reader_of(rec).classify(doc(), BOOKING.read_bytes())
    return seen.requests[0]


# ------------------------------------------------------------------ the lock


def test_a_second_lock_is_a_stop_and_keeps_the_file_of_the_first(
    tmp_path: Path,
) -> None:
    path = tmp_path / "run.lock"
    with Lock(path):
        before = path.read_text(encoding="utf-8")
        with pytest.raises(Stop, match="lock"), Lock(path):
            pass
        assert path.read_text(encoding="utf-8") == before
    assert not path.exists()


def test_the_lock_is_removed_when_the_body_raises(tmp_path: Path) -> None:
    path = tmp_path / "run.lock"
    with pytest.raises(ValueError), Lock(path):
        assert path.exists()
        raise ValueError("boom")
    assert not path.exists()
    with Lock(path):  # and a new run can start
        assert path.exists()


def test_the_lock_holds_the_process_id_and_the_time(tmp_path: Path) -> None:
    path = tmp_path / "run.lock"
    with Lock(path):
        held = json.loads(path.read_text(encoding="utf-8"))
    assert held["pid"] == os.getpid() and held["time"].endswith("Z")


def test_an_old_lock_is_never_taken_over(tmp_path: Path) -> None:
    path = tmp_path / "run.lock"
    path.write_text("pid 1 long ago\n", encoding="utf-8")
    with pytest.raises(Stop, match="lock"), Lock(path):
        pass
    assert path.read_text(encoding="utf-8") == "pid 1 long ago\n"


# ------------------------------------------------------------------ the record file


def test_a_broken_last_line_is_left_out_and_reported(tmp_path: Path) -> None:
    path = tmp_path / "calls.jsonl"
    good = [line(1), line(2, status=429)]
    text = "".join(json.dumps(x) + "\n" for x in good)
    path.write_text(text + '{"tag": "t", "call": 3, "att', encoding="utf-8")
    with pytest.warns(BrokenLine, match="line 3"):
        assert load(path) == good
    path.write_text(text, encoding="utf-8")  # a clean file gives no warning
    assert load(path) == good
    path.write_text(text.rstrip("\n"), encoding="utf-8")  # no final newline: complete
    assert load(path) == good


def test_a_broken_line_inside_the_file_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "calls.jsonl"
    path.write_text(json.dumps(line(1)) + "\n{broken\n" + json.dumps(line(2)) + "\n")
    with pytest.raises(ValueError, match="line 2"):
        load(path)


def test_an_empty_file_gives_an_empty_summary(tmp_path: Path) -> None:
    rec, _ = make(tmp_path, Endpoint())
    assert lines_of(rec) == [] and calls([]) == []
    stats = summary([])
    assert stats["calls"] == {} and stats["attempts"] == 0 and stats["by_tag"] == {}
    assert stats["seconds"] == 0 and stats["not_sent"] == 0
    assert stats["tokens"]["prompt"] == {"sum": None, "reported": 0, "missing": 0}


def test_the_summary_of_one_tag_and_of_several(tmp_path: Path) -> None:
    rec, _ = make(tmp_path, Endpoint("{}", 500, "{}", "{}"))
    rec.begin("a")
    post(rec, body_of("classify"))
    post(rec, body_of("page", 1))
    rec.begin("b")
    post(rec, body_of("document", 2, images=2))
    saved = lines_of(rec)
    assert [(x["tag"], x["call"]) for x in saved] == [("a", 1), ("a", 2), ("b", 3)]
    stats = summary(saved)
    assert stats["calls"] == {"classify": 1, "document": 1, "page": 1}
    assert (
        stats["by_status"] == {"200": 2, "500": 1}
        and stats["calls_without_answer"] == 1
    )
    assert set(stats["by_tag"]) == {"a", "b"}
    assert stats["by_tag"]["a"]["calls"] == {"classify": 1, "page": 1}
    assert stats["by_tag"]["a"]["calls_without_answer"] == 1
    assert stats["by_tag"]["b"]["calls"] == {"document": 1}
    one = summary([x for x in saved if x["tag"] == "b"])
    assert list(one["by_tag"]) == ["b"] and one["attempts"] == 1


def test_not_sent_lines_do_not_count_as_attempts_or_calls() -> None:
    sent = line(1)
    unsent = line(2, status=None, error="not sent: cap", seconds=None, usage=NONE)
    found = calls([sent, unsent])
    assert [(c["call"], len(c["attempts"])) for c in found] == [(1, 1)]
    stats = summary([sent, unsent])
    assert (stats["attempts"], stats["not_sent"], stats["calls"]) == (1, 1, {"page": 1})
    assert stats["calls_without_answer"] == 0 and stats["seconds"] == 1.0
    assert stats["finish_reasons"] == {"page": {"stop": 1}}
