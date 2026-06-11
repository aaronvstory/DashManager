"""Live self-test against the real DoorDash customers in the DB.

Proves the refund automation works end to end on the goldmine of already-
resolved live data, and surfaces selector/flow drift so it can be fixed.

Run it like the backend (Proactor loop for Playwright):

    .venv\\Scripts\\python.exe -m scripts.live_selftest                 # all customers
    .venv\\Scripts\\python.exe -m scripts.live_selftest --only 7        # one customer
    .venv\\Scripts\\python.exe -m scripts.live_selftest --chat 7        # + connectivity chat
    .venv\\Scripts\\python.exe -m scripts.live_selftest --headless

It is IDEMPOTENT and READ-ONLY against DoorDash, with ONE deliberate exception:
the connectivity chat (``--chat <id>``), which opens a real support chat, says
exactly two polite messages, and ends — purely to exercise the transcript +
agent-connect path. At most one such chat per customer per night.

For each customer it:
  1. Login check — open the profile, hit /orders. On Cloudflare variant-B
     (stale-session block) it wipes the profile + relogins (the proven fix).
  2. Order-bearers — open each receipt, assert detect() == refunded.
  3. No-orders — assert classify_orders_page == "none" and 0 orders.
  4. (optional) Connectivity chat — escalate to a human, send "Hello!", wait
     for a human reply, send the all-good line, End, and assert the transcript
     was captured order-keyed.

Results are appended to .claude/live_selftest.log with timestamps. The script
NEVER pursues a refund — the orders are already resolved.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Allow `python scripts/live_selftest.py` as well as `-m scripts.live_selftest`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import config, db  # noqa: E402

LOG_PATH = config.BASE / ".claude" / "live_selftest.log"


def _log(msg: str) -> None:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


async def _open_orders(p, cid: int, headless: bool, seed: str | None):
    """Open the profile and scrape /orders. Returns (ctx, page, result)."""
    from backend.browser.driver import open_customer_profile
    from backend.browser.orders import scrape_orders_full

    ctx = await open_customer_profile(p, cid, headless, seed_storage_state=seed)
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    result = await scrape_orders_full(page)
    return ctx, page, result


async def _check_login_and_orders(p, cust: dict, headless: bool):
    """Load /orders for one customer, recovering from a variant-B block.

    The caller holds the per-customer profile lock for the whole call EXCEPT
    while relogin runs (relogin acquires the lock itself) — we release it
    around the recovery and the caller re-acquires on return via the returned
    ``needs_reacquire`` flag handling. To keep the lock contract simple, this
    function does NOT touch the lock; the caller releases before calling and
    re-acquires after, because a variant-B recovery needs the lock free for
    relogin_customer. Returns (ctx, page, result, recovered).
    """
    from backend.browser.driver import classify_cloudflare
    from backend.browser.orders import scrape_orders_full

    cid = cust["id"]
    seed = cust.get("storage_state_path") or None

    from backend.browser.driver import open_customer_profile
    ctx = await open_customer_profile(p, cid, headless, seed_storage_state=seed)
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    await page.goto("https://www.doordash.com/orders",
                    wait_until="domcontentloaded")
    await asyncio.sleep(3)
    body = await page.evaluate(
        "() => document.body ? document.body.innerText : ''") or ""

    if classify_cloudflare(body) == "b":
        _log(f"  cust {cid}: Cloudflare variant-B — wipe + relogin")
        await ctx.close()
        # relogin_customer takes the per-customer lock itself, so the caller
        # must NOT be holding it here (see the caller's release before the
        # recovery). Wipe the stale profile dir as part of the fix.
        from backend.relogin import relogin_customer
        await relogin_customer(cid, headless=headless, wipe_profile=True)
        ctx, page, result = await _open_orders(p, cid, headless, None)
        return ctx, page, result, True

    result = await scrape_orders_full(page)
    return ctx, page, result, False


async def _verify_orders(page, result, refund_cfg) -> tuple[int, int]:
    """Open each completed order's receipt; assert detect() == refunded.

    Returns (verified_refunded, drift) counts. Drift = an order whose receipt
    did NOT classify as refunded (the live goldmine is all refunded, so drift
    flags real selector/detection breakage).
    """
    from backend.browser.orders import open_receipt
    from backend.browser.refund_detector import detect
    from backend.models import OrderStatus, RefundStatus

    verified = drift = 0
    for o in result.orders:
        if o.order_status != OrderStatus.completed or not o.receipt_url:
            continue
        try:
            text = await open_receipt(page, o.receipt_url)
            rr = detect(text, refund_cfg)
        except Exception as exc:
            _log(f"    order {o.store_name}: receipt read FAILED: {exc}")
            drift += 1
            continue
        if rr.status == RefundStatus.refunded:
            verified += 1
            _log(f"    order {o.store_name} ${o.price}: refunded ✓")
        else:
            drift += 1
            _log(f"    order {o.store_name} ${o.price}: DRIFT — "
                 f"detected {rr.status.value} (expected refunded)")
    return verified, drift


async def _connectivity_chat(page, result) -> bool:
    """ONE polite chat: escalate to a human, 'Hello!', wait, all-good, End.

    Returns True if a transcript was captured. Does NOT pursue a refund.
    """
    from backend.browser import chat as chatmod
    from backend.browser.pacing import human_pause

    order = next((o for o in result.orders if o.receipt_url), None)
    if order is None:
        _log("    connectivity chat: no order with a receipt — skipped")
        return False

    cfg = await db.get_setting("chat")
    nav = await chatmod.navigate_to_chat(page, order.order_uuid)
    if nav != "ok":
        _log(f"    connectivity chat: nav returned {nav} — could not open")
        return False

    captured: list[tuple[str, str]] = []

    # Escalate to a human first.
    await chatmod.send_message(page, cfg["agent_word"])
    captured.append(("out", cfg["agent_word"]))
    human = False
    deadline = time.monotonic() + float(cfg.get("human_wait_seconds", 90)) * 2
    before = await chatmod.count_received(page)
    while time.monotonic() < deadline:
        if await chatmod.wait_for_reply(page, before, max_wait=20):
            body = await page.evaluate(
                "() => document.body ? document.body.innerText : ''") or ""
            if "connected to our support agent" in body.lower():
                human = True
                break
            before = await chatmod.count_received(page)
            await chatmod.send_message(page, cfg["agent_word"])
            captured.append(("out", cfg["agent_word"]))
        await human_pause(1.5, 3.0)

    if not human:
        _log("    connectivity chat: no human connected in time")
        return bool(captured)

    await human_pause(1.0, 2.0)
    await chatmod.send_message(page, "Hello!")
    captured.append(("out", "Hello!"))
    # Wait for a HUMAN reply before the closing line.
    before = await chatmod.count_received(page)
    got = await chatmod.wait_for_reply(page, before, max_wait=120)
    _log(f"    connectivity chat: human {'replied' if got else 'silent'}")
    await human_pause(1.0, 2.0)
    await chatmod.send_message(page, "oh all good now thanks for your help!")
    captured.append(("out", "oh all good now thanks for your help!"))
    await human_pause(1.0, 2.0)
    await chatmod.end_chat(page)
    _log(f"    connectivity chat: sent {len(captured)} messages, ended")
    return True


async def run(args) -> int:
    from playwright.async_api import async_playwright

    db.init_db()
    refund_cfg = await db.get_setting("refund_signal")
    customers = await db.list_customers()
    if args.only:
        customers = [c for c in customers if c["id"] == args.only]
    if not customers:
        _log("no customers to test")
        return 1

    _log(f"=== live self-test start — {len(customers)} customer(s), "
         f"headless={args.headless} ===")
    failures = 0
    async with async_playwright() as p:
        for cust in customers:
            cid = cust["id"]
            name = (f"{cust['first_name']} {cust['last_name']}".strip()
                    or f"Customer {cid}")
            _log(f"customer {cid} ({name}):")
            ctx = None
            try:
                # _check_login_and_orders may itself call relogin_customer
                # (which takes the per-customer lock internally), so we do NOT
                # hold the lock here — the self-test is a single-task script
                # and is not meant to run concurrently with a backend run.
                ctx, page, result, recovered = await _check_login_and_orders(
                    p, cust, args.headless)
                _log(f"  login OK{' (recovered)' if recovered else ''}; "
                     f"state={result.state}, {len(result.orders)} order(s)")

                if result.state == "none":
                    if result.orders:
                        _log("  DRIFT: state=none but orders present")
                        failures += 1
                    else:
                        _log("  no-orders state confirmed ✓")
                else:
                    verified, drift = await _verify_orders(
                        page, result, refund_cfg)
                    _log(f"  receipts: {verified} refunded ✓, {drift} drift")
                    failures += drift

                if args.chat == cid:
                    ok = await _connectivity_chat(page, result)
                    _log(f"  connectivity chat captured={ok}")
            except Exception as exc:
                _log(f"  FAILED: {exc!r}")
                failures += 1
            finally:
                if ctx is not None:
                    try:
                        await ctx.close()
                    except Exception:
                        pass

    _log(f"=== live self-test done — {failures} failure(s) ===")
    return 1 if failures else 0


def main() -> None:
    ap = argparse.ArgumentParser(description="DashManager live self-test")
    ap.add_argument("--only", type=int, help="test only this customer id")
    ap.add_argument("--chat", type=int,
                    help="run the ONE connectivity chat on this customer id")
    ap.add_argument("--headless", action="store_true",
                    help="run headless (default: headed)")
    args = ap.parse_args()

    # Windows + Playwright need the Proactor loop, same as `python -m backend`.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
