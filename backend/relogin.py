"""Fresh-login orchestrator + on-demand OTP for an existing customer.

`fetch_otp_for_customer` grabs a single fresh code from the customer's saved
api.cc number (for manual phone login). `relogin_customer` drives a full headed
email+password+OTP login and captures a new storage_state + cookies.

Both read the credentials stored on the customer row (SCHEMA_V2): password,
number_token, api_url, mirror_hosts. Password falls back to the configured
default (CustomerDaisy uses one shared password for all accounts).
"""
from __future__ import annotations

import time
from typing import Any

from backend import db
from backend.daisy.bridge import DaisyBridge
from backend.events import bus
from backend.otp_fetch import _loads_list


async def _resolve_password(customer: dict[str, Any]) -> str:
    if customer.get("password"):
        return customer["password"]
    daisy_cfg = await db.get_setting("daisy")
    return daisy_cfg.get("default_password", "")


def _token_fields(customer: dict[str, Any]) -> tuple[str, str, list[str]]:
    # mirror_hosts is a JSON-string TEXT column, but a caller may hand us the
    # row before it's serialized (already a list). The shared _loads_list handles
    # both so a list-shaped value isn't silently dropped to [] (which would lose
    # the api.cc mirror hosts the OTP poll needs).
    token = customer.get("number_token") or ""
    api_url = customer.get("api_url") or ""
    return token, api_url, _loads_list(customer.get("mirror_hosts"))


async def fetch_otp_for_customer(customer_id: int, *,
                                 wait_s: float = 120) -> dict[str, Any]:
    """Poll the customer's number for a fresh OTP. Returns {code, sms_text}.

    Blocks (inside its own bridge) until a code arrives or wait_s elapses, so
    the UI shows a spinner then the code. Raises if the customer has no token.
    """
    customer = await db.get_customer(customer_id)
    if customer is None:
        raise ValueError("customer not found")
    token, api_url, hosts = _token_fields(customer)
    if not token:
        raise ValueError("customer has no saved number token "
                         "(created outside the account flow?)")
    daisy_cfg = await db.get_setting("daisy")
    deadline = time.monotonic() + wait_s
    async with DaisyBridge(root=daisy_cfg.get("root")) as daisy:
        while time.monotonic() < deadline:
            res = await daisy.fetch_otp(token, api_url, hosts)
            if res.get("code"):
                return {"code": res["code"],
                        "sms_text": res.get("sms_text", "")}
            import asyncio
            await asyncio.sleep(4)
    return {"code": "", "sms_text": "", "timeout": True}


def _emit(type: str, data: dict | None = None) -> None:
    bus.publish(type, data or {})


async def relogin_customer(customer_id: int,
                           headless: bool | None = None,
                           wipe_profile: bool = False) -> dict[str, Any]:
    """Fresh login + OTP + session capture for one customer.

    `headless` overrides the browser setting for this login (None = setting).
    `wipe_profile` deletes the on-disk Chromium profile dir BEFORE logging in —
    the reliable recovery from a Cloudflare variant-B block, which is a
    stale/flagged-session signal (verified live 2026-06-12): wiping the profile
    and logging in fresh clears the gate where waits/reloads/fresh-navs do not.
    The wipe must happen under the per-customer profile lock so a concurrent
    run/test-session isn't using the dir; we acquire it here, then customer_
    profile re-acquires the (reentrant-by-ownership) lock — so wipe inside the
    lock, release, then open.
    """
    from playwright.async_api import async_playwright

    from backend.browser.driver import (customer_profile, export_storage_state,
                                         profile_lock, remove_profile)
    from backend.browser.login_flow import login_and_capture

    customer = await db.get_customer(customer_id)
    if customer is None:
        raise ValueError("customer not found")

    if wipe_profile:
        # Hold the lock only for the wipe; customer_profile acquires it again
        # for the open→use→close span below. asyncio.Lock is NOT reentrant, so
        # we must release before customer_profile tries to acquire.
        async with profile_lock(customer_id):
            remove_profile(customer_id)
        _emit("log", {"customer_id": customer_id,
                      "message": "wiped profile dir for fresh relogin"})
    token, api_url, hosts = _token_fields(customer)
    if not token:
        raise ValueError("customer has no saved number token for OTP login")
    password = await _resolve_password(customer)
    if not password:
        raise ValueError("no password on customer and no default configured")

    browser_cfg = await db.get_setting("browser")
    daisy_cfg = await db.get_setting("daisy")
    headless = (headless if headless is not None
                else bool(browser_cfg.get("headless", False)))
    address = {"full_address": (customer.get("notes") or "")}  # best effort
    _emit("relogin_started", {"customer_id": customer_id})

    async with DaisyBridge(root=daisy_cfg.get("root")) as daisy:
        async def poll_otp() -> str:
            res = await daisy.fetch_otp(token, api_url, hosts)
            return res.get("code") or ""

        async with async_playwright() as p:
            outcome = "failed"
            # customer_profile holds the per-customer lock for the whole
            # open->use->close span, so a concurrent run/test-session on this
            # same profile can't collide on Chromium's user-data-dir lock.
            async with customer_profile(
                    p, customer_id, headless,
                    seed_storage_state=customer.get("storage_state_path")
                    or None,
                    viewport=tuple(browser_cfg.get("viewport", [1400, 900]))
                    ) as ctx:
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                outcome = await login_and_capture(
                    page, customer["email"], password, poll_otp,
                    address=address, emit=_emit)
                _emit("relogin_outcome", {"customer_id": customer_id,
                                          "outcome": outcome})
                if outcome == "logged_in":
                    storage = await export_storage_state(ctx, customer_id)
                    # Don't clobber a previously-valid path with an empty
                    # export ("" = capture failed). The live profile dir is
                    # the real session anyway; keep the old backup path.
                    fields = {"session_status": "active"}
                    if storage:
                        fields["storage_state_path"] = storage
                    await db.update_customer(customer_id, **fields)

    if outcome != "logged_in":
        _emit("relogin_failed", {"customer_id": customer_id,
                                 "outcome": outcome})
        raise RuntimeError(f"login failed (outcome={outcome})")
    _emit("relogin_done", {"customer_id": customer_id})
    return {"customer_id": customer_id, "outcome": outcome}


async def phone_login_customer(customer_id: int,
                               headless: bool | None = None) -> dict[str, Any]:
    """Log a customer in via PHONE NUMBER → OTP (no password) + capture session.

    For accounts whose DoorDash password we don't have (e.g. recovered from a
    failed signup). Uses the customer's stored phone + api.cc number: DoorDash
    texts a code to the number, which we poll. Mirrors relogin_customer but
    drives phone_login_and_capture.
    """
    from playwright.async_api import async_playwright

    from backend.browser.driver import customer_profile, export_storage_state
    from backend.browser.login_flow import phone_login_and_capture

    customer = await db.get_customer(customer_id)
    if customer is None:
        raise ValueError("customer not found")
    token, api_url, hosts = _token_fields(customer)
    if not token:
        raise ValueError("customer has no saved number token for OTP login")
    email = customer.get("email") or ""
    if not email:
        raise ValueError("customer has no email for OTP login")

    browser_cfg = await db.get_setting("browser")
    daisy_cfg = await db.get_setting("daisy")
    headless = (headless if headless is not None
                else bool(browser_cfg.get("headless", False)))
    address = {"full_address": (customer.get("notes") or "")}
    _emit("relogin_started", {"customer_id": customer_id, "mode": "otp"})

    async with DaisyBridge(root=daisy_cfg.get("root")) as daisy:
        async def poll_otp() -> str:
            res = await daisy.fetch_otp(token, api_url, hosts)
            return res.get("code") or ""

        async with async_playwright() as p:
            outcome = "failed"
            async with customer_profile(
                    p, customer_id, headless,
                    seed_storage_state=customer.get("storage_state_path")
                    or None,
                    viewport=tuple(browser_cfg.get("viewport", [1400, 900]))
                    ) as ctx:
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                outcome = await phone_login_and_capture(
                    page, email, poll_otp, address=address, emit=_emit)
                _emit("relogin_outcome", {"customer_id": customer_id,
                                          "outcome": outcome, "mode": "phone"})
                if outcome == "logged_in":
                    storage = await export_storage_state(ctx, customer_id)
                    fields = {"session_status": "active"}
                    if storage:
                        fields["storage_state_path"] = storage
                    await db.update_customer(customer_id, **fields)

    if outcome != "logged_in":
        _emit("relogin_failed", {"customer_id": customer_id,
                                 "outcome": outcome, "mode": "phone"})
        raise RuntimeError(f"phone login failed (outcome={outcome})")
    _emit("relogin_done", {"customer_id": customer_id, "mode": "phone"})
    return {"customer_id": customer_id, "outcome": outcome}


async def phone_login_customer_cdp(customer_id: int,
                                   headless: bool = False,
                                   set_address: str | None = None,
                                   instruction: str | None = None
                                   ) -> dict[str, Any]:
    """Phone-number→OTP login via SeleniumBase CDP (beats the login CF gate).

    The plain-Playwright phone_login_customer can't clear the login Cloudflare
    Turnstile. This drives the CDP path (cdp_login.phone_login_via_cdp, os_input)
    which clicks the Turnstile + enters the number with real input, then saves
    the captured storage_state as the customer's session.

    ⚠️ os_input grabs the real cursor — hands-off while running.
    """
    import asyncio

    from backend import config
    from backend.browser.cdp_login import phone_login_via_cdp

    customer = await db.get_customer(customer_id)
    if customer is None:
        raise ValueError("customer not found")
    token, api_url, hosts = _token_fields(customer)
    if not token:
        raise ValueError("customer has no saved number token for OTP login")
    email = customer.get("email") or ""
    if not email:
        raise ValueError("customer has no email for OTP login")

    daisy_cfg = await db.get_setting("daisy")
    loop = asyncio.get_running_loop()
    _emit("relogin_started", {"customer_id": customer_id, "mode": "cdp_phone"})

    async with DaisyBridge(root=daisy_cfg.get("root")) as daisy:
        def _poll_otp_sync() -> str:
            fut = asyncio.run_coroutine_threadsafe(
                daisy.fetch_otp(token, api_url, hosts), loop)
            try:
                return (fut.result(timeout=60.0).get("code") or "")
            except Exception:
                return ""

        shot_dir = None
        if hasattr(config, "SCREENSHOTS_DIR"):
            shot_dir = str(config.SCREENSHOTS_DIR / "login" / f"cust{customer_id}")
        result = await asyncio.to_thread(
            phone_login_via_cdp, email, poll_otp=_poll_otp_sync,
            proxy=None, headless=headless, os_input=True,
            set_address=set_address, instruction=instruction,
            emit=_emit, screenshot_dir=shot_dir)

    outcome = result.get("outcome", "failed")
    _emit("relogin_outcome", {"customer_id": customer_id, "outcome": outcome,
                              "mode": "cdp_phone"})
    if outcome == "logged_in" and result.get("storage_state"):
        from backend.browser.driver import write_storage_state_dict
        path = write_storage_state_dict(customer_id, result["storage_state"])
        fields = {"session_status": "active"}
        if path:
            fields["storage_state_path"] = path
        await db.update_customer(customer_id, **fields)
        _emit("relogin_done", {"customer_id": customer_id, "mode": "cdp_phone"})
        return {"customer_id": customer_id, "outcome": outcome,
                "prefs": result.get("prefs")}

    _emit("relogin_failed", {"customer_id": customer_id, "outcome": outcome,
                             "mode": "cdp_phone"})
    raise RuntimeError(f"cdp phone login failed (outcome={outcome})")


async def is_logged_in(page: Any) -> bool:
    """True if `page` is on a logged-in DoorDash surface (not a login screen).

    Keep-open navigates a kept window to /orders; a valid session stays there,
    an expired one redirects to identity.doordash.com/auth (login). Cheap URL
    check — good enough to decide "open logged-in vs needs login".
    """
    from backend.browser.signup import SUCCESS_URL_MARKERS
    url = (page.url or "").lower()
    if "identity.doordash.com" in url or "/consumer/login" in url:
        return False
    return any(m in url for m in SUCCESS_URL_MARKERS)


async def login_open_page(customer_id: int, page: Any) -> str:
    """Drive a login on an ALREADY-OPEN page (the keep-open window) and capture.

    Unlike `relogin_customer` (which opens its own context), this logs in INSIDE
    the page the keep-open manager already holds — so the same window the user is
    watching ends up logged in, no close/reopen. Picks the email+password flow
    when a password is known, else phone-OTP. Returns the login outcome string.
    """
    from backend.browser.login_flow import (login_and_capture,
                                             phone_login_and_capture)

    customer = await db.get_customer(customer_id)
    if customer is None:
        raise ValueError("customer not found")
    token, api_url, hosts = _token_fields(customer)
    if not token:
        raise ValueError("no saved number token for OTP login")
    password = await _resolve_password(customer)
    daisy_cfg = await db.get_setting("daisy")

    _emit("relogin_started", {"customer_id": customer_id, "mode": "keep_open"})
    async with DaisyBridge(root=daisy_cfg.get("root")) as daisy:
        async def poll_otp() -> str:
            res = await daisy.fetch_otp(token, api_url, hosts)
            return res.get("code") or ""

        if password:
            outcome = await login_and_capture(
                page, customer["email"], password, poll_otp,
                address={"full_address": (customer.get("notes") or "")},
                emit=_emit)
        else:
            outcome = await phone_login_and_capture(
                page, customer["email"], poll_otp, emit=_emit)

    _emit("relogin_outcome", {"customer_id": customer_id, "outcome": outcome,
                              "mode": "keep_open"})
    if outcome == "logged_in":
        await db.update_customer(customer_id, session_status="active")
    return outcome
