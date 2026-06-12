"""Live SMS-code view — an auto-refreshing terminal table of OTPs.

Mirrors CustomerDaisy's "Live SMS Codes" screen so the user can read codes while
creating accounts in a real browser AND while logging each account into their
phone (which re-requests a fresh OTP). Polls api.cc (via DaisyBridge) every few
seconds for every selected account and prints Name / Phone / Email / Address /
ID / Code.

Select accounts by name (the ones being worked on) or by a DashManager bucket.
Runs until Ctrl-C. Pure terminal — no extra deps.

Usage:
    python -m backend.otp_live --names "Michelle Green,Jill Murphy"
    python -m backend.otp_live --bucket 2026-06-12
    python -m backend.otp_live              # all of today's recent api.cc accts
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from typing import Any

from backend.daisy.bridge import DaisyBridge

REFRESH_S = 4.0


async def _resolve_accounts(names: list[str] | None,
                            limit: int = 30) -> list[dict[str, Any]]:
    """Pick CustomerDaisy records to watch — by name, else most-recent."""
    async with DaisyBridge() as d:
        recents = await d.list_recent_customers(limit)
    if names:
        wanted = {n.strip().lower() for n in names}
        picked = [c for c in recents
                  if f"{c.get('first_name','')} {c.get('last_name','')}"
                  .strip().lower() in wanted]
    else:
        picked = recents
    # keep only rows with a usable token (others can't fetch a code)
    return [c for c in picked if c.get("number_token")]


def _short_id(c: dict[str, Any], idx: int) -> str:
    """A compact copy-paste id like CustomerDaisy's '06-12 1 Michelle'."""
    day = datetime.now(timezone.utc).strftime("%m-%d")
    return f"{day} {idx} {c.get('first_name','')}".strip()


def _render(rows: list[dict[str, Any]], codes: dict[str, str],
            tick: int) -> str:
    """Build the table string (pure-ish; takes current codes)."""
    name_w = max(4, max((len(r["name"]) for r in rows), default=4))
    phone_w = max(5, max((len(r["phone"]) for r in rows), default=5))
    addr_w = max(7, min(34, max((len(r["address"]) for r in rows), default=7)))
    id_w = max(2, max((len(r["id"]) for r in rows), default=2))
    spin = "|/-\\"[tick % 4]
    head = (f"  Live SMS Codes  {spin}\n\n"
            f"{'NAME':<{name_w}}  {'PHONE':<{phone_w}}  "
            f"{'ADDRESS':<{addr_w}}  {'ID':<{id_w}}  CODE")
    lines = [head, "-" * (len(head.split(chr(10))[-1]) + 6)]
    for r in rows:
        code = codes.get(r["token"], "") or "······"
        addr = r["address"][:addr_w]
        lines.append(f"{r['name']:<{name_w}}  {r['phone']:<{phone_w}}  "
                     f"{addr:<{addr_w}}  {r['id']:<{id_w}}  {code}")
    lines.append("")
    lines.append("Ctrl-C to quit · codes refresh every "
                 f"{int(REFRESH_S)}s")
    return "\n".join(lines)


async def run_live(names: list[str] | None = None) -> None:
    accts = await _resolve_accounts(names)
    if not accts:
        print("No matching accounts with rentable numbers found.")
        return

    rows = []
    for i, c in enumerate(accts, 1):
        rows.append({
            "name": f"{c.get('first_name','')} {c.get('last_name','')}".strip()
                    or "(unnamed)",
            "phone": c.get("phone") or "—",
            "email": c.get("email") or "—",
            "address": c.get("full_address") or "—",
            "id": _short_id(c, i),
            "token": c.get("number_token"),
            "api_url": c.get("api_url") or "",
            "hosts": c.get("mirror_hosts") or [],
        })

    codes: dict[str, str] = {}
    tick = 0
    async with DaisyBridge() as d:
        while True:
            for r in rows:
                try:
                    res = await d.fetch_otp(r["token"], r["api_url"],
                                            r["hosts"])
                    code = (res or {}).get("code") or ""
                    if code:
                        codes[r["token"]] = code
                except Exception:
                    pass  # transient — keep last known code
            # clear screen + redraw
            sys.stdout.write("\033[2J\033[H")
            sys.stdout.write(_render(rows, codes, tick))
            sys.stdout.flush()
            tick += 1
            await asyncio.sleep(REFRESH_S)


def main() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    ap = argparse.ArgumentParser()
    ap.add_argument("--names", default="",
                    help="comma-separated full names to watch")
    args = ap.parse_args()
    names = [n for n in args.names.split(",") if n.strip()] or None
    try:
        asyncio.run(run_live(names))
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
