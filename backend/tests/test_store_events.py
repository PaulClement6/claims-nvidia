import threading
from pathlib import Path

import pytest

from app.store import Store


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(str(tmp_path / "claims.db"))


def test_sequence_starts_at_1_per_claim(store: Store) -> None:
    a1 = store.append_event("A", "tool", "s", "t")
    a2 = store.append_event("A", "tool", "s", "t")
    b1 = store.append_event("B", "tool", "s", "t")
    assert (a1.sequence, a2.sequence, b1.sequence) == (1, 2, 1)


def test_events_since_returns_only_later_events_of_that_claim(store: Store) -> None:
    for _ in range(3):
        store.append_event("A", "tool", "s", "t")
    store.append_event("B", "tool", "s", "t")
    assert [e.sequence for e in store.events_since("A", 0)] == [1, 2, 3]
    assert [e.sequence for e in store.events_since("A", 2)] == [3]
    assert store.events_since("A", 3) == []


def test_event_round_trip(store: Store) -> None:
    sent = store.append_event(
        "A", "tool", "s", "Title", {"i": [1]}, {"o": {"x": 1}}, "m", 0.25
    )
    [got] = store.events_since("A", 0)
    assert got == sent
    assert got.inputs == {"i": [1]}
    assert got.model == "m"
    assert got.duration == 0.25


def test_two_threads_never_get_the_same_sequence(store: Store) -> None:
    def work() -> None:
        for _ in range(50):
            store.append_event("A", "tool", "s", "t")

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert [e.sequence for e in store.events_since("A", 0)] == list(range(1, 401))


def test_seed_is_read_from_claims_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Docker image holds no data folder: compose mounts it and sets this."""
    (tmp_path / "mock").mkdir()
    (tmp_path / "mock" / "policies.json").write_text(
        '{"customers": [{"id": "C-X", "name": "X"}], "policies": []}'
    )
    monkeypatch.setenv("CLAIMS_DATA", str(tmp_path))
    store = Store(str(tmp_path / "x.db"))
    assert [c.id for c in store.get_customers()] == ["C-X"]
