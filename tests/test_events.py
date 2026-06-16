"""EventBus behavior: delivery, ids, replay, ring trim.

Uses fresh EventBus instances, never the shared global ``bus``.
"""
from __future__ import annotations

import asyncio
from typing import Any

from backend.events import EventBus


async def test_publish_subscribe_delivery():
    bus = EventBus()
    q = bus.subscribe()

    published = bus.publish("log", {"msg": "hi"}, run_id=7)
    got = await asyncio.wait_for(q.get(), timeout=1)
    assert got is published
    assert got["type"] == "log"
    assert got["data"] == {"msg": "hi"}
    assert got["run_id"] == 7
    assert got["ts"]

    bus.unsubscribe(q)
    bus.publish("log", {"msg": "after unsubscribe"})
    assert q.empty()


def test_monotonic_ids():
    bus = EventBus()
    ids = [bus.publish("log")["id"] for _ in range(5)]
    assert ids == [1, 2, 3, 4, 5]


def test_replay_after():
    bus = EventBus()
    for i in range(5):
        bus.publish("log", {"i": i})

    assert [e["id"] for e in bus.replay_after(3)] == [4, 5]
    assert [e["id"] for e in bus.replay_after(0)] == [1, 2, 3, 4, 5]
    assert bus.replay_after(99) == []


def test_ring_buffer_trim():
    bus = EventBus(ring_size=3)
    for _ in range(5):
        bus.publish("log")

    # Oldest events fall off; ids keep counting monotonically.
    assert [e["id"] for e in bus.replay_after(0)] == [3, 4, 5]
    assert bus.publish("log")["id"] == 6


async def test_subscriber_queue_is_bounded():
    # Each subscriber gets a bounded queue so a stalled consumer can't grow it
    # without limit.
    bus = EventBus(sub_queue_size=4)
    q = bus.subscribe()
    assert q.maxsize == 4


async def test_full_subscriber_queue_drops_without_blocking_or_raising():
    # A subscriber that never drains overflows its bounded queue. publish() must
    # NOT raise or block — it drops the overflow for that subscriber (the ring
    # buffer recovers it on reconnect). And publish keeps its monotonic id.
    bus = EventBus(sub_queue_size=2)
    bus.subscribe()                       # never drained — will fill + overflow
    for _ in range(10):
        ev = bus.publish("log")           # must not raise QueueFull
    assert ev["id"] == 10                 # all 10 still counted in the ring
    # the dropped events are still replayable from the ring buffer.
    assert [e["id"] for e in bus.replay_after(0)][-1] == 10


async def test_full_queue_does_not_break_delivery_to_others():
    # One stalled subscriber (full queue) must NOT stop a healthy subscriber
    # from receiving events. The stalled one has a tiny bound and is never
    # drained; the healthy one is drained after each publish so it never
    # overflows and receives EVERY event.
    bus = EventBus(sub_queue_size=2)
    stalled = bus.subscribe()             # never drained -> overflows + drops
    healthy = bus.subscribe()
    got = []
    for _ in range(5):
        bus.publish("log")
        got.append((await healthy.get())["id"])   # drain healthy each time
    assert stalled.qsize() == 2           # stalled capped at its bound
    assert got == [1, 2, 3, 4, 5]         # healthy received EVERY event


async def test_unsubscribe_during_delivery_does_not_raise():
    # The SSE generator unsubscribes from its `finally` (on client disconnect),
    # which can fire while _deliver is mid-iteration over the subscriber set. If
    # _deliver iterated the live set, that discard would raise "set changed size
    # during iteration". A subscriber whose queue-put unsubscribes a SECOND
    # subscriber forces exactly that interleaving — it must NOT raise.
    bus = EventBus()

    class UnsubscribingQueue(asyncio.Queue):
        def put_nowait(self, item):
            # On first delivery, yank the other subscriber out from under the
            # in-progress iteration, the way a disconnect's `finally` would.
            if victim["q"] is not None:
                bus.unsubscribe(victim["q"])
                victim["q"] = None
            super().put_nowait(item)

    victim: dict[str, Any] = {"q": bus.subscribe()}  # discarded mid-fan-out
    bus._subs.add(UnsubscribingQueue())  # triggers the discard during delivery
    bus.publish("log")                  # must not raise


async def test_publish_from_worker_thread_is_thread_safe():
    # publish() is called from worker THREADS too (signup_via_cdp runs under
    # asyncio.to_thread and emits progress). asyncio.Queue isn't thread-safe, so
    # an off-loop publish must marshal back onto the subscriber's loop via
    # call_soon_threadsafe rather than touch the queue directly. Here we publish
    # from a thread and confirm the event still arrives.
    bus = EventBus()
    q = bus.subscribe()                   # binds the bus to this loop

    def emit_from_thread() -> None:
        bus.publish("log", {"from": "thread"})

    await asyncio.to_thread(emit_from_thread)   # publish on a worker thread
    got = await asyncio.wait_for(q.get(), timeout=2)
    assert got["type"] == "log" and got["data"] == {"from": "thread"}
