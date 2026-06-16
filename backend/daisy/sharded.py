"""Concurrent sharded fetch across a pool of DaisyBridge subprocesses.

A single ``DaisyBridge`` serializes its calls behind a lock (one subprocess,
one pipe), so a sequential loop over N accounts takes ~Σ(per-account api.cc
poll) — far too slow for the OTP live-tables' ~5s poll. The fix (used by both
``otp_fetch.fetch_bucket_otps`` and ``daisy_batches.batch_otps``) is to SHARD
the items across a small pool of bridges and run the shards concurrently, so
wall-time is ~ceil(N/pool)·per-poll instead of N·per-poll.

This module factors that pattern out (it was duplicated). ``sharded_gather``
owns the tricky parts — index-keyed sharding, ``gather(return_exceptions=True)``
so one shard whose BRIDGE fails to start doesn't abort the batch, and
ORDER-PRESERVING reassembly where every input item ALWAYS gets a result (no
silent drops). Each caller supplies its own per-shard worker (which fetches
through one bridge and catches per-item errors) and an error-row factory for a
whole-shard failure.
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")
R = TypeVar("R")

# How many bridges (subprocesses) to run concurrently. Capped so a big batch
# doesn't spawn a CustomerDaisy subprocess per item.
POOL_SIZE = 4

# A shard worker receives [(original_index, item), ...] and returns
# {original_index: result} — it MUST return a result for every index it was
# given (catching per-item errors itself), so the batch can't silently drop one.
ShardWorker = Callable[
    [list[tuple[int, T]]], Awaitable[dict[int, R]]]
# Builds the fallback result for an item whose whole SHARD failed (bridge died).
ShardErrorFactory = Callable[[T, BaseException], R]


async def sharded_gather(items: list[T], worker: ShardWorker,
                         on_shard_error: ShardErrorFactory, *,
                         pool_size: int = POOL_SIZE) -> list[R]:
    """Run ``worker`` over ``items`` sharded across ``pool_size`` bridges.

    Returns a list of results in the SAME ORDER as ``items`` — every item gets
    a result (a real one, or ``on_shard_error(item, exc)`` if its shard's bridge
    failed). Never raises for one shard's sake.
    """
    if not items:
        return []
    pool = min(pool_size, len(items))
    shards: list[list[tuple[int, T]]] = [[] for _ in range(pool)]
    for i, item in enumerate(items):
        shards[i % pool].append((i, item))

    results = await asyncio.gather(
        *(worker(shard) for shard in shards),
        return_exceptions=True)

    by_idx: dict[int, R] = {}
    for shard, result in zip(shards, results):
        if isinstance(result, BaseException):
            for idx, item in shard:
                by_idx[idx] = on_shard_error(item, result)
        else:
            by_idx.update(result)
    # Order-preserving — every index 0..N-1 is present (worker covers its shard,
    # on_shard_error covers a failed shard).
    return [by_idx[i] for i in range(len(items))]
