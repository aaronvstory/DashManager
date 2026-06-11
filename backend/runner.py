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

                await asyncio.gather(
                    *(worker(i, c) for i, c in enumerate(customers, start=1)),
                    return_exceptions=True)

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
        from backend.browser.chat import run_chat
        from backend.browser.chat_strategy import (ChatContext, ProblemOrder,
                                                   get_strategy)
        from backend.browser.driver import (SessionExpiredError,
                                             export_storage_state,
                                             open_customer_profile)
        from backend.browser.orders import open_receipt, scrape_orders
        from backend.browser.refund_detector import detect

        cid = cust["id"]
        name = (f"{cust['first_name']} {cust['last_name']}".strip()
                or f"Customer {cid}")
        emit("customer_started", {"customer_id": cid, "name": name,
                                  "position": pos, "total": total})
        await self._bump(stats, "customers")
        browser_cfg = cfg["browser"]
        ctx = None
        try:
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
            problems: list[tuple[int, Any]] = []
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
                if rr.status in (RefundStatus.not_refunded,
                                 RefundStatus.partial):
                    await self._bump(stats, "not_refunded")
                    problems.append((oid, so))

            if problems and strategy_name != "none" and not self._stop.is_set():
                await self._pursue_refunds(run_id, cid, name, cust, page,
                                           problems, strategy_name, cfg, stats,
                                           emit, run_chat, ChatContext,
                                           ProblemOrder, get_strategy)
        except Exception as exc:
            await self._bump(stats, "errors")
            emit("log", {"level": "error",
                         "message": f"customer {name} failed: {exc}"})
        finally:
            if ctx is not None:
                await ctx.close()  # persistent context owns the browser
        emit("customer_done", {"customer_id": cid, "stats": dict(stats)})

    async def _pursue_refunds(self, run_id, cid, name, cust, page, problems,
                              strategy_name, cfg, stats, emit, run_chat,
                              ChatContext, ProblemOrder, get_strategy) -> None:
        # Shared auto-throttle: back off once DoorDash starts blocking chats,
        # decay after a quiet period.
        now = time.monotonic()
        if self._last_block_t and now - self._last_block_t > THROTTLE_DECAY_S:
            self._throttle_s = 0.0
        if self._throttle_s:
            emit("log", {"level": "info",
                         "message": f"throttle: waiting {self._throttle_s:.0f}s"})
            await asyncio.sleep(self._throttle_s)

        chat_cfg = cfg["chat"]
        amounts = _amounts_text([so.price for _, so in problems])
        try:
            opening = chat_cfg["opening_template"].format(
                order_count=len(problems), amounts=amounts,
                customer_name=cust["first_name"] or name)
        except (ValueError, KeyError, IndexError):
            opening = (f"Hi, I placed {len(problems)} order(s) for {amounts} "
                       "and they are not showing as refunded. Please ensure "
                       "they are REFUNDED back to my original payment card "
                       "(not credits).")
        order_ids = [oid for oid, _ in problems]
        chat_id = await db.create_chat(run_id, cid, order_ids, opening)
        emit("chat_opened", {"chat_id": chat_id, "customer_id": cid,
                             "order_ids": order_ids})
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
                                 refund_status="not_refunded")
                    for oid, so in problems],
            opening_message=opening,
            config={**chat_cfg,
                    "llm_system_prompt": cfg["llm"].get("system_prompt") or None,
                    "llm_model": cfg["llm"].get("model"),
                    "openrouter_api_key": cfg["api_key"] or None})
        effective_cfg = dict(chat_cfg)
        if strategy_name == "llm":
            effective_cfg["max_turns"] = int(
                cfg["llm"].get("max_turns", chat_cfg["max_turns"]))
        outcome, agent_reached = await run_chat(
            page, get_strategy(strategy_name), chat_ctx,
            chat_cfg=effective_cfg, emit=emit, record=record)
        await db.finish_chat(chat_id, outcome, agent_reached)
        emit("chat_outcome", {"chat_id": chat_id, "outcome": outcome,
                              "agent_reached": agent_reached})
        if outcome == "success":
            await self._bump(stats, "chats_won")
        elif outcome in ("blocked", "review_blocked"):
            await self._bump(stats, "blocked")
            self._last_block_t = time.monotonic()
            self._throttle_s = min(self._throttle_s + THROTTLE_STEP_S,
                                   THROTTLE_CAP_S)
        elif outcome == "manual_flag":
            await self._bump(stats, "manual")


manager = RunManager()
