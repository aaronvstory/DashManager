"""Customer CRUD, interactive login capture, and saved-session testing.

Browser modules (Playwright) are imported lazily inside handlers so the app
boots instantly and tests never load Playwright.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend import config, db
from backend.events import bus

router = APIRouter()

# Single login capture at a time: acquired in the endpoint (so a second POST
# 409s immediately) and released by the background task when it finishes.
_login_lock = asyncio.Lock()
# Strong reference so the background task is never garbage-collected mid-run.
_login_task: asyncio.Task | None = None


class LoginBody(BaseModel):
    bucket_date: str | None = None


class CustomerPatch(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    phone: str | None = None
    bucket_date: str | None = None
    notes: str | None = None


@router.get("")
async def list_customers() -> dict[str, Any]:
    return {"customers": await db.list_customers()}


async def _run_login(bucket_date: str | None) -> None:
    try:
        # Lazy: pulls in Playwright, which must not load at app boot.
        from backend.browser.session import login_and_capture

        bus.publish("login_waiting")
        storage, cookies, profile = await login_and_capture(
            emit=lambda t, d: bus.publish(t, d))

        bucket = bucket_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        cid = await db.create_customer(
            bucket,
            first_name=profile.first_name,
            last_name=profile.last_name,
            email=profile.email,
            phone=profile.phone,
        )

        storage_dest = config.SESSIONS_DIR / f"{cid}_storage.json"
        cookies_dest = config.SESSIONS_DIR / f"{cid}_cookies.json"
        Path(storage).replace(storage_dest)
        Path(cookies).replace(cookies_dest)
        await db.update_customer(
            cid,
            storage_state_path=str(storage_dest),
            cookies_path=str(cookies_dest),
        )

        bus.publish("login_captured", {
            "customer_id": cid,
            "name": f"{profile.first_name} {profile.last_name}".strip(),
            "email": profile.email,
        })
    except Exception as e:  # noqa: BLE001 — surfaced to the UI as an event
        bus.publish("login_failed", {"error": str(e)})
    finally:
        _login_lock.release()


@router.post("/login")
async def start_login(body: LoginBody | None = None) -> dict[str, Any]:
    global _login_task
    if _login_lock.locked():
        raise HTTPException(status_code=409,
                            detail="a login capture is already running")
    await _login_lock.acquire()
    _login_task = asyncio.create_task(
        _run_login(body.bucket_date if body else None))
    return {"started": True}


@router.patch("/{cid}")
async def update_customer(cid: int, body: CustomerPatch) -> dict[str, Any]:
    if await db.get_customer(cid) is None:
        raise HTTPException(status_code=404, detail="customer not found")
    fields = body.model_dump(exclude_unset=True, exclude_none=True)
    if fields:
        await db.update_customer(cid, **fields)
    row = await db.get_customer(cid)
    if row is None:  # not assert — must survive `python -O`
        raise HTTPException(status_code=500,
                            detail="customer vanished after update")
    return row


@router.delete("/{cid}")
async def delete_customer(cid: int) -> dict[str, Any]:
    row = await db.get_customer(cid)
    if row is None:
        raise HTTPException(status_code=404, detail="customer not found")
    for p in (row.get("storage_state_path"), row.get("cookies_path")):
        if p:
            try:
                Path(p).unlink(missing_ok=True)
            except OSError:
                pass  # locked/permission-denied file must not block DB delete
    await db.delete_customer(cid)
    return {"ok": True}


@router.post("/{cid}/test-session")
async def test_session(cid: int) -> dict[str, Any]:
    row = await db.get_customer(cid)
    if row is None:
        raise HTTPException(status_code=404, detail="customer not found")

    # Lazy: Playwright + browser modules load only when actually testing.
    from playwright.async_api import async_playwright

    from backend.browser import driver, orders
    from backend.browser.driver import SessionExpiredError

    browser_cfg = await db.get_setting("browser")
    try:
        async with async_playwright() as p:
            browser = await driver.launch_browser(
                p, headless=bool(browser_cfg["headless"]))
            try:
                ctx = await driver.new_customer_context(
                    browser,
                    row["storage_state_path"],
                    row["cookies_path"],
                    viewport=tuple(browser_cfg["viewport"]),
                )
                page = await ctx.new_page()
                scraped = await orders.scrape_orders(page)
            finally:
                await browser.close()
    except SessionExpiredError:
        await db.update_customer(cid, session_status="expired")
        return {"ok": False, "error": "session_expired"}

    await db.update_customer(cid, session_status="active")
    return {"ok": True, "orders_count": len(scraped)}
