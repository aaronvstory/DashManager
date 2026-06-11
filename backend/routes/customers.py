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


class CreateAccountBody(BaseModel):
    bucket_date: str | None = None
    location_origin: str | None = None  # falls back to daisy settings default
    radius_miles: float | None = None


_create_lock = asyncio.Lock()
_create_task: asyncio.Task | None = None
_relogin_lock = asyncio.Lock()
_relogin_task: asyncio.Task | None = None


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
        storage, cookies, profile, temp_profile = await login_and_capture(
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
        # Adopt the temp login profile as the customer's persistent profile.
        import shutil as _shutil

        from backend.browser.driver import profile_dir
        dest = profile_dir(cid)
        _shutil.rmtree(dest, ignore_errors=True)
        try:
            _shutil.move(temp_profile, str(dest))
        except Exception:
            _shutil.rmtree(temp_profile, ignore_errors=True)
        await db.update_customer(
            cid,
            storage_state_path=str(storage_dest),
            cookies_path=str(cookies_dest),
            session_status="active",
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


async def _run_create_account(body: CreateAccountBody) -> None:
    try:
        from backend.account_creator import create_account

        daisy_cfg = await db.get_setting("daisy")
        origin = body.location_origin or daisy_cfg.get("location_origin")
        radius = (body.radius_miles if body.radius_miles is not None
                  else float(daisy_cfg.get("radius_miles", 5.0)))
        await create_account(
            location_origin=origin, radius_miles=radius,
            bucket_date=body.bucket_date, daisy_root=daisy_cfg.get("root"))
    except Exception as e:  # surfaced to the UI as an event
        bus.publish("account_failed", {"error": str(e)})
    finally:
        _create_lock.release()


@router.post("/create-account")
async def create_account_route(body: CreateAccountBody | None = None
                               ) -> dict[str, Any]:
    global _create_task
    if _create_lock.locked():
        raise HTTPException(status_code=409,
                            detail="an account creation is already running")
    await _create_lock.acquire()
    _create_task = asyncio.create_task(
        _run_create_account(body or CreateAccountBody()))
    return {"started": True}


class ImportBody(BaseModel):
    customer_ids: list[str]  # CustomerDaisy customer_id (UUID) values
    bucket_date: str | None = None


@router.get("/daisy/recent")
async def daisy_recent(limit: int = 20) -> dict[str, Any]:
    """Recent CustomerDaisy accounts available to import (with OTP tokens)."""
    from backend.daisy.bridge import DaisyBridge

    daisy_cfg = await db.get_setting("daisy")
    async with DaisyBridge(root=daisy_cfg.get("root")) as d:
        return {"customers": await d.list_recent_customers(limit)}


@router.post("/daisy/import")
async def daisy_import(body: ImportBody) -> dict[str, Any]:
    """Import chosen CustomerDaisy accounts as DashManager customers."""
    import json as _json

    from backend.daisy.bridge import DaisyBridge

    daisy_cfg = await db.get_setting("daisy")
    bucket = body.bucket_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    wanted = set(body.customer_ids)
    async with DaisyBridge(root=daisy_cfg.get("root")) as d:
        recent = await d.list_recent_customers(100)
    imported = []
    for c in recent:
        if c["customer_id"] not in wanted:
            continue
        cid = await db.create_customer(
            bucket,
            first_name=c.get("first_name", ""),
            last_name=c.get("last_name", ""),
            email=c.get("email", ""),
            phone=c.get("phone", ""),
            password=c.get("password", ""),
            number_token=c.get("number_token", ""),
            api_url=c.get("api_url", ""),
            mirror_hosts=_json.dumps(c.get("mirror_hosts", [])),
            session_status="invalid",  # no session yet — needs login
            notes=f"imported from CustomerDaisy · {c.get('full_address','')}")
        imported.append({"id": cid, "name":
                         f"{c.get('first_name','')} {c.get('last_name','')}"
                         .strip()})
    return {"imported": imported}


@router.get("/daisy/locations")
async def daisy_locations() -> dict[str, Any]:
    """Predefined CustomerDaisy starting locations for the create-account UI."""
    from backend.daisy.bridge import DaisyBridge

    daisy_cfg = await db.get_setting("daisy")
    async with DaisyBridge(root=daisy_cfg.get("root")) as d:
        return {"locations": await d.locations(),
                "balance": await d.balance()}


@router.post("/{cid}/fetch-otp")
async def fetch_otp(cid: int) -> dict[str, Any]:
    """Grab a fresh verification code from the customer's saved number.

    For manual login (e.g. on a phone). Blocks until a code arrives or times
    out, then returns it for display.
    """
    from backend.relogin import fetch_otp_for_customer

    try:
        return await fetch_otp_for_customer(cid)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


async def _run_relogin(cid: int) -> None:
    try:
        from backend.relogin import relogin_customer
        await relogin_customer(cid)
    except Exception as e:
        bus.publish("relogin_failed", {"customer_id": cid, "error": str(e)})
    finally:
        _relogin_lock.release()


@router.post("/{cid}/relogin")
async def relogin(cid: int) -> dict[str, Any]:
    """Headed fresh login (email+password+OTP) and capture a new session."""
    global _relogin_task
    if await db.get_customer(cid) is None:
        raise HTTPException(status_code=404, detail="customer not found")
    if _relogin_lock.locked():
        raise HTTPException(status_code=409, detail="a login is already running")
    await _relogin_lock.acquire()
    _relogin_task = asyncio.create_task(_run_relogin(cid))
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
    # Remove the customer's persistent Chromium profile dir too.
    from backend.browser.driver import remove_profile
    try:
        remove_profile(cid)
    except Exception:
        pass
    await db.delete_customer(cid)
    return {"ok": True}


@router.post("/{cid}/test-session")
async def test_session(cid: int) -> dict[str, Any]:
    row = await db.get_customer(cid)
    if row is None:
        raise HTTPException(status_code=404, detail="customer not found")

    # Lazy: Playwright + browser modules load only when actually testing.
    from playwright.async_api import async_playwright

    from backend.browser import orders
    from backend.browser.driver import (SessionExpiredError,
                                        export_storage_state,
                                        open_customer_profile)

    browser_cfg = await db.get_setting("browser")
    scraped = []
    try:
        async with async_playwright() as p:
            ctx = None
            try:
                ctx = await open_customer_profile(
                    p, cid, headless=bool(browser_cfg["headless"]),
                    seed_storage_state=row.get("storage_state_path") or None,
                    viewport=tuple(browser_cfg["viewport"]))
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                scraped = await orders.scrape_orders(page)
                await export_storage_state(ctx, cid)
            finally:
                if ctx is not None:
                    await ctx.close()
    except SessionExpiredError:
        await db.update_customer(cid, session_status="expired")
        return {"ok": False, "error": "session_expired"}

    await db.update_customer(cid, session_status="active")
    return {"ok": True, "orders_count": len(scraped)}
