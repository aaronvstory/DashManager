"""One-command refund run — detect → self-claim → verify → report.

Wraps the gotchas that otherwise get rediscovered every session:
  - RunManager.start() returns immediately and fires a background asyncio.Task,
    so you MUST `await rm._task` or the run exits half-done. Handled here.
  - The Windows Proactor event-loop policy must be set before Playwright. Done.
  - Card-based pending_claim orders carry a synthetic ``pendingclaim:store:N``
    UUID and no receipt URL; after a claim the auto-verify can flag them
    `manual`/`unconfirmed` even when they worked. This module re-scrapes each
    such customer, reopens the real receipt, and promotes to `refunded` ONLY
    when it positively reads a ``Refund -$X`` line (zero-tolerance — see
    [[dashmanager-zero-tolerance-gate]]).

Usage (always inside the venv, run as a module — never the uvicorn CLI):

    .venv\\Scripts\\python.exe -m backend.refund_run detect  --bucket 2026-06-15
    .venv\\Scripts\\python.exe -m backend.refund_run claim   --bucket 2026-06-15
    .venv\\Scripts\\python.exe -m backend.refund_run all     --bucket 2026-06-15
    .venv\\Scripts\\python.exe -m backend.refund_run status  --bucket 2026-06-15

    # scope by ids instead of a whole bucket:
    .venv\\Scripts\\python.exe -m backend.refund_run claim --ids 17,20,21

`detect`  = scrape + classify only (no claims/chats).
`claim`   = detect, then self-claim pending_claim orders, then verify by receipt.
`all`     = claim + (re)build the daily report. The everyday command.
`status`  = print the current DB state for the scope (no browser).

Live support CHAT is intentionally NOT automated here — that is the
judgment-heavy, Opus-only step. This module resolves everything that needs no
agent; whatever remains `not_refunded`/`credits-issued`/`unconfirmed` after it
runs is what a human (on Opus) takes to chat. It NEVER marks an order
`refunded` without a receipt-proven Refund line.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

if sys.platform == "win32":  # Playwright needs subprocess support on Windows
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from backend import db  # noqa: E402  (after the event-loop policy)
from backend.models import RefundStatus  # noqa: E402

ORDERS_URL = "https://www.doordash.com/orders"

# Receipt amount tolerance when matching a claimed order to its fresh-scrape
# twin (the synthetic-UUID case): same store, price within a few cents.
_PRICE_TOL = 0.50


# ── scope ────────────────────────────────────────────────────────────────────

async def _scope_customers(bucket: str | None,
                           ids: list[int] | None) -> list[dict]:
    customers = await db.list_customers()
    if ids:
        wanted = set(ids)
        return [c for c in customers if c["id"] in wanted]
    if bucket:
        return [c for c in customers if c.get("bucket_date") == bucket]
    return []


def _scope_dict(bucket: str | None, ids: list[int] | None) -> dict:
    if ids:
        return {"customer_ids": ids}
    return {"bucket_date": bucket}


# ── run a RunManager pass to completion (the await-the-task gotcha) ───────────

async def _run_pass(scope: dict, strategy: str, headless: bool) -> int:
    """Start a RunManager pass and AWAIT its background task to completion."""
    from backend.runner import RunManager
    rm = RunManager()
    run_id = await rm.start(scope, strategy, headless=headless)
    if rm._task is not None:
        await rm._task
    return run_id


# ── status print ─────────────────────────────────────────────────────────────

_SYMBOL = {
    "refunded": "✅", "pending_claim": "🟡", "not_refunded": "❌",
    "credits_issued": "💳", "unconfirmed": "⚠", "partial": "◐",
    "remake": "🔁", "unchecked": "❓", "unknown": "❓",
}


async def _print_status(customers: list[dict]) -> dict[str, int]:
    totals: dict[str, int] = {}
    grand = 0.0
    for c in sorted(customers, key=lambda r: r["id"]):
        orders = await db.list_orders(c["id"])
        name = f"{c.get('first_name','')} {c.get('last_name','')}".strip()
        if not orders:
            print(f"  [{c['id']}] {name} — 0 orders")
            continue
        print(f"  [{c['id']}] {name} — {len(orders)} orders")
        for o in orders:
            st = o.get("refund_status") or "unchecked"
            amt = o.get("refund_amount")
            price = o.get("price")
            shown = amt if amt is not None else price
            if st == "refunded" and amt is not None:
                grand += float(amt)
            sym = _SYMBOL.get(st, "?")
            money = f"${shown:.2f}" if isinstance(shown, (int, float)) else "?"
            print(f"      {sym} {st:<13} {money:>9}  {o.get('store_name','')}")
            totals[st] = totals.get(st, 0) + 1
    print("\n  === SUMMARY ===")
    for k, v in sorted(totals.items()):
        print(f"    {_SYMBOL.get(k,'?')} {k}: {v}")
    print(f"    💰 confirmed-to-card total: ${grand:.2f}")
    return totals


# ── post-claim verification (the synthetic-UUID gotcha) ──────────────────────

async def _verify_unconfirmed(customers: list[dict], headless: bool) -> int:
    """For each customer with an `unconfirmed`/`pending_claim` order, re-scrape,
    reopen the real receipt, and promote to `refunded` ONLY on a proven Refund
    line. Returns the count promoted. Pure zero-tolerance: no Refund line ⇒ no
    promotion (left for a human / chat).
    """
    from playwright.async_api import async_playwright
    from backend.browser.driver import customer_profile, handle_cloudflare
    from backend.browser.orders import scrape_orders, open_receipt
    from backend.browser.refund_detector import detect

    cfg = await db.get_setting("refund_signal")
    promoted = 0

    needs = []
    for c in customers:
        orders = await db.list_orders(c["id"])
        if any((o.get("refund_status") in ("unconfirmed", "pending_claim"))
               for o in orders):
            needs.append(c)
    if not needs:
        return 0

    async with async_playwright() as p:
        for c in needs:
            cid = c["id"]
            db_orders = await db.list_orders(cid)
            pend = [o for o in db_orders
                    if o.get("refund_status") in ("unconfirmed", "pending_claim")]
            try:
                async with customer_profile(
                        p, cid, headless=headless, viewport=(1200, 720)) as ctx:
                    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                    await page.goto(ORDERS_URL, wait_until="domcontentloaded")
                    await handle_cloudflare(page)
                    await asyncio.sleep(2.5)
                    fresh = await scrape_orders(page)

                    for dbo in pend:
                        twin = _match_twin(dbo, fresh)
                        if twin is None or not twin.receipt_url:
                            print(f"  [{cid}] {dbo.get('store_name','')} "
                                  f"${dbo.get('price')}: no receipt yet — "
                                  f"left {dbo.get('refund_status')}")
                            continue
                        text = await open_receipt(page, twin.receipt_url)
                        rr = detect(text, cfg)
                        if rr.status in (RefundStatus.refunded,
                                         RefundStatus.partial):
                            # Promote: ensure the order row carries the REAL uuid
                            # (synthetic pendingclaim:* rows get superseded).
                            oid = await db.upsert_order(
                                customer_id=cid, order_uuid=twin.order_uuid,
                                receipt_url=twin.receipt_url,
                                store_name=twin.store_name, price=twin.price)
                            amt = rr.refund_amount or twin.price
                            await db.update_order_refund(
                                oid, RefundStatus.refunded.value, amt, amt)
                            promoted += 1
                            print(f"  [{cid}] {twin.store_name} ${twin.price}: "
                                  f"✅ receipt-proven refund → refunded")
                        else:
                            print(f"  [{cid}] {twin.store_name} ${twin.price}: "
                                  f"no Refund line → left {dbo.get('refund_status')}"
                                  f" (needs chat/human)")
            except Exception as exc:  # one bad customer ≠ kill the pass
                print(f"  [{cid}] verify failed: {type(exc).__name__}: {exc}")
    return promoted


def _match_twin(dbo: dict, fresh: list):
    """Find the fresh-scrape order matching a stored (possibly synthetic-UUID)
    one: exact uuid if real, else same store + the CLOSEST price within
    tolerance. Closest (not first) matters when a customer has two orders only
    cents apart — picking the wrong twin could promote the wrong order."""
    uuid = dbo.get("order_uuid", "")
    if uuid and not uuid.startswith("pendingclaim:"):
        for o in fresh:
            if o.order_uuid == uuid:
                return o
    price = dbo.get("price")
    store = (dbo.get("store_name") or "").lower()
    if price is None:
        return None
    best, best_delta = None, _PRICE_TOL
    for o in fresh:
        if o.price is None or (o.store_name or "").lower() != store:
            continue
        delta = abs(o.price - price)
        if delta < best_delta:
            best, best_delta = o, delta
    return best


# ── commands ─────────────────────────────────────────────────────────────────

async def cmd_status(bucket, ids):
    db.init_db()
    customers = await _scope_customers(bucket, ids)
    print(f"=== status ({len(customers)} customers) ===")
    await _print_status(customers)


async def cmd_detect(bucket, ids, headless):
    db.init_db()
    scope = _scope_dict(bucket, ids)
    print("→ detect pass (scrape + classify)…")
    rid = await _run_pass(scope, "none", headless)
    print(f"  run {rid} done.")
    customers = await _scope_customers(bucket, ids)
    await _print_status(customers)


async def cmd_claim(bucket, ids, headless):
    db.init_db()
    scope = _scope_dict(bucket, ids)
    print("→ detect pass…")
    await _run_pass(scope, "none", headless)
    print("→ self-claim pass (scripted; code-gated to card)…")
    await _run_pass(scope, "scripted", headless)
    print("→ verifying claims against receipts (zero-tolerance)…")
    customers = await _scope_customers(bucket, ids)
    n = await _verify_unconfirmed(customers, headless)
    print(f"  promoted {n} claim(s) to refunded on receipt proof.")
    await _print_status(customers)


async def cmd_all(bucket, ids, headless):
    await cmd_claim(bucket, ids, headless)
    if bucket:
        from backend import report
        path = await report.build_daily_report(bucket)
        print(f"→ report: {path}")
    else:
        print("→ (no bucket given; skipped report build — pass --bucket to build)")


def main() -> None:
    ap = argparse.ArgumentParser(prog="refund_run")
    ap.add_argument("command",
                    choices=["detect", "claim", "all", "status"])
    ap.add_argument("--bucket", help="bucket date YYYY-MM-DD")
    ap.add_argument("--ids", help="comma-separated customer ids")
    ap.add_argument("--headless", action="store_true",
                    help="run headless (default headed — user watches)")
    args = ap.parse_args()

    ids = [int(x) for x in args.ids.split(",")] if args.ids else None
    if not args.bucket and not ids:
        ap.error("need --bucket or --ids")
    headless = args.headless

    if args.command == "status":
        asyncio.run(cmd_status(args.bucket, ids))
    elif args.command == "detect":
        asyncio.run(cmd_detect(args.bucket, ids, headless))
    elif args.command == "claim":
        asyncio.run(cmd_claim(args.bucket, ids, headless))
    elif args.command == "all":
        asyncio.run(cmd_all(args.bucket, ids, headless))


if __name__ == "__main__":
    main()
