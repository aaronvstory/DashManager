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
    def __init__(self, ring_size: int = 1000) -> None:
        self._subs: set[asyncio.Queue] = set()
        self._ring: deque[dict[str, Any]] = deque(maxlen=ring_size)
        self._next_id = 1

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
            q.put_nowait(event)
        return event

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def replay_after(self, last_id: int) -> list[dict[str, Any]]:
        return [e for e in self._ring if e["id"] > last_id]


bus = EventBus()
