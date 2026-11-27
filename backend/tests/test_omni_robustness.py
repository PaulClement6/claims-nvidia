"""Two answers of the endpoint that made the reader raise (found while issue #126 was
tested, present since #18): JSON nested too deep, and `Retry-After: nan`. The reader
must never raise: a claim must not stop because of one answer. Stub only, no network.
"""

from typing import Any

import pytest

from app.docintel import omni
from tests.test_omni_reader import ANSWER, BOOKING, URL, Stub, answer_from, doc, names

DEEP = "[" * 100_000 + "]" * 100_000


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVIDIA_KEY", "test-key-value")
    monkeypatch.setenv("VLM_BASE_URL", URL)
    monkeypatch.setenv("VLM_MODEL", "omni-test")


@pytest.mark.parametrize("content", [DEEP, '{"values": ' + DEEP + ', "snippets": {}}'])
def test_a_page_answer_nested_too_deep_gives_no_field_and_no_error(
    content: str,
) -> None:
    stub = Stub(ANSWER, content)
    out = stub.reader().extract(doc(), BOOKING.read_bytes(), "travel")
    assert out.document_type == "booking_confirmation" and out.fields == []
    assert len(stub.requests) == 2  # the answer is final: no second try


def test_a_classify_answer_nested_too_deep_gives_unknown() -> None:
    assert Stub(DEEP).reader().classify(doc(), BOOKING.read_bytes()) == "unknown"


def test_the_json_of_an_answer_is_none_when_it_is_nested_too_deep() -> None:
    assert omni._json(DEEP) is None
    assert omni._json("[" * 50 + "]" * 50) is not None  # a normal depth is read


@pytest.mark.parametrize("header", ["nan", "NaN", "-nan"])
def test_a_retry_after_of_nan_waits_the_default_time(header: str) -> None:
    """`float("nan")` is a float: `min` and `max` kept it, and `time.sleep(nan)`
    raises. The default wait of the reader is used."""
    stub = Stub((429, {"Retry-After": header}), ANSWER)
    assert stub.reader().classify(doc(), BOOKING.read_bytes()) == "booking_confirmation"
    assert stub.waits == [omni.WAITS[0]]


@pytest.mark.parametrize(
    ("header", "wait"),
    [
        ("7", 7.0),
        ("0", 0.0),
        ("-3", 0.0),
        ("inf", omni.MAX_WAIT),
        ("1e999", omni.MAX_WAIT),
    ],
)
def test_a_retry_after_that_is_a_number_is_kept_within_its_bounds(
    header: str, wait: float
) -> None:
    stub = Stub((429, {"Retry-After": header}), ANSWER)
    stub.reader().classify(doc(), BOOKING.read_bytes())
    assert stub.waits == [wait]


def test_a_reader_with_these_fixes_still_reads_a_normal_answer() -> None:
    stub = Stub(ANSWER, answer_from(BOOKING))
    out: Any = stub.reader().extract(doc(), BOOKING.read_bytes(), "travel")
    assert len(names(out)) == 4
