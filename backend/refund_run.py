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

    .venv\\Scripts\\python.exe -m backend.refund_run login   --bucket 2026-06-15
    .venv\\Scripts\\python.exe -m backend.refund_run detect  --bucket 2026-06-15
    .venv\\Scripts\\python.exe -m backend.refund_run claim   --bucket 2026-06-15
    .venv\\Scripts\\python.exe -m backend.refund_run all     --bucket 2026-06-15
    .venv\\Scripts\\python.exe -m backend.refund_run status  --bucket 2026-06-15

    # scope by ids instead of a whole bucket:
    .venv\\Scripts\\python.exe -m backend.refund_run claim --ids 17,20,21

`login`   = log in every in-scope customer with no session yet (skips ones
            already logged in). OTP via api.cc internally.
`detect`  = scrape + classify only (no claims/chats).
`claim`   = detect, then self-claim pending_claim orders, then verify by receipt.
`all`     = login (no-op if all logged in) + claim + (re)build the daily report.
            The everyday command — runs a freshly-adopted batch end to end.
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


def _p(msg: str) -> None:
    """Print a progress line and FLUSH immediately, so a session driving this
    headed for many minutes can stream live updates to the user instead of a
    silent black box. `PROGRESS:`-prefixed lines are the ones meant to surface."""
    print(msg, flush=True)


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
    """Start a pass on the SHARED RunManager and AWAIT it to completion.

    Use the module-global `manager` (not a fresh RunManager) so the CLI and the
    web UI share one run-lifecycle: `start()` raises if a run is already active
    (mutual exclusion — no two concurrent runs fighting over the same profiles),
    and the web UI's "run active" / /api/runs/active reflect the CLI's run.
    """
    from backend.runner import manager
    run_id = await manager.start(scope, strategy, headless=headless)
    if manager._task is not None:
        await manager._task
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
    from backend.browser.orders import scrape_orders

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
        for i, c in enumerate(needs, 1):
            cid = c["id"]
            name = f"{c.get('first_name','')} {c.get('last_name','')}".strip()
            _p(f"PROGRESS: verifying {i}/{len(needs)} — [{cid}] {name} "
               f"(reopening receipts)…")
            try:
                async with customer_profile(
                        p, cid, headless=headless, viewport=(1200, 720)) as ctx:
                    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                    await page.goto(ORDERS_URL, wait_until="domcontentloaded")
                    await handle_cloudflare(page)
                    await asyncio.sleep(2.5)
                    fresh = await scrape_orders(page)
                    promoted += await _reconcile_customer(cid, fresh, cfg, page,
                                                          name)
            except Exception as exc:  # one bad customer ≠ kill the pass
                _p(f"PROGRESS:   [{cid}] {name}: verify failed — "
                   f"{type(exc).__name__}: {exc}")
    return promoted


def resolution_write(rr, fallback_price):
    """Pure: given a receipt detect() result, decide what to WRITE.

    Returns ``(status, total_amount, refund_amount, is_promotion)``. The
    zero-tolerance rules live here so they're unit-testable without a browser:
      - ``refunded`` ONLY for a full receipt-proven refund (is_promotion=True).
      - ``partial`` stays ``partial`` — a partial refund is NEVER written as
        ``refunded`` (the shortfall must stay visible for the chat step).
      - every other status is written through as-is (pending_claim /
        not_refunded / unknown) — none count as a promotion.
    """
    if rr.status == RefundStatus.refunded:
        amt = rr.refund_amount if rr.refund_amount is not None else fallback_price
        return (RefundStatus.refunded.value, rr.total_amount, amt, True)
    if rr.status == RefundStatus.partial:
        return (RefundStatus.partial.value, rr.total_amount,
                rr.refund_amount, False)
    return (rr.status.value, rr.total_amount, rr.refund_amount, False)


async def _reconcile_customer(cid: int, fresh: list, cfg: dict, page,
                              name: str = "") -> int:
    """Reconcile one customer's orders by REAL UUID — never by price.

    Prices collide (orders can be identical or cents apart), so a price-based
    twin match is unsafe and is NOT used here. Instead: every freshly-scraped
    COMPLETED order carries a real /orders/<uuid>. We open each such receipt,
    read its refund state directly, and write the result keyed by that real
    UUID (idempotent upsert). Synthetic ``pendingclaim:*`` / ``inprogress:*``
    rows that have been superseded by a real-UUID twin for the same store are
    then dropped. A refund is recorded ONLY on a receipt-proven line, and a
    PARTIAL refund is recorded as ``partial`` (never ``refunded``).
    """
    from backend.browser.orders import open_receipt
    from backend.browser.refund_detector import detect

    promoted = 0
    # Real-UUID completed orders only — these are the ground truth.
    real = [o for o in fresh
            if o.receipt_url and not o.order_uuid.startswith(
                ("pendingclaim:", "inprogress:"))]

    who = f"[{cid}] {name}".strip()
    for j, o in enumerate(real, 1):
        _p(f"PROGRESS:   {who} — order {j}/{len(real)} "
           f"({o.store_name} ${o.price}): reading receipt…")
        text = await open_receipt(page, o.receipt_url)
        rr = detect(text, cfg)
        status, total, amount, promotion = resolution_write(rr, o.price)
        # Always (idempotent) upsert the real-UUID row so the DB tracks it.
        oid = await db.upsert_order(
            customer_id=cid, order_uuid=o.order_uuid,
            receipt_url=o.receipt_url, store_name=o.store_name, price=o.price)
        await db.update_order_refund(oid, status, total, amount)
        if promotion:
            promoted += 1
        tag = {"refunded": "✅ refunded", "partial": "◐ PARTIAL → needs chat"}\
            .get(status, f"{status} → needs chat/human")
        _p(f"PROGRESS:   {who} — {o.store_name} ${o.price}: {tag}")

    # Drop synthetic rows now superseded by a real-UUID order at the same store.
    # Count-based, not price-based: if the store now has >= as many real orders
    # as it had synthetic rows, the synthetics are stale duplicates.
    db_orders = await db.list_orders(cid)
    real_by_store: dict[str, int] = {}
    for o in real:
        real_by_store[(o.store_name or "").lower()] = \
            real_by_store.get((o.store_name or "").lower(), 0) + 1
    for dbo in db_orders:
        uuid = dbo.get("order_uuid", "")
        if not uuid.startswith(("pendingclaim:", "inprogress:")):
            continue
        store = (dbo.get("store_name") or "").lower()
        if real_by_store.get(store, 0) > 0:
            # A real-UUID twin exists for this store; the synthetic row is stale.
            await _delete_order(dbo["id"])
            print(f"  [{cid}] dropped stale synthetic row "
                  f"{uuid[:28]} (superseded by real order)")
    return promoted


async def _delete_order(order_id: int) -> None:
    """Delete an order row and its run_orders FK refs (stale synthetic rows)."""
    await db.execute("DELETE FROM run_orders WHERE order_id=?", (order_id,))
    await db.execute("DELETE FROM orders WHERE id=?", (order_id,))


# NOTE: there is deliberately NO price-based "twin matcher" here. Orders can
# share the same price or sit cents apart, so pairing a stored row to a receipt
# by price is unsafe and could promote the WRONG order. Reconciliation is
# UUID-driven only — see _reconcile_customer.


# ── commands ─────────────────────────────────────────────────────────────────

async def cmd_status(bucket, ids):
    db.init_db()
    customers = await _scope_customers(bucket, ids)
    print(f"=== status ({len(customers)} customers) ===")
    await _print_status(customers)


async def cmd_detect(bucket, ids, headless, legacy=False):
    db.init_db()
    if legacy:
        scope = _scope_dict(bucket, ids)
        _p("PROGRESS: phase 1/1 — detect (legacy Playwright scrape + classify)…")
        rid = await _run_pass(scope, "none", headless)
        _p(f"PROGRESS: detect run {rid} complete.")
        await _print_status(await _scope_customers(bucket, ids))
        return
    # CDP detect: reads receipts PAST Cloudflare (the Playwright path re-gates on
    # /orders and leaves everything unconfirmed). Promotes only on proof.
    await _cdp_detect(bucket, ids, headless)
    await _print_status(await _scope_customers(bucket, ids))


async def _cdp_detect(bucket, ids, headless):
    """Per-customer CDP detect for any customer with pending (unconfirmed/
    unchecked/not_refunded) receipt-bearing orders."""
    from backend.relogin import detect_customer_via_cdp
    customers = await _scope_customers(bucket, ids)
    pending_cids = []
    for c in customers:
        os_ = await db.list_orders(c["id"])
        if any(o.get("refund_status") in ("unconfirmed", "unchecked",
                                          "not_refunded")
               and "/orders/" in (o.get("receipt_url") or "")
               for o in os_):
            pending_cids.append(c)
    if not pending_cids:
        _p("PROGRESS: detect — no pending receipt-bearing orders to check.")
        return
    _p(f"PROGRESS: CDP detect — {len(pending_cids)} customer(s) with pending "
       f"orders (reads receipts past Cloudflare)…")
    for i, c in enumerate(pending_cids, 1):
        name = f"{c.get('first_name','')} {c.get('last_name','')}".strip()
        _p(f"PROGRESS: [{c['id']}] {name} ({i}/{len(pending_cids)})…")
        try:
            res = await detect_customer_via_cdp(c["id"], headless=headless)
            _p(f"PROGRESS:   [{c['id']}] {name}: {res.get('promoted',0)} "
               f"promoted → refunded (login {res.get('outcome')})")
        except Exception as exc:
            _p(f"PROGRESS:   [{c['id']}] {name}: detect FAILED — "
               f"{type(exc).__name__}: {exc}")


async def cmd_claim(bucket, ids, headless, legacy=False):
    db.init_db()
    scope = _scope_dict(bucket, ids)
    _p("PROGRESS: phase 1/3 — detect (scraping + classifying orders)…")
    await _run_pass(scope, "none", headless)
    _p("PROGRESS: phase 2/3 — self-claim (selecting original card, never credits)…")
    await _run_pass(scope, "scripted", headless)
    _p("PROGRESS: phase 3/3 — verifying claims against receipts (zero-tolerance)…")
    customers = await _scope_customers(bucket, ids)
    n = await _verify_unconfirmed(customers, headless)
    _p(f"PROGRESS: {n} claim(s) proven on receipt → refunded.")
    # CDP verify pass: promote any still-pending receipt-bearing orders the
    # Playwright verify couldn't read past Cloudflare.
    if not legacy:
        await _cdp_detect(bucket, ids, headless)
    await _print_status(await _scope_customers(bucket, ids))


async def cmd_login(bucket, ids, headless, legacy=False):
    """Log in every in-scope customer that has no DoorDash session yet.

    Skips customers already logged in (a carried-over customer keeps yesterday's
    session). One at a time, headed — the OTP is fetched via api.cc internally.
    """
    db.init_db()
    # CDP login clears the Cloudflare Turnstile that plain-Playwright login can't
    # (it fronts the consumer login page). Falls back to Playwright with --legacy.
    from backend.relogin import phone_login_customer_cdp, relogin_customer
    login_fn = relogin_customer if legacy else phone_login_customer_cdp
    customers = await _scope_customers(bucket, ids)
    need = [c for c in customers if not (c.get("storage_state_path") or "").strip()]
    skip = len(customers) - len(need)
    if skip:
        _p(f"PROGRESS: {skip} customer(s) already logged in — skipping.")
    if not need:
        _p("PROGRESS: login phase — nothing to log in.")
        return
    for i, c in enumerate(need, 1):
        name = f"{c.get('first_name','')} {c.get('last_name','')}".strip()
        _p(f"PROGRESS: logging in {i}/{len(need)} — [{c['id']}] {name}…")
        try:
            res = await login_fn(c["id"], headless=headless)
            _p(f"PROGRESS:   [{c['id']}] {name}: {res.get('outcome', '?')}")
        except Exception as exc:
            _p(f"PROGRESS:   [{c['id']}] {name}: LOGIN FAILED — "
               f"{type(exc).__name__}: {exc}")


async def cmd_all(bucket, ids, headless, legacy=False):
    # Log in any not-yet-logged-in customer first (no-op if all have sessions),
    # so a freshly-adopted batch runs end-to-end from one command.
    _p("PROGRESS: === full run starting (login → detect → claim → verify → "
       "report) ===")
    await cmd_login(bucket, ids, headless, legacy)
    await cmd_claim(bucket, ids, headless, legacy)
    if bucket:
        from backend import report
        _p("PROGRESS: building daily report…")
        path = await report.build_daily_report(bucket)
        _p(f"PROGRESS: report ready → {path}")
    else:
        _p("PROGRESS: (no bucket given; skipped report — pass --bucket to build)")
    _p("PROGRESS: === full run complete ===")


def main() -> None:
    ap = argparse.ArgumentParser(prog="refund_run")
    ap.add_argument("command",
                    choices=["login", "detect", "claim", "all", "status"])
    ap.add_argument("--bucket", help="bucket date YYYY-MM-DD")
    ap.add_argument("--ids", help="comma-separated customer ids")
    ap.add_argument("--headless", action="store_true",
                    help="run headless (default headed — user watches)")
    ap.add_argument("--legacy", action="store_true",
                    help="use the old Playwright login/detect path (re-trips "
                         "Cloudflare); default is the CDP path that clears CF")
    args = ap.parse_args()

    try:
        ids = [int(x) for x in args.ids.split(",")] if args.ids else None
    except ValueError:
        ap.error("--ids must be comma-separated integers, e.g. --ids 17,20,21")
    if not args.bucket and not ids:
        ap.error("need --bucket or --ids")
    headless = args.headless

    legacy = args.legacy
    if args.command == "status":
        asyncio.run(cmd_status(args.bucket, ids))
    elif args.command == "login":
        asyncio.run(cmd_login(args.bucket, ids, headless, legacy))
    elif args.command == "detect":
        asyncio.run(cmd_detect(args.bucket, ids, headless, legacy))
    elif args.command == "claim":
        asyncio.run(cmd_claim(args.bucket, ids, headless, legacy))
    elif args.command == "all":
        asyncio.run(cmd_all(args.bucket, ids, headless, legacy))


if __name__ == "__main__":
    main()
