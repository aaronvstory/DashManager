"""In-process event bus feeding the SSE endpoint.

Monotonic ids + a ring buffer let a reconnecting EventSource replay missed
events via Last-Event-ID. Durable history lives in the DB; the buffer only
needs to cover reconnect gaps.
"""
from __future__ import annotations

import asyncio
import itertools
from collections import deque
from datetime import datetime, timezone
from typing import Any


class EventBus:
    def __init__(self, ring_size: int = 1000, sub_queue_size: int = 1000
                 ) -> None:
        self._subs: set[asyncio.Queue] = set()
        self._ring: deque[dict[str, Any]] = deque(maxlen=ring_size)
        # itertools.count, not `self._next_id += 1`: publish() runs from worker
        # THREADS too, and a bare `+= 1` is a 3-bytecode read-modify-write the
        # GIL can yield between (dup/skip an id). count().__next__ is a single
        # C-level atomic-under-GIL call — safe without a lock.
        self._ids = itertools.count(1)
        # Per-subscriber queue bound. A subscriber whose SSE consumer stalls (or
        # silently died without us noticing) must NOT make publish() grow its
        # queue without limit (memory leak) or raise QueueFull and break delivery
        # to OTHER subscribers. So queues are bounded and publish DROPS on a full
        # one — the ring buffer + Last-Event-ID replay recovers the gap when that
        # client reconnects, AS LONG AS the gap fits within ring_size (a burst
        # that overflows both the queue AND the ring loses the oldest events;
        # durable history lives in the DB, the ring only covers reconnect gaps).
        self._sub_queue_size = sub_queue_size
        # The loop the subscriber queues live on (set on first subscribe). publish
        # MUST touch those asyncio.Queues only from this loop's thread — see
        # publish(). None until something subscribes.
        self._loop: asyncio.AbstractEventLoop | None = None

    def _deliver(self, event: dict[str, Any]) -> None:
        """Fan an event out to the subscriber queues. Runs ON the loop thread."""
        # Snapshot first: a subscriber's SSE generator unsubscribes from its
        # `finally` (on client disconnect), and that `discard` can land while
        # we're mid-iteration here — "set changed size during iteration". The
        # list() copy is a single atomic-under-GIL call; a just-discarded queue
        # gets one harmless extra put_nowait on the way out (nobody reads it).
        for q in list(self._subs):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # A stalled/dead subscriber — drop this event for it (it can
                # replay from the ring on reconnect) rather than block, grow
                # unbounded, or break delivery to the others.
                pass

    def publish(self, type: str, data: dict[str, Any] | None = None,
                run_id: int | None = None) -> dict[str, Any]:
        event = {
            "id": next(self._ids),       # atomic-under-GIL across threads
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "run_id": run_id,
            "type": type,
            "data": data or {},
        }
        self._ring.append(event)         # deque.append is atomic — fine off-loop
        # asyncio.Queue is NOT thread-safe. publish() is called from worker
        # THREADS too (e.g. the SYNC signup_via_cdp runs under asyncio.to_thread
        # and emits progress). Touching the subscriber queues from a non-loop
        # thread can corrupt the loop, so when we're off the owning loop we hop
        # back onto it via call_soon_threadsafe; on the loop we deliver inline.
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is not None:
            # On a loop: track it (so a later off-loop publish knows where to
            # marshal) and deliver inline.
            self._loop = running
            self._deliver(event)
            return event
        # Off-loop (a worker THREAD, e.g. the sync signup running under
        # asyncio.to_thread). asyncio.Queue isn't thread-safe, so hop onto the
        # bound loop via call_soon_threadsafe. If there's no live bound loop
        # (none yet, or it's closed/stopped — subscribers gone with it), drop:
        # a thread can't safely touch the queues and there's nothing to deliver.
        # ORDERING: ids stay strictly monotonic (assigned above, atomically), but
        # *delivery* order is not guaranteed across this boundary — an off-loop
        # event scheduled here runs after whatever's already queued on the loop,
        # so it can reach a subscriber AFTER an on-loop event with a higher id.
        # That's fine: the ring + Last-Event-ID replay reconciles by id, and SSE
        # consumers key on id, not arrival order.
        loop = self._loop
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(self._deliver, event)
            except RuntimeError:
                pass
        return event

    def subscribe(self) -> asyncio.Queue:
        # Remember the loop these queues belong to so an off-loop publish() (a
        # worker thread) can marshal back onto it. publish() also refreshes this
        # on every on-loop call, so it always tracks the live loop.
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        q: asyncio.Queue = asyncio.Queue(maxsize=self._sub_queue_size)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def replay_after(self, last_id: int) -> list[dict[str, Any]]:
        return [e for e in self._ring if e["id"] > last_id]


bus = EventBus()
