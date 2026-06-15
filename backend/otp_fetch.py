"""On-demand OTP fetch for a bucket — for logging customers in by hand (phone).

OTP codes expire in ~30s, so they can't live in the static daily report; this
fetches the LATEST code live from api.cc (via the existing DaisyBridge) for every
customer in a bucket and prints a clean name -> code table you read right then.

Usage (prints a table for today's bucket, or a given date):
    python -m backend.otp_fetch
    python -m backend.otp_fetch 2026-06-12
    python -m backend.otp_fetch --ids 3,5,7

Each customer row already stores the api.cc handle (number_token, api_url,
mirror_hosts) captured at creation, so no extra wiring is needed.
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from typing import Any

from backend import db
from backend.daisy.bridge import DaisyBridge, DaisyError


async def fetch_bucket_otps(bucket_date: str | None = None,
                            customer_ids: list[int] | None = None,
                            ) -> list[dict[str, Any]]:
    """Fetch the latest OTP for each selected customer. Pure data, no printing.

    Selects by `customer_ids` if given, else by `bucket_date` (default today).
    Returns rows: {id, name, phone, code, error}. A customer with no rented
    number, or whose code isn't available yet, gets code="" and an error note —
    never raises for one customer's sake.
    """
    bucket_date = bucket_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    customers = await db.list_customers()
    if customer_ids:
        wanted = {int(i) for i in customer_ids}
        customers = [c for c in customers if c["id"] in wanted]
    else:
        customers = [c for c in customers if c["bucket_date"] == bucket_date]

    if not customers:
        return []

    # A single DaisyBridge serializes its calls behind an internal lock (one
    # subprocess, one pipe), so a sequential loop over a full bucket takes
    # ~Σ(per-customer api.cc poll) — measured ~28s for 8 customers, far too slow
    # for the live-table's ~5s poll. Shard the customers across a SMALL pool of
    # bridges (each its own subprocess) and run them concurrently, so wall-time
    # is ~ceil(N/pool)·per-poll instead of N·per-poll. Pool is capped so we
    # don't spawn a CustomerDaisy subprocess per customer.
    pool = min(POOL_SIZE, len(customers))
    shards: list[list[dict[str, Any]]] = [[] for _ in range(pool)]
    for i, c in enumerate(customers):
        shards[i % pool].append(c)

    results = await asyncio.gather(
        *(_fetch_shard(shard) for shard in shards))

    # Re-interleave so the output order matches the input order.
    by_id = {r["id"]: r for shard_rows in results for r in shard_rows}
    return [by_id[c["id"]] for c in customers if c["id"] in by_id]


POOL_SIZE = 4


async def _fetch_shard(customers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fetch OTPs for one shard of customers through a single bridge.

    One bridge per shard (a subprocess); calls within a shard are sequential
    (the bridge serializes them anyway). Returns row dicts; one customer's
    failure never aborts the shard.
    """
    rows: list[dict[str, Any]] = []
    if not customers:
        return rows
    async with DaisyBridge() as daisy:
        for c in customers:
            name = f"{c.get('first_name', '')} {c.get('last_name', '')}".strip()
            row = {"id": c["id"], "name": name or "(unnamed)",
                   "phone": c.get("phone") or "—", "code": "", "error": ""}
            token = c.get("number_token") or ""
            if not token:
                row["error"] = "no rented number on file"
                rows.append(row)
                continue
            try:
                hosts = _loads_list(c.get("mirror_hosts"))
                res = await daisy.fetch_otp(token, c.get("api_url") or "", hosts)
                row["code"] = (res or {}).get("code") or ""
                if not row["code"]:
                    row["error"] = "no code yet (trigger the send, then retry)"
            except DaisyError as exc:
                row["error"] = f"fetch failed: {exc}"
            except Exception as exc:  # never let one customer abort the batch
                row["error"] = f"{type(exc).__name__}: {exc}"
            rows.append(row)
    return rows


def _loads_list(v: Any) -> list[str]:
    if isinstance(v, list):
        return v
    try:
        out = json.loads(v) if v else []
        return out if isinstance(out, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def format_table(rows: list[dict[str, Any]]) -> str:
    """Render the rows as a compact monospace table for the terminal."""
    if not rows:
        return "No customers in that bucket."
    w_name = max(4, max(len(r["name"]) for r in rows))
    w_phone = max(5, max(len(r["phone"]) for r in rows))
    head = f"{'NAME':<{w_name}}  {'PHONE':<{w_phone}}  {'CODE':<8}  NOTE"
    lines = [head, "-" * len(head)]
    for r in rows:
        code = r["code"] or "·····"
        lines.append(f"{r['name']:<{w_name}}  {r['phone']:<{w_phone}}  "
                     f"{code:<8}  {r['error']}")
    return "\n".join(lines)


def _parse_args(argv: list[str]) -> tuple[str | None, list[int] | None]:
    date_arg: str | None = None
    ids: list[int] | None = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--ids" and i + 1 < len(argv):
            ids = [int(x) for x in argv[i + 1].split(",") if x.strip()]
            i += 2
            continue
        if not a.startswith("-"):
            date_arg = a
        i += 1
    return date_arg, ids


async def _main(argv: list[str]) -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    db.init_db()
    date_arg, ids = _parse_args(argv)
    rows = await fetch_bucket_otps(date_arg, ids)
    print(format_table(rows))


if __name__ == "__main__":
    asyncio.run(_main(sys.argv[1:]))
