"""CustomerDaisy batch OTP access — for grabbing OTPs of accounts CLAUDE created
in a batch (named "<label> - claude"), so they can be used for mobile login.

The batch-created accounts are saved into CustomerDaisy (not DashManager's
customers table), each stamped with apicc_batch_id / apicc_batch_label and the
apicc_* number handle. This module reads recent CustomerDaisy records, groups
them by batch, and fetches the latest live OTP per account through DaisyBridge —
the same non-blocking single-pass model as otp_fetch (api.cc codes expire ~30s).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.daisy.bridge import DaisyBridge, DaisyError


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
    async with DaisyBridge(root=daisy_root) as daisy:
        recents = await daisy.list_recent_customers(limit)
        members = []
        for rec in recents:
            bid, label = _rec_batch(rec)
            if batch_id and bid == batch_id:
                members.append(rec)
            elif batch_label and label == batch_label and not batch_id:
                members.append(rec)
        rows: list[dict[str, Any]] = []
        for rec in members:
            name = f"{rec.get('first_name', '')} " \
                   f"{rec.get('last_name', '')}".strip()
            row = {"name": name or "(unnamed)", "email": rec.get("email") or "",
                   "phone": (rec.get("primary_phone") or rec.get("phone") or ""),
                   "code": "", "error": ""}
            token, api_url, hosts = _rec_token(rec)
            if not token:
                row["error"] = "no number token on file"
                rows.append(row)
                continue
            try:
                res = await daisy.fetch_otp(token, api_url, hosts)
                row["code"] = (res or {}).get("code") or ""
                if not row["code"]:
                    row["error"] = "no code yet (trigger send, then retry)"
            except DaisyError as exc:
                row["error"] = f"fetch failed: {exc}"
            except Exception as exc:
                row["error"] = f"{type(exc).__name__}: {exc}"
            rows.append(row)
    return {"rows": rows,
            "fetched_at": datetime.now(timezone.utc).isoformat()}
