"""In-process event bus feeding the SSE endpoint.

Monotonic ids + a ring buffer let a reconnecting EventSource replay missed
events via Last-Event-ID. Durable history lives in the DB; the buffer only
needs to cover reconnect gaps.
"""
from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
from typing import Any


class EventBus:
    def __init__(self, ring_size: int = 1000, sub_queue_size: int = 1000
                 ) -> None:
        self._subs: set[asyncio.Queue] = set()
        self._ring: deque[dict[str, Any]] = deque(maxlen=ring_size)
        self._next_id = 1
        # Per-subscriber queue bound. A subscriber whose SSE consumer stalls (or
        # silently died without us noticing) must NOT make publish() grow its
        # queue without limit (memory leak) or raise QueueFull and break delivery
        # to OTHER subscribers. So queues are bounded and publish DROPS on a full
        # one — the ring buffer + Last-Event-ID replay recovers the gap when that
        # client reconnects.
        self._sub_queue_size = sub_queue_size

    def publish(self, type: str, data: dict[str, Any] | None = None,
                run_id: int | None = None) -> dict[str, Any]:
        event = {
            "id": self._next_id,
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "run_id": run_id,
            "type": type,
            "data": data or {},
        }
        self._next_id += 1
        self._ring.append(event)
        for q in self._subs:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # A stalled/dead subscriber — drop this event for it (it can
                # replay from the ring on reconnect) rather than block, grow
                # unbounded, or break delivery to the others.
                pass
        return event

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._sub_queue_size)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def replay_after(self, last_id: int) -> list[dict[str, Any]]:
        return [e for e in self._ring if e["id"] > last_id]


bus = EventBus()
