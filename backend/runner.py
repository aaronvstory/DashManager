"""RunManager: orchestrates a refund-check run across customers.

One run at a time. Per customer (sequential): replay saved session → scrape
all orders → open each receipt → detect refund → one support chat per
customer covering every not-properly-refunded order. Progress streams to the
UI via the EventBus; durable results land in SQLite.
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

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self, scope: dict[str, Any], chat_strategy: str) -> int:
        if self.is_running:
            raise RuntimeError("a run is already active")
        run_id = await db.create_run(scope, chat_strategy)
        self.current_run_id = run_id
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(
            self._run(run_id, scope, chat_strategy))
        return run_id

    def stop(self) -> None:
        self._stop.set()

    async def _resolve_customers(self, scope: dict[str, Any]) -> list[dict]:
        customers = await db.list_customers()
        if scope.get("customer_ids"):
            wanted = set(scope["customer_ids"])
            return [c for c in customers if c["id"] in wanted]
        if scope.get("bucket_date"):
            return [c for c in customers
                    if c["bucket_date"] == scope["bucket_date"]]
        return []

    async def _run(self, run_id: int, scope: dict[str, Any],
                   strategy_name: str) -> None:
        def emit(type: str, data: dict | None = None) -> None:
            bus.publish(type, data or {}, run_id=run_id)

        stats = {"customers": 0, "checked": 0, "not_refunded": 0,
                 "chats_started": 0, "chats_won": 0, "blocked": 0,
                 "manual": 0, "sessions_expired": 0, "errors": 0}
        status = "completed"
        try:
            # Playwright + browser modules are imported lazily so the API
            # process starts fast and tests never need them.
            from playwright.async_api import async_playwright

            from backend.browser.chat import run_chat
            from backend.browser.chat_strategy import (ChatContext,
                                                       ProblemOrder,
                                                       get_strategy)
            from backend.browser.driver import (SessionExpiredError,
                                                launch_browser,
                                                new_customer_context)
            from backend.browser.orders import open_receipt, scrape_orders
            from backend.browser.refund_detector import detect

            customers = await self._resolve_customers(scope)
            chat_cfg = await db.get_setting("chat")
            refund_cfg = await db.get_setting("refund_signal")
            llm_cfg = await db.get_setting("llm")
            browser_cfg = await db.get_setting("browser")
            api_key_override = await db.get_setting("openrouter_api_key")

            emit("run_started", {"scope": scope, "chat_strategy": strategy_name,
                                 "customer_count": len(customers)})

            throttle_s = 0.0
            last_block_t = 0.0

            async with async_playwright() as p:
                for pos, cust in enumerate(customers, start=1):
                    if self._stop.is_set():
                        status = "stopped"
                        break
                    name = (f"{cust['first_name']} {cust['last_name']}".strip()
                            or f"Customer {cust['id']}")
                    emit("customer_started", {"customer_id": cust["id"],
                                              "name": name, "position": pos,
                                              "total": len(customers)})
                    stats["customers"] += 1
                    browser = await launch_browser(
                        p, bool(browser_cfg.get("headless", False)))
                    try:
                        try:
                            ctx = await new_customer_context(
                                browser, cust["storage_state_path"],
                                cust["cookies_path"],
                                viewport=tuple(browser_cfg.get("viewport",
                                                               [1400, 900])))
                            page = await ctx.new_page()
                            scraped = await scrape_orders(page, emit=emit)
                        except SessionExpiredError:
                            stats["sessions_expired"] += 1
                            await db.update_customer(cust["id"],
                                                     session_status="expired")
                            emit("session_invalid", {"customer_id": cust["id"]})
                            continue
                        emit("orders_found", {"customer_id": cust["id"],
                                              "count": len(scraped)})

                        problems: list[tuple[int, Any]] = []
                        for so in scraped:
                            if self._stop.is_set():
                                break
                            oid = await db.upsert_order(
                                cust["id"], so.order_uuid, so.receipt_url,
                                store_name=so.store_name,
                                description=so.description,
                                items_count=so.items_count, price=so.price,
                                order_status=so.order_status.value)
                            emit("order_checking",
                                 {"order_id": oid, "store": so.store_name,
                                  "url": so.receipt_url})
                            try:
                                text = await open_receipt(page, so.receipt_url)
                                rr = detect(text, refund_cfg)
                            except Exception as exc:  # one bad receipt ≠ dead run
                                stats["errors"] += 1
                                await db.add_run_order(run_id, oid, cust["id"],
                                                       error=str(exc))
                                emit("log", {"level": "error",
                                             "message": f"receipt check failed: {exc}"})
                                continue
                            await db.update_order_refund(
                                oid, rr.status.value, rr.total_amount,
                                rr.refund_amount)
                            await db.add_run_order(run_id, oid, cust["id"],
                                                   refund_status=rr.status.value)
                            emit("order_checked",
                                 {"order_id": oid,
                                  "refund_status": rr.status.value,
                                  "total_amount": rr.total_amount,
                                  "refund_amount": rr.refund_amount})
                            stats["checked"] += 1
                            if rr.status in (RefundStatus.not_refunded,
                                             RefundStatus.partial):
                                stats["not_refunded"] += 1
                                problems.append((oid, so))

                        if problems and strategy_name != "none" \
                                and not self._stop.is_set():
                            # Ported auto-throttle: back off when DoorDash
                            # started blocking chats, decay after quiet time.
                            now = time.monotonic()
                            if last_block_t and now - last_block_t > THROTTLE_DECAY_S:
                                throttle_s = 0.0
                            if throttle_s:
                                emit("log", {"level": "info",
                                             "message": f"throttle: waiting {throttle_s:.0f}s"})
                                await asyncio.sleep(throttle_s)

                            opening = chat_cfg["opening_template"].format(
                                order_count=len(problems),
                                amounts=_amounts_text(
                                    [so.price for _, so in problems]))
                            order_ids = [oid for oid, _ in problems]
                            chat_id = await db.create_chat(
                                run_id, cust["id"], order_ids, opening)
                            emit("chat_opened", {"chat_id": chat_id,
                                                 "customer_id": cust["id"],
                                                 "order_ids": order_ids})
                            stats["chats_started"] += 1

                            async def record(direction: str, content: str,
                                             _cid: int = chat_id) -> None:
                                await db.add_chat_message(_cid, direction,
                                                          content)
                                emit("chat_message", {"chat_id": _cid,
                                                      "direction": direction,
                                                      "content": content})

                            chat_ctx = ChatContext(
                                customer_name=cust["first_name"] or name,
                                orders=[ProblemOrder(
                                    order_id=oid, order_uuid=so.order_uuid,
                                    store_name=so.store_name, price=so.price,
                                    refund_status="not_refunded")
                                    for oid, so in problems],
                                opening_message=opening,
                                config={**chat_cfg,
                                        "llm_system_prompt":
                                            llm_cfg.get("system_prompt") or None,
                                        "llm_model": llm_cfg.get("model"),
                                        "openrouter_api_key":
                                            api_key_override or None})
                            strategy = get_strategy(strategy_name)
                            outcome, agent_reached = await run_chat(
                                page, strategy, chat_ctx, chat_cfg=chat_cfg,
                                emit=emit, record=record)
                            await db.finish_chat(chat_id, outcome,
                                                 agent_reached)
                            emit("chat_outcome", {"chat_id": chat_id,
                                                  "outcome": outcome,
                                                  "agent_reached": agent_reached})
                            if outcome == "success":
                                stats["chats_won"] += 1
                            elif outcome in ("blocked", "review_blocked"):
                                stats["blocked"] += 1
                                last_block_t = time.monotonic()
                                throttle_s = min(throttle_s + THROTTLE_STEP_S,
                                                 THROTTLE_CAP_S)
                            elif outcome == "manual_flag":
                                stats["manual"] += 1
                    except Exception as exc:
                        stats["errors"] += 1
                        emit("log", {"level": "error",
                                     "message": f"customer failed: {exc}"})
                    finally:
                        await browser.close()
                    emit("customer_done", {"customer_id": cust["id"],
                                           "stats": dict(stats)})

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


manager = RunManager()
