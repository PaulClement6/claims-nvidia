"""Server-Sent Events of one claim: stored events first, then new ones."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable

from app.store import Store


async def event_stream(
    store: Store,
    claim_id: str,
    after: int,
    disconnected: Callable[[], Awaitable[bool]],
    interval: float = 0.3,
) -> AsyncIterator[str]:
    # ponytail: polls the store every 0.3 s per client, so a new event waits up to
    # 0.3 s and each open panel costs one query per tick. Fine for a demo; push from
    # the gateway (asyncio.Queue per claim) if clients or latency ever matter.
    while not await disconnected():
        for event in store.events_since(claim_id, after):
            after = event.sequence
            yield f"id: {event.sequence}\ndata: {event.model_dump_json()}\n\n"
        await asyncio.sleep(interval)
