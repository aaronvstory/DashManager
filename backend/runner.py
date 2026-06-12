"""RunManager: orchestrates a refund-check run across customers.

One run at a time. Customers are processed CONCURRENTLY (bounded by a
semaphore), each in its own persistent Chromium profile → scrape all orders →
open each receipt → detect refund → one support chat per customer covering
every not-properly-refunded order. Progress streams to the UI via the
EventBus; durable results land in SQLite.
"""
from __future__ import annotations

import asyncio
import time
import traceback
from typing import Any

from backend import db
from backend.events import bus
from backend.models import RefundStatus

THROTTLE_STEP_S = 8     # ported auto-throttle: grow per blocked chat
THROTTLE_CAP_S = 45
THROTTLE_DECAY_S = 120  # quiet period that resets the backoff


def _amounts_text(prices: list[float | None]) -> str:
    parts = [f"${p:.2f}" for p in prices if p is not None]
    if not parts:
        return "the listed amounts"
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


class RunManager:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.current_run_id: int | None = None
        self._throttle_s = 0.0     # shared chat backoff across customers
        self._last_block_t = 0.0
        # Concurrent workers mutate stats/throttle; `+= 1` spans multiple
        # bytecodes and the workers interleave at await points, so guard them.
        self._stats_lock = asyncio.Lock()

    async def _bump(self, stats: dict[str, Any], key: str, n: int = 1) -> None:
        async with self._stats_lock:
            stats[key] = stats.get(key, 0) + n

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self, scope: dict[str, Any], chat_strategy: str,
                    headless: bool | None = None) -> int:
        if self.is_running:
            raise RuntimeError("a run is already active")
        run_id = await db.create_run(scope, chat_strategy)
        self.current_run_id = run_id
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(
            self._run(run_id, scope, chat_strategy, headless))
        return run_id

    def stop(self) -> None:
        self._stop.set()

    async def _resolve_customers(self, scope: dict[str, Any]) -> list[dict]:
        customers = await db.list_customers()
        if scope.get("customer_ids"):
            # Coerce in case the API client sends IDs as strings — otherwise
            # the int customer ids never match and the run selects nobody.
            wanted = {int(cid) for cid in scope["customer_ids"]}
            return [c for c in customers if c["id"] in wanted]
        if scope.get("bucket_date"):
            return [c for c in customers
                    if c["bucket_date"] == scope["bucket_date"]]
        return []

    async def _run(self, run_id: int, scope: dict[str, Any],
                   strategy_name: str, headless: bool | None = None) -> None:
        def emit(type: str, data: dict | None = None) -> None:
            bus.publish(type, data or {}, run_id=run_id)

        stats = {"customers": 0, "checked": 0, "not_refunded": 0,
                 "claims_started": 0, "claims_won": 0,
                 "chats_started": 0, "chats_won": 0, "blocked": 0,
                 "manual": 0, "sessions_expired": 0, "errors": 0}
        status = "completed"
        # Shared backoff across concurrent customers (single event loop, so
        # plain attributes are safe to read/write between awaits).
        self._throttle_s = 0.0
        self._last_block_t = 0.0
        try:
            from playwright.async_api import async_playwright  # lazy

            customers = await self._resolve_customers(scope)
            browser_cfg = await db.get_setting("browser")
            cfg = {
                "chat": await db.get_setting("chat"),
                "refund": await db.get_setting("refund_signal"),
                "llm": await db.get_setting("llm"),
                "browser": browser_cfg,
                "api_key": await db.get_setting("openrouter_api_key"),
                # Per-run headless override (None -> use the setting).
                "headless": (headless if headless is not None
                             else bool(browser_cfg.get("headless", False))),
            }
            max_conc = int(browser_cfg.get("max_concurrent", 3))
            sem = asyncio.Semaphore(max(1, max_conc))

            emit("run_started", {"scope": scope, "chat_strategy": strategy_name,
                                 "customer_count": len(customers),
                                 "max_concurrent": max_conc})

            async with async_playwright() as p:
                async def worker(pos: int, cust: dict[str, Any]) -> None:
                    async with sem:
                        if self._stop.is_set():
                            return
                        await self._process_customer(
                            p, run_id, pos, len(customers), cust,
                            strategy_name, cfg, stats, emit)

                results = await asyncio.gather(
                    *(worker(i, c) for i, c in enumerate(customers, start=1)),
                    return_exceptions=True)
                # _process_customer catches its own errors, but a failure in
                # the worker wrapper itself would otherwise vanish — log it.
                for r in results:
                    if isinstance(r, BaseException):
                        emit("log", {"level": "error",
                                     "message": f"worker crashed: {r}"})

            if self._stop.is_set():
                status = "stopped"
            await db.finish_run(run_id, status, stats)
            emit("run_done", {"status": status, "stats": stats})
        except Exception as exc:
            traceback.print_exc()
            await db.finish_run(run_id, "error", {**stats, "error": str(exc)})
            emit("run_error", {"error": str(exc)})
        finally:
            self.current_run_id = None

    async def _process_customer(self, p: Any, run_id: int, pos: int,
                                total: int, cust: dict[str, Any],
                                strategy_name: str, cfg: dict[str, Any],
                                stats: dict[str, Any], emit: Any) -> None:
        from backend.browser.chat import run_chat, try_reconnect
        from backend.browser.chat_strategy import (ChatContext, ProblemOrder,
                                                   get_strategy)
        from backend.browser.claim import run_claim
        from backend.browser.driver import (SessionExpiredError,
                                             export_storage_state,
                                             open_customer_profile,
                                             profile_lock)
        from backend.browser.orders import open_receipt, scrape_orders
        from backend.browser.refund_detector import detect

        # Everything is inside the try so a KeyError on cust/cfg (or any
        # setup error) is logged, not silently swallowed by gather(
        # return_exceptions=True).
        cid = None
        name = "Unknown customer"
        ctx = None
        plock = None
        plock_held = False
        try:
            cid = cust["id"]
            name = (f"{cust['first_name']} {cust['last_name']}".strip()
                    or f"Customer {cid}")
            emit("customer_started", {"customer_id": cid, "name": name,
                                      "position": pos, "total": total})
            await self._bump(stats, "customers")
            browser_cfg = cfg["browser"]
            # Per-customer lock: never let a manual test-session/relogin open
            # this same profile concurrently (Chromium locks the dir). Track
            # OUR acquisition with a flag — `locked()` is True even when
            # another task holds it, so releasing on that would steal it.
            plock = profile_lock(cid)
            await plock.acquire()
            plock_held = True
            try:
                # Each customer drives its OWN persistent profile — fully
                # isolated, so concurrent customers never share cookies.
                ctx = await open_customer_profile(
                    p, cid, bool(cfg["headless"]),
                    seed_storage_state=cust.get("storage_state_path") or None,
                    viewport=tuple(browser_cfg.get("viewport", [1400, 900])))
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                scraped = await scrape_orders(page, emit=emit)
            except SessionExpiredError:
                await self._bump(stats, "sessions_expired")
                await db.update_customer(cid, session_status="expired")
                emit("session_invalid", {"customer_id": cid})
                return
            await export_storage_state(ctx, cid)  # refresh portable backup
            emit("orders_found", {"customer_id": cid, "count": len(scraped)})

            # Replace prior in-progress rows (no stable identity) so phantoms
            # from completed/vanished live orders don't accumulate.
            await db.clear_in_progress_orders(cid)
            # Orders needing a CHAT (not_refunded/partial/remake) and orders to
            # SELF-CLAIM (pending_claim) are routed differently.
            problems: list[tuple[int, Any]] = []
            claimables: list[tuple[int, Any]] = []
            for so in scraped:
                if self._stop.is_set():
                    break
                oid = await db.upsert_order(
                    cid, so.order_uuid, so.receipt_url,
                    store_name=so.store_name, description=so.description,
                    items_count=so.items_count, price=so.price,
                    order_status=so.order_status.value,
                    status_text=so.status_text, dasher_name=so.dasher_name)
                # Refunds only apply to FINISHED orders — skip in-progress and
                # cancelled (cancelled refunds are detected on the receipt).
                if so.order_status.value == "in_progress":
                    emit("order_checked", {"order_id": oid,
                                           "refund_status": "unchecked",
                                           "order_status": "in_progress",
                                           "status_text": so.status_text,
                                           "dasher_name": so.dasher_name})
                    continue
                emit("order_checking", {"order_id": oid,
                                        "store": so.store_name,
                                        "url": so.receipt_url})
                try:
                    text = await open_receipt(page, so.receipt_url)
                    rr = detect(text, cfg["refund"])
                except Exception as exc:  # one bad receipt ≠ dead customer
                    await self._bump(stats, "errors")
                    await db.add_run_order(run_id, oid, cid, error=str(exc))
                    emit("log", {"level": "error",
                                 "message": f"receipt check failed: {exc}"})
                    continue
                await db.update_order_refund(oid, rr.status.value,
                                             rr.total_amount, rr.refund_amount)
                await db.add_run_order(run_id, oid, cid,
                                       refund_status=rr.status.value)
                emit("order_checked", {"order_id": oid,
                                       "refund_status": rr.status.value,
                                       "total_amount": rr.total_amount,
                                       "refund_amount": rr.refund_amount})
                await self._bump(stats, "checked")
                if rr.status == RefundStatus.pending_claim:
                    claimables.append((oid, so))
                elif rr.status in (RefundStatus.not_refunded,
                                   RefundStatus.partial, RefundStatus.remake):
                    await self._bump(stats, "not_refunded")
                    # Carry the remake flag for the chat to call out.
                    problems.append(
                        (oid, so, rr.status == RefundStatus.remake))

            if strategy_name == "none" or self._stop.is_set():
                pass  # detect-only mode: never claim or chat
            else:
                # Self-claim pending refunds first (no agent needed), then
                # chat the rest — one chat (with up to 3 attempts) per order.
                for oid, so in claimables:
                    if self._stop.is_set():
                        break
                    await self._claim_order(run_id, cid, page, oid, so,
                                            cfg, stats, emit, run_claim)
                for oid, so, is_remake in problems:
                    if self._stop.is_set():
                        break
                    await self._pursue_order(
                        run_id, cid, name, cust, page, oid, so, is_remake,
                        strategy_name, cfg, stats, emit, run_chat,
                        try_reconnect, ChatContext, ProblemOrder, get_strategy)
        except Exception as exc:
            await self._bump(stats, "errors")
            emit("log", {"level": "error",
                         "message": f"customer {name} failed: {exc}"})
        finally:
            if ctx is not None:
                await ctx.close()  # persistent context owns the browser
            if plock is not None and plock_held:
                plock.release()  # only release the lock WE acquired
        emit("customer_done", {"customer_id": cid, "stats": dict(stats)})

    async def _claim_order(self, run_id, cid, page, oid, so, cfg, stats, emit,
                           run_claim) -> None:
        """Self-claim one pending_claim order (no agent chat). Records an
        audit row and updates the order's refund_status on success."""
        emit("claim_started", {"customer_id": cid, "order_id": oid,
                               "store": so.store_name, "amount": so.price})
        await self._bump(stats, "claims_started")
        result = await run_claim(page, so.order_uuid, so.receipt_url,
                                 cfg["refund"], emit=emit)
        await db.create_claim(
            run_id, oid, cid, amount=result.amount,
            to_original_payment=result.to_original_payment,
            confirmed=result.confirmed, outcome=result.outcome,
            error=result.error)
        emit("claim_outcome", {"order_id": oid, "outcome": result.outcome,
                               "amount": result.amount,
                               "to_original_payment": result.to_original_payment})
        if result.outcome == "success":
            await self._bump(stats, "claims_won")
            await db.update_order_refund(
                oid, RefundStatus.refunded.value, result.amount, result.amount)
        else:
            # A failed self-claim is escalated for a human to look at.
            await self._bump(stats, "manual")

    async def _throttle_wait(self, emit) -> bool:
        """Apply the shared chat backoff. Returns False if a Stop interrupted
        it (caller should abort)."""
        now = time.monotonic()
        if self._last_block_t and now - self._last_block_t > THROTTLE_DECAY_S:
            self._throttle_s = 0.0
        if self._throttle_s:
            emit("log", {"level": "info",
                         "message": f"throttle: waiting {self._throttle_s:.0f}s"})
            try:
                await asyncio.wait_for(self._stop.wait(),
                                       timeout=self._throttle_s)
            except asyncio.TimeoutError:
                pass
        return not self._stop.is_set()

    async def _pursue_order(self, run_id, cid, name, cust, page, oid, so,
                            is_remake, strategy_name, cfg, stats, emit,
                            run_chat, try_reconnect, ChatContext, ProblemOrder,
                            get_strategy) -> None:
        """One unrefunded order → one support chat, retried up to max_attempts.

        Each attempt is its own chat row (the per-order audit stacks attempts).
        Between attempts we try to reconnect the same session before reopening
        a fresh chat (cheaper, less rate-limit-prone).
        """
        chat_cfg = cfg["chat"]
        max_attempts = int(chat_cfg.get("max_attempts", 3))
        amounts = _amounts_text([so.price])
        try:
            opening = chat_cfg["opening_template"].format(
                order_count=1, amounts=amounts,
                customer_name=cust["first_name"] or name)
        except (ValueError, KeyError, IndexError):
            opening = (f"Hi, my order for {amounts} was canceled but I have "
                       "not received a refund. Please refund it to my original "
                       "payment card (not credits).")
        if is_remake:
            opening += chat_cfg.get("remake_note", "")

        effective_cfg = dict(chat_cfg)
        if strategy_name == "llm":
            effective_cfg["max_turns"] = int(
                cfg["llm"].get("max_turns", chat_cfg["max_turns"]))

        final_outcome = "manual_flag"
        for attempt in range(1, max_attempts + 1):
            if self._stop.is_set():
                break
            if not await self._throttle_wait(emit):
                break

            chat_id = await db.create_chat(
                run_id, cid, [oid], opening, order_id=oid, attempt_no=attempt)
            emit("chat_opened", {"chat_id": chat_id, "customer_id": cid,
                                 "order_id": oid, "attempt": attempt})
            emit("chat_attempt", {"chat_id": chat_id, "order_id": oid,
                                  "attempt": attempt, "max": max_attempts})
            await self._bump(stats, "chats_started")

            async def record(direction: str, content: str,
                             _cid: int = chat_id) -> None:
                await db.add_chat_message(_cid, direction, content)
                emit("chat_message", {"chat_id": _cid, "direction": direction,
                                      "content": content})

            chat_ctx = ChatContext(
                customer_name=cust["first_name"] or name,
                orders=[ProblemOrder(order_id=oid, order_uuid=so.order_uuid,
                                     store_name=so.store_name, price=so.price,
                                     refund_status="not_refunded",
                                     remake=is_remake)],
                opening_message=opening,
                config={**chat_cfg,
                        "llm_system_prompt": (cfg["llm"].get("system_prompt")
                                              or None),
                        "llm_model": cfg["llm"].get("model"),
                        "openrouter_api_key": cfg["api_key"] or None})
            # After the first attempt, try to resume the existing session.
            skip_nav = False
            if attempt > 1:
                skip_nav = await try_reconnect(page)
            outcome, agent_reached = await run_chat(
                page, get_strategy(strategy_name), chat_ctx,
                chat_cfg=effective_cfg, emit=emit, record=record,
                skip_nav=skip_nav)
            await db.finish_chat(chat_id, outcome, agent_reached)
            emit("chat_outcome", {"chat_id": chat_id, "order_id": oid,
                                  "outcome": outcome, "attempt": attempt,
                                  "agent_reached": agent_reached})
            final_outcome = outcome
            if outcome == "success":
                await self._bump(stats, "chats_won")
                await db.update_order_refund(
                    oid, RefundStatus.refunded.value, so.price, so.price)
                return
            if outcome in ("blocked", "review_blocked"):
                await self._bump(stats, "blocked")
                self._last_block_t = time.monotonic()
                self._throttle_s = min(self._throttle_s + THROTTLE_STEP_S,
                                       THROTTLE_CAP_S)
            # failed / manual_flag → retry (until attempts exhausted).

        # Count an unresolved order as "manual" (needs a human) EXCEPT when its
        # last attempt was blocked — that's already tallied under "blocked", and
        # the UI shows the two separately, so double-counting would inflate it.
        if final_outcome not in ("success", "blocked", "review_blocked"):
            await self._bump(stats, "manual")


manager = RunManager()
