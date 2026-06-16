"""Unit tests for the shared sharded_gather helper (backend.daisy.sharded).

The shard/gather/order-preserve/graceful-degrade mechanics that otp_fetch and
daisy_batches both rely on — tested once, here.
"""
from backend.daisy import sharded


async def test_empty():
    async def worker(shard):
        return {}
    assert await sharded.sharded_gather([], worker, lambda i, e: i) == []


async def test_preserves_order_across_shards(monkeypatch):
    monkeypatch.setattr(sharded, "POOL_SIZE", 3)
    items = list(range(10))

    async def worker(shard):
        # echo each item's value, keyed by its original index
        return {idx: f"r{item}" for idx, item in shard}

    out = await sharded.sharded_gather(items, worker, lambda i, e: "ERR")
    assert out == [f"r{i}" for i in range(10)]   # exact input order


async def test_failed_shard_degrades_only_its_items(monkeypatch):
    monkeypatch.setattr(sharded, "POOL_SIZE", 2)
    items = list(range(6))
    calls = {"n": 0}

    async def worker(shard):
        calls["n"] += 1
        if calls["n"] == 1:                  # first shard's bridge dies
            raise RuntimeError("boom")
        return {idx: f"ok{item}" for idx, item in shard}

    out = await sharded.sharded_gather(
        items, worker, lambda item, exc: f"ERR{item}:{exc}")
    # every item present, in order; shard-0 items (even indices, round-robin)
    # carry the error, the rest are ok
    assert len(out) == 6
    errs = [x for x in out if str(x).startswith("ERR")]
    oks = [x for x in out if str(x).startswith("ok")]
    assert errs and oks
    assert "boom" in errs[0]


async def test_per_item_error_stays_in_worker(monkeypatch):
    # a worker that returns an error result for one item (catching it itself)
    # must NOT fail the whole shard.
    monkeypatch.setattr(sharded, "POOL_SIZE", 1)

    async def worker(shard):
        out = {}
        for idx, item in shard:
            out[idx] = "bad" if item == 2 else f"ok{item}"
        return out

    out = await sharded.sharded_gather([0, 1, 2, 3], worker, lambda i, e: "X")
    assert out == ["ok0", "ok1", "bad", "ok3"]


async def test_pool_capped_to_item_count():
    # fewer items than POOL_SIZE → still works (pool = min(POOL_SIZE, N)).
    async def worker(shard):
        return {idx: item for idx, item in shard}
    assert await sharded.sharded_gather([42], worker, lambda i, e: 0) == [42]
