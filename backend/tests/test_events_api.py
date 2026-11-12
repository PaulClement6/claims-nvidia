import asyncio
import json
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import httpx2 as httpx
import pytest
import uvicorn

from app.main import create_app
from app.store import Store
from app.stream import event_stream


def parse(chunk: str) -> tuple[int, dict[str, object]]:
    lines = dict(line.split(": ", 1) for line in chunk.strip().split("\n"))
    return int(lines["id"]), json.loads(lines["data"])


def test_stream_replays_after_a_sequence_then_sends_new_events(
    tmp_path: Path,
) -> None:
    store = Store(str(tmp_path / "claims.db"))
    for _ in range(3):
        store.append_event("A", "tool", "s", "t")
    polls = 0

    async def disconnected() -> bool:
        nonlocal polls
        polls += 1
        if polls == 2:
            store.append_event("A", "tool", "s", "late")
        return polls > 3

    async def collect() -> list[str]:
        return [c async for c in event_stream(store, "A", 1, disconnected, 0.01)]

    chunks = asyncio.run(collect())
    assert [parse(c)[0] for c in chunks] == [2, 3, 4]
    assert parse(chunks[-1])[1]["title"] == "late"


def test_stream_stops_when_the_client_is_gone(tmp_path: Path) -> None:
    store = Store(str(tmp_path / "claims.db"))

    async def disconnected() -> bool:
        return True

    async def collect() -> list[str]:
        return [c async for c in event_stream(store, "A", 0, disconnected, 0.01)]

    assert asyncio.run(collect()) == []


@pytest.fixture
def live_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A real server: the test client buffers a stream that never ends."""
    monkeypatch.setenv("CLAIMS_DB", str(tmp_path / "claims.db"))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(create_app(), port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.01)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(5)


def read_ids(
    url: str,
    n: int,
    headers: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
) -> list[int]:
    ids: list[int] = []
    with httpx.stream("GET", url, timeout=5, headers=headers, params=params) as res:
        assert res.headers["content-type"].startswith("text/event-stream")
        for line in res.iter_lines():
            if line.startswith("id: "):
                ids.append(int(line[4:]))
                if len(ids) == n:
                    break
    return ids


def test_endpoint_streams_with_last_event_id_and_after(live_url: str) -> None:
    claim = httpx.post(f"{live_url}/claims", json={"customer_id": "C-1001"}).json()
    cid = claim["id"]
    for _ in range(2):
        httpx.get(f"{live_url}/claims/{cid}/policies")
    url = f"{live_url}/claims/{cid}/events"

    assert read_ids(url, 2) == [1, 2]
    assert read_ids(url, 1, headers={"Last-Event-ID": "1"}) == [2]
    assert read_ids(url, 1, params={"after": "1"}) == [2]

    # a new event reaches a client that is already connected
    got: list[int] = []
    t = threading.Thread(
        target=lambda: got.extend(read_ids(url, 1, params={"after": "2"}))
    )
    t.start()
    time.sleep(0.2)
    httpx.get(f"{live_url}/claims/{cid}/policies")
    t.join(5)
    assert got == [3]


def test_events_of_an_unknown_claim_is_404(live_url: str) -> None:
    assert httpx.get(f"{live_url}/claims/nope/events").status_code == 404
