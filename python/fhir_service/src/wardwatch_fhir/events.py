"""In-process fan-out of live events to Server-Sent Event subscribers."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

SUBSCRIBER_QUEUE_SIZE = 1000


class EventBus:
    """Each subscriber gets its own bounded queue. A subscriber too slow to
    keep up loses its oldest events rather than stalling everyone else."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    @property
    def subscribers(self) -> int:
        return len(self._subscribers)

    def publish(self, event: dict[str, Any]) -> None:
        for queue in self._subscribers:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)
