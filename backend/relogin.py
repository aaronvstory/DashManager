"""Fresh-login orchestrator + on-demand OTP for an existing customer.

`fetch_otp_for_customer` grabs a single fresh code from the customer's saved
api.cc number (for manual phone login). `relogin_customer` drives a full headed
email+password+OTP login and captures a new storage_state + cookies.

Both read the credentials stored on the customer row (SCHEMA_V2): password,
number_token, api_url, mirror_hosts. Password falls back to the configured
default (CustomerDaisy uses one shared password for all accounts).
"""
from __future__ import annotations

import json
import time
from typing import Any

from backend import db
from backend.daisy.bridge import DaisyBridge
from backend.events import bus


async def _resolve_password(customer: dict[str, Any]) -> str:
    if customer.get("password"):
        return customer["password"]
    daisy_cfg = await db.get_setting("daisy")
    return daisy_cfg.get("default_password", "")


def _token_fields(customer: dict[str, Any]) -> tuple[str, str, list[str]]:
    token = customer.get("number_token") or ""
    api_url = customer.get("api_url") or ""
    try:
        hosts = json.loads(customer.get("mirror_hosts") or "[]")
    except (json.JSONDecodeError, TypeError):
        hosts = []
    return token, api_url, hosts


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
                           headless: bool | None = None) -> dict[str, Any]:
    """Fresh login + OTP + session capture for one customer.

    `headless` overrides the browser setting for this login (None = setting).
    """
    from playwright.async_api import async_playwright

    from backend.browser.driver import customer_profile, export_storage_state
    from backend.browser.login_flow import login_and_capture

    customer = await db.get_customer(customer_id)
    if customer is None:
        raise ValueError("customer not found")
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
                    await db.update_customer(
                        customer_id, storage_state_path=storage,
                        session_status="active")

    if outcome != "logged_in":
        _emit("relogin_failed", {"customer_id": customer_id,
                                 "outcome": outcome})
        raise RuntimeError(f"login failed (outcome={outcome})")
    _emit("relogin_done", {"customer_id": customer_id})
    return {"customer_id": customer_id, "outcome": outcome}
