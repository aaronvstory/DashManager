"""Slice 5: batch_otps parallel fetch — order preserved, shard failures graceful.

Stubs _fetch_shard so no CustomerDaisy/bridge is needed; verifies the
sharding + re-assembly (which is the part the parallelization changed).
"""
from backend import daisy_batches as B


def test_member_row_shape():
    row = B._member_row({"first_name": "Ada", "last_name": "Vance",
                         "email": "ada@x.net", "primary_phone": "5551110000"})
    assert row == {"name": "Ada Vance", "email": "ada@x.net",
                   "phone": "5551110000", "code": "", "error": ""}


def test_member_row_falls_back_to_phone_and_unnamed():
    row = B._member_row({"phone": "5552220000"})
    assert row["name"] == "(unnamed)" and row["phone"] == "5552220000"


async def test_fetch_members_empty():
    assert await B._fetch_members([], None) == []


async def test_fetch_members_preserves_order(monkeypatch):
    members = [{"first_name": f"C{i}", "email": f"c{i}@x.net"}
               for i in range(7)]

    async def fake_shard(shard, daisy_root):
        # echo a code = the original index so we can assert ordering
        out = {}
        for idx, rec in shard:
            row = B._member_row(rec)
            row["code"] = str(idx)
            out[idx] = row
        return out

    monkeypatch.setattr(B, "_fetch_shard", fake_shard)
    monkeypatch.setattr(B, "POOL_SIZE", 3)
    rows = await B._fetch_members(members, None)
    assert [r["code"] for r in rows] == [str(i) for i in range(7)]  # in order
    assert [r["email"] for r in rows] == [f"c{i}@x.net" for i in range(7)]


async def test_fetch_members_degrades_failed_shard(monkeypatch):
    members = [{"first_name": f"C{i}", "email": f"c{i}@x.net"}
               for i in range(6)]
    calls = {"n": 0}

    async def flaky_shard(shard, daisy_root):
        calls["n"] += 1
        if calls["n"] == 1:           # first shard's bridge "fails to start"
            raise RuntimeError("bridge down")
        return {idx: B._member_row(rec) for idx, rec in shard}

    monkeypatch.setattr(B, "_fetch_shard", flaky_shard)
    monkeypatch.setattr(B, "POOL_SIZE", 2)
    rows = await B._fetch_members(members, None)
    assert len(rows) == 6                       # every member still gets a row
    assert any("bridge failed" in r["error"] for r in rows)
    assert all("code" in r for r in rows)
