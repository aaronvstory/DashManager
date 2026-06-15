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
    count: int = 1                      # batch size (normal: 4-6)
    batch_label: str | None = None      # base label; ' - claude' is appended


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
    """Everything the DB viewer needs: customers (+pills) and their orders,
    each order enriched with its self-claim records, full chat transcripts, and
    a derived ``resolution`` (HOW the refund happened + the proof line) — so the
    customer view can show the method category and the transcript inline,
    matching the daily report.
    """
    from backend.report import resolution_method

    rows = await db.list_customers()
    out = []
    for c in rows:
        c = dict(c)
        c["pills"] = _derive_pills(c)
        orders = []
        for o in await db.list_orders(c["id"]):
            o = dict(o)
            o["claims"] = await db.list_claims_for_order(o["id"])
            chats = []
            for ch in await db.list_chats_for_order(o["id"]):
                ch = dict(ch)
                ch["messages"] = await db.list_chat_messages(ch["id"])
                chats.append(ch)
            o["chats"] = chats
            label, confirmation = resolution_method(o)
            o["resolution"] = {"label": label, "confirmation": confirmation}
            orders.append(o)
        c["orders"] = orders
        out.append(c)
    return {"customers": out}


async def _run_login(bucket_date: str | None) -> None:
    import shutil as _shutil

    temp_profile: str | None = None
    try:
        # Lazy: pulls in Playwright, which must not load at app boot.
        from backend.browser.session import manual_login_and_capture

        bus.publish("login_waiting")
        storage, cookies, profile, temp_profile = \
            await manual_login_and_capture(emit=lambda t, d: bus.publish(t, d))

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
        # Adopt the temp login profile as the customer's persistent profile;
        # the finally cleans temp_profile if this move never happened.
        from backend.browser.driver import profile_dir
        dest = profile_dir(cid)
        _shutil.rmtree(dest, ignore_errors=True)
        _shutil.move(str(temp_profile), str(dest))
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
        # Clean the temp login profile if it wasn't adopted (a successful
        # move leaves the path gone, so this is then a no-op).
        if temp_profile:
            _shutil.rmtree(temp_profile, ignore_errors=True)


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
    from datetime import datetime, timezone

    # Outer guard: a failure BEFORE the loop (settings fetch, bad count, import)
    # would otherwise propagate out of the asyncio task and get swallowed,
    # leaving the dialog stuck in "running" with no terminal event. Always emit
    # something terminal.
    count = max(1, int(body.count or 1))
    try:
        from backend.account_creator import create_account

        daisy_cfg = await db.get_setting("daisy")
        origin = body.location_origin or daisy_cfg.get("location_origin")
        radius = (body.radius_miles if body.radius_miles is not None
                  else float(daisy_cfg.get("radius_miles", 5.0)))
        # Stamp a shared batch id/label so the run groups under one
        # '<label> - claude' batch in CustomerDaisy (and the in-app Batch OTP
        # view). The '- claude' suffix is the user's naming convention.
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        base = (body.batch_label or f"batch {stamp}").strip()
        batch_label = base if base.endswith("- claude") else f"{base} - claude"
        batch_id = f"claude_{stamp}"
        # Send `of` (mirrors batch_progress/batch_done) so the dialog reads one
        # consistent key; keep `count` for back-compat.
        bus.publish("batch_started", {"of": count, "count": count,
                                      "batch_label": batch_label})

        created = 0
        for i in range(count):
            bus.publish("batch_progress", {"index": i + 1, "of": count,
                                           "created": created})
            try:
                await create_account(
                    location_origin=origin, radius_miles=radius,
                    bucket_date=body.bucket_date,
                    daisy_root=daisy_cfg.get("root"),
                    headless=body.headless,
                    batch_id=batch_id, batch_label=batch_label)
                created += 1
            except Exception as e:  # one account's failure never aborts a batch
                bus.publish("account_failed", {"error": str(e),
                                               "index": i + 1, "of": count})
        bus.publish("batch_done", {"created": created, "of": count,
                                   "batch_label": batch_label})
    except Exception as e:  # pre-loop / setup failure — still terminate the UI
        bus.publish("account_failed", {"error": str(e), "index": 0, "of": count})
        bus.publish("batch_done", {"created": 0, "of": count})


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


@router.get("/otp-live")
async def otp_live(bucket_date: str | None = None, ids: str | None = None
                   ) -> dict[str, Any]:
    """Latest OTP for every customer in a bucket — for the live-table view.

    NON-BLOCKING single pass (unlike POST /{cid}/fetch-otp which waits up to
    ~2 min): returns whatever code api.cc has RIGHT NOW for each customer, so
    the UI can poll this every few seconds and render a live grid like
    CustomerDaisy's "Live SMS Codes". `ids` is a comma-separated customer-id
    list (overrides `bucket_date`); otherwise `bucket_date` (default today).
    Each row is {id, name, phone, code, error}; one customer's failure never
    aborts the batch. `fetched_at` is the server's UTC timestamp so the UI can
    show code freshness (api.cc codes expire ~30s).
    """
    import re

    from backend.otp_fetch import fetch_bucket_otps

    id_list = None
    if ids:
        try:
            id_list = [int(x) for x in ids.split(",") if x.strip()]
        except ValueError:
            raise HTTPException(status_code=400,
                                detail="ids must be comma-separated integers")
    # Validate the date shape (consistent with the ids check) so a malformed
    # ?bucket_date=foo 400s instead of silently returning an empty 200.
    if bucket_date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", bucket_date):
        raise HTTPException(status_code=400,
                            detail="bucket_date must be YYYY-MM-DD")
    rows = await fetch_bucket_otps(bucket_date, id_list)
    return {"rows": rows,
            "fetched_at": datetime.now(timezone.utc).isoformat()}


@router.get("/daisy-batches")
async def daisy_batches() -> dict[str, Any]:
    """List CustomerDaisy batches Claude created (named '<label> - claude'),
    with per-account name/email/phone, for the in-app batch OTP view."""
    from backend.daisy_batches import list_batches

    daisy_cfg = await db.get_setting("daisy")
    batches = await list_batches(daisy_root=daisy_cfg.get("root"))
    return {"batches": batches}


@router.get("/daisy-batch-otps")
async def daisy_batch_otps(batch_id: str | None = None,
                           batch_label: str | None = None) -> dict[str, Any]:
    """Latest live OTP for each account in a CustomerDaisy batch (single pass,
    pollable). Pass batch_id (preferred) or batch_label."""
    from backend.daisy_batches import batch_otps

    if not batch_id and not batch_label:
        raise HTTPException(status_code=400,
                            detail="batch_id or batch_label required")
    daisy_cfg = await db.get_setting("daisy")
    return await batch_otps(batch_id, batch_label,
                            daisy_root=daisy_cfg.get("root"))


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
    # Remove the customer's persistent Chromium profile dir too. rmtree on a
    # large profile can block for seconds, so off-load it from the event loop.
    from backend.browser.driver import remove_profile
    try:
        await asyncio.to_thread(remove_profile, cid)
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
    from backend.browser.driver import (SessionExpiredError, customer_profile,
                                        export_storage_state)

    browser_cfg = await db.get_setting("browser")
    override = (body or ReloginBody()).headless
    headless = override if override is not None else bool(browser_cfg["headless"])
    result = None
    try:
        async with async_playwright() as p:
            # Per-customer lock (via customer_profile) so this can't collide
            # with a run/relogin already driving this customer's profile.
            async with customer_profile(
                    p, cid, headless=headless,
                    seed_storage_state=row.get("storage_state_path") or None,
                    viewport=tuple(browser_cfg["viewport"])) as ctx:
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                result = await orders.scrape_orders_full(page)
                await export_storage_state(ctx, cid)
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
