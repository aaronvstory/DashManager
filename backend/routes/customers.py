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
# 409s immediately). Strong reference so the background task is never
# garbage-collected mid-run; its done-ness is the single source of "running".
_login_task: asyncio.Task | None = None


class LoginBody(BaseModel):
    bucket_date: str | None = None


class CreateAccountBody(BaseModel):
    bucket_date: str | None = None
    location_origin: str | None = None  # falls back to daisy settings default
    radius_miles: float | None = None
    headless: bool | None = None        # per-action override of the setting


_create_task: asyncio.Task | None = None
_relogin_task: asyncio.Task | None = None


def _task_running(task: asyncio.Task | None) -> bool:
    """A task is 'running' only while it actually exists and isn't done.

    Using the live task (not a Lock acquired outside it) avoids the failure
    mode where create_task's coroutine never reaches a finally and a Lock
    stays held forever — a done/None task is simply not running.
    """
    return task is not None and not task.done()


class CustomerPatch(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    phone: str | None = None
    bucket_date: str | None = None
    notes: str | None = None


def _derive_pills(c: dict[str, Any]) -> dict[str, Any]:
    """Lifecycle/session pills for a customer row (for UI + DB viewer)."""
    from backend.browser.driver import profile_exists

    has_profile = False
    try:
        has_profile = profile_exists(c["id"])
    except Exception:
        pass
    has_storage = bool(c.get("storage_state_path")
                       and Path(c["storage_state_path"]).exists())
    has_token = bool(c.get("number_token"))
    session = c.get("session_status", "invalid")
    # lifecycle: created -> logged_in (session active + a profile/storage)
    logged_in = session == "active" and (has_profile or has_storage)
    return {
        "lifecycle": "logged_in" if logged_in else "created",
        "session_status": session,
        "has_session": has_profile or has_storage,
        "has_profile": has_profile,
        "has_storage_backup": has_storage,
        "has_number_token": has_token,
    }


@router.get("")
async def list_customers() -> dict[str, Any]:
    rows = await db.list_customers()
    for c in rows:
        c["pills"] = _derive_pills(c)
    return {"customers": rows}


@router.get("/full")
async def customers_full() -> dict[str, Any]:
    """Everything the DB viewer needs: customers (+pills) and their orders."""
    rows = await db.list_customers()
    out = []
    for c in rows:
        c = dict(c)
        c["pills"] = _derive_pills(c)
        c["orders"] = await db.list_orders(c["id"])
        out.append(c)
    return {"customers": out}


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


@router.post("/login")
async def start_login(body: LoginBody | None = None) -> dict[str, Any]:
    global _login_task
    # Synchronous check-then-create: no await between the check and
    # create_task, so two requests can't both pass; and a done/None task is
    # never "running" (no lock to leak if a task fails to start).
    if _task_running(_login_task):
        raise HTTPException(status_code=409,
                            detail="a login capture is already running")
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
            bucket_date=body.bucket_date, daisy_root=daisy_cfg.get("root"),
            headless=body.headless)
    except Exception as e:  # surfaced to the UI as an event
        bus.publish("account_failed", {"error": str(e)})


@router.post("/create-account")
async def create_account_route(body: CreateAccountBody | None = None
                               ) -> dict[str, Any]:
    global _create_task
    if _task_running(_create_task):
        raise HTTPException(status_code=409,
                            detail="an account creation is already running")
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


class ReloginBody(BaseModel):
    headless: bool | None = None  # per-action override of the setting


async def _run_relogin(cid: int, headless: bool | None) -> None:
    try:
        from backend.relogin import relogin_customer
        await relogin_customer(cid, headless=headless)
    except Exception as e:
        bus.publish("relogin_failed", {"customer_id": cid, "error": str(e)})


@router.post("/{cid}/relogin")
async def relogin(cid: int, body: ReloginBody | None = None) -> dict[str, Any]:
    """Fresh login (email+password+OTP) and capture a new session."""
    global _relogin_task
    if await db.get_customer(cid) is None:
        raise HTTPException(status_code=404, detail="customer not found")
    if _task_running(_relogin_task):
        raise HTTPException(status_code=409, detail="a login is already running")
    _relogin_task = asyncio.create_task(
        _run_relogin(cid, (body or ReloginBody()).headless))
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
async def test_session(cid: int, body: ReloginBody | None = None
                       ) -> dict[str, Any]:
    """Replay the session, scrape orders, report state. Persists order rows."""
    import json as _json

    row = await db.get_customer(cid)
    if row is None:
        raise HTTPException(status_code=404, detail="customer not found")

    from playwright.async_api import async_playwright  # lazy

    from backend.browser import orders
    from backend.browser.driver import (SessionExpiredError,
                                        export_storage_state,
                                        open_customer_profile)

    browser_cfg = await db.get_setting("browser")
    override = (body or ReloginBody()).headless
    headless = override if override is not None else bool(browser_cfg["headless"])
    result = None
    try:
        async with async_playwright() as p:
            ctx = None
            try:
                ctx = await open_customer_profile(
                    p, cid, headless=headless,
                    seed_storage_state=row.get("storage_state_path") or None,
                    viewport=tuple(browser_cfg["viewport"]))
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                result = await orders.scrape_orders_full(page)
                await export_storage_state(ctx, cid)
            finally:
                if ctx is not None:
                    await ctx.close()
    except SessionExpiredError:
        await db.update_customer(cid, session_status="expired")
        return {"ok": False, "error": "session_expired"}

    # Replace in-progress orders (no stable identity) before re-persisting, so
    # phantom rows from completed/vanished live orders don't accumulate.
    await db.clear_in_progress_orders(cid)
    for so in result.orders:
        await db.upsert_order(
            cid, so.order_uuid, so.receipt_url, store_name=so.store_name,
            description=so.description, items_count=so.items_count,
            price=so.price, order_status=so.order_status.value,
            status_text=so.status_text, dasher_name=so.dasher_name)
    await db.update_customer(cid, session_status="active")
    return {"ok": True, "orders_count": len(result.orders),
            "state": result.state,
            "in_progress_count": result.in_progress_count,
            "completed_count": result.completed_count,
            "orders": _json.loads(
                _json.dumps([o.model_dump() for o in result.orders]))}
