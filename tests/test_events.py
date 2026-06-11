"""EventBus behavior: delivery, ids, replay, ring trim.

Uses fresh EventBus instances, never the shared global ``bus``.
"""
from __future__ import annotations

import asyncio

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
