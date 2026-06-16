"""CustomerDaisy batch OTP access — for grabbing OTPs of accounts CLAUDE created
in a batch (named "<label> - claude"), so they can be used for mobile login.

The batch-created accounts are saved into CustomerDaisy (not DashManager's
customers table), each stamped with apicc_batch_id / apicc_batch_label and the
apicc_* number handle. This module reads recent CustomerDaisy records, groups
them by batch, and fetches the latest live OTP per account through DaisyBridge —
the same non-blocking single-pass model as otp_fetch (api.cc codes expire ~30s).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from backend.daisy.bridge import DaisyBridge, DaisyError

# OTPs are fetched concurrently across a SMALL pool of bridges — one bridge
# serializes its calls behind a lock, so a sequential loop over a 6-10 account
# batch is ~Σ(per-account api.cc poll) and far too slow for the ~5s live poll.
POOL_SIZE = 4


def _rec_batch(rec: dict[str, Any]) -> tuple[str, str]:
    """(batch_id, batch_label) for a CustomerDaisy record, or ('','')."""
    meta = rec.get("metadata") or {}
    bid = meta.get("apicc_batch_id") or rec.get("apicc_batch_id") or ""
    label = (meta.get("apicc_batch_label") or rec.get("apicc_batch_label")
             or "")
    return str(bid), str(label)


def _rec_token(rec: dict[str, Any]) -> tuple[str, str, list[str]]:
    """(number_token, api_url, mirror_hosts) from a CustomerDaisy record."""
    meta = rec.get("metadata") or {}
    token = (meta.get("apicc_number_token") or rec.get("number_token") or "")
    api_url = (meta.get("apicc_api_url") or rec.get("api_url") or "")
    hosts = meta.get("apicc_mirror_hosts") or rec.get("mirror_hosts") or []
    if not isinstance(hosts, list):
        hosts = []
    return token, api_url, hosts


async def list_batches(*, daisy_root: str | None = None, limit: int = 80
                       ) -> list[dict[str, Any]]:
    """List CustomerDaisy batches (most recent first), with account counts.

    Only returns batches that have a label (the "<label> - claude" naming).
    Each entry: {batch_id, batch_label, count, accounts:[{name,email,phone}]}.
    """
    async with DaisyBridge(root=daisy_root) as daisy:
        recents = await daisy.list_recent_customers(limit)
    groups: dict[str, dict[str, Any]] = {}
    for rec in recents:
        bid, label = _rec_batch(rec)
        if not label:
            continue
        key = bid or label
        g = groups.setdefault(key, {"batch_id": bid, "batch_label": label,
                                    "count": 0, "accounts": []})
        name = f"{rec.get('first_name', '')} {rec.get('last_name', '')}".strip()
        g["accounts"].append({
            "name": name or "(unnamed)",
            "email": rec.get("email") or "",
            "phone": (rec.get("primary_phone") or rec.get("phone") or ""),
            "customer_id": rec.get("customer_id") or "",
        })
        g["count"] += 1
    return list(groups.values())


async def batch_otps(batch_id: str | None = None, batch_label: str | None = None,
                     *, daisy_root: str | None = None, limit: int = 80
                     ) -> dict[str, Any]:
    """Latest live OTP for each account in a batch (non-blocking single pass).

    Matches by batch_id if given, else batch_label. Returns
    {rows:[{name,email,phone,code,error}], fetched_at}. One account's failure
    never aborts the batch.
    """
    # One bridge to resolve the batch members (cheap), then fetch their OTPs
    # CONCURRENTLY across a pool of bridges.
    async with DaisyBridge(root=daisy_root) as daisy:
        recents = await daisy.list_recent_customers(limit)
    members = []
    for rec in recents:
        bid, label = _rec_batch(rec)
        if batch_id and bid == batch_id:
            members.append(rec)
        elif batch_label and label == batch_label and not batch_id:
            members.append(rec)

    rows = await _fetch_members(members, daisy_root)
    return {"rows": rows,
            "fetched_at": datetime.now(timezone.utc).isoformat()}


def _member_row(rec: dict[str, Any]) -> dict[str, Any]:
    """The base row for one batch member (no code yet)."""
    name = f"{rec.get('first_name', '')} {rec.get('last_name', '')}".strip()
    return {"name": name or "(unnamed)", "email": rec.get("email") or "",
            "phone": (rec.get("primary_phone") or rec.get("phone") or ""),
            "code": "", "error": ""}


async def _fetch_members(members: list[dict[str, Any]],
                         daisy_root: str | None) -> list[dict[str, Any]]:
    """Fetch the latest OTP for each member, CONCURRENTLY, order preserved.

    Shards the members across POOL_SIZE bridges (each its own subprocess; a
    single bridge serializes its calls behind a lock). One member's failure —
    or a whole shard's bridge failing to start — never aborts the batch.
    """
    if not members:
        return []
    pool = min(POOL_SIZE, len(members))
    shards: list[list[tuple[int, dict[str, Any]]]] = [[] for _ in range(pool)]
    for i, rec in enumerate(members):
        shards[i % pool].append((i, rec))

    results = await asyncio.gather(
        *(_fetch_shard(shard, daisy_root) for shard in shards),
        return_exceptions=True)

    by_idx: dict[int, dict[str, Any]] = {}
    for shard, result in zip(shards, results):
        if isinstance(result, BaseException):
            for idx, rec in shard:
                row = _member_row(rec)
                row["error"] = f"bridge failed: {result}"
                by_idx[idx] = row
        else:
            by_idx.update(result)
    return [by_idx[i] for i in range(len(members))]


async def _fetch_shard(shard: list[tuple[int, dict[str, Any]]],
                       daisy_root: str | None) -> dict[int, dict[str, Any]]:
    """Fetch OTPs for one shard of members through a single bridge. Returns
    {original_index: row}. One member's failure never aborts the shard."""
    out: dict[int, dict[str, Any]] = {}
    if not shard:
        return out
    async with DaisyBridge(root=daisy_root) as daisy:
        for idx, rec in shard:
            row = _member_row(rec)
            token, api_url, hosts = _rec_token(rec)
            if not token:
                row["error"] = "no number token on file"
                out[idx] = row
                continue
            try:
                res = await daisy.fetch_otp(token, api_url, hosts)
                row["code"] = (res or {}).get("code") or ""
                if not row["code"]:
                    row["error"] = "no code yet (trigger send, then retry)"
            except DaisyError as exc:
                row["error"] = f"fetch failed: {exc}"
            except Exception as exc:  # one member never aborts the shard
                row["error"] = f"{type(exc).__name__}: {exc}"
            out[idx] = row
    return out
