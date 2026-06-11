"""Automated fresh login for an existing DoorDash account.

Used when a saved session is missing/expired: drive the email+password login,
clear the 2-Step Verification (the SAME OTP modal as signup) using a code
polled live from the account's rented api.cc number, then capture a fresh
storage_state + cookies for the run pipeline.

Login flow captured live 2026-06-12 (identity.doordash.com/auth?prompt=login):
  email  -> "Continue to Sign In"
  password ("Welcome back") -> "Sign In"
  2-Step Verification (identical OTP dialog) -> code -> "Submit"
  -> doordash.com/home   (then the optional "$0 delivery" address modal)
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from playwright.async_api import Page

from backend.browser.driver import handle_cloudflare, screenshot
from backend.browser.signup import (
    OtpPoller,
    SUCCESS_URL_MARKERS,
    _enter_otp,
    _fill_address_if_present,
    _find_otp_input,
    _notify,
    _resend,
    Emit,
)

# Forces the logged-out password prompt even within a browser that may hold a
# session; the redirect lands back on the consumer site after auth.
LOGIN_URL = (
    "https://identity.doordash.com/auth"
    "?client_id=1666519390426295040&intl=en-US&layout=consumer_web"
    "&prompt=login&redirect_uri=https%3A%2F%2Fwww.doordash.com%2Fpost-login%2F"
    "&response_type=code&scope=%2A&state=%2Fhome"
)

EMAIL_INPUT = "input[type='email']"
PASSWORD_INPUT = "input[type='password']"
CONTINUE_BUTTON = "Continue to Sign In"
SIGNIN_BUTTON = "Sign In"


async def _click_button_text(page: Page, text: str, timeout: float = 8000
                             ) -> bool:
    try:
        await page.get_by_role("button", name=text, exact=False).first.click(
            timeout=timeout)
        return True
    except Exception:
        try:
            await page.locator(f"button:has-text('{text}')").first.click(
                timeout=timeout)
            return True
        except Exception:
            return False


async def login_and_capture(page: Page, email: str, password: str,
                            poll_otp: OtpPoller, *,
                            address: dict[str, Any] | None = None,
                            emit: Emit | None = None,
                            otp_total_wait_s: float = 180,
                            resend_after_s: float = 75) -> str:
    """Drive the full email+password+OTP login on `page`.

    Returns 'logged_in' | 'otp_timeout' | 'otp_failed' | 'bad_credentials'
    | 'failed'. The caller owns the context and captures storage_state when
    this returns 'logged_in'.
    """
    await page.goto(LOGIN_URL, wait_until="domcontentloaded")
    await handle_cloudflare(page)
    await asyncio.sleep(1.0)

    # ── Email step ──
    try:
        em = page.locator(EMAIL_INPUT).first
        await em.click()
        await em.fill(email)
    except Exception:
        await screenshot(page, "login_no_email_field")
        return "failed"
    await _click_button_text(page, CONTINUE_BUTTON)
    await asyncio.sleep(2.0)
    await handle_cloudflare(page)

    # Already authenticated (browser held a session) — short-circuit.
    if any(m in page.url for m in SUCCESS_URL_MARKERS):
        return "logged_in"

    # After the email step DoorDash branches into ONE of two flows (verified
    # live 2026-06-12):
    #   (a) password screen ("Welcome back") -> Sign In -> 2-Step OTP, or
    #   (b) passwordless OTP-first ("Verify code", a 6-box split input).
    # Wait for whichever appears and handle it.
    deadline = time.monotonic() + 20
    branch = None  # "password" | "otp" | "done"
    while time.monotonic() < deadline:
        if any(m in page.url for m in SUCCESS_URL_MARKERS):
            branch = "done"
            break
        kind, _ = await _find_otp_input(page)
        if kind is not None:
            branch = "otp"
            break
        try:
            if await page.locator(PASSWORD_INPUT).first.is_visible():
                branch = "password"
                break
        except Exception:
            pass
        await asyncio.sleep(1.0)

    if branch == "done":
        await _fill_address_if_present(page, address, emit=emit)
        return "logged_in"

    if branch == "password":
        try:
            pw = page.locator(PASSWORD_INPUT).first
            await pw.click()
            await pw.fill(password)
        except Exception:
            await screenshot(page, "login_no_password_field")
            return "failed"
        await _click_button_text(page, SIGNIN_BUTTON)
        await asyncio.sleep(2.5)
        await handle_cloudflare(page)
        if any(m in page.url for m in SUCCESS_URL_MARKERS):
            await _fill_address_if_present(page, address, emit=emit)
            return "logged_in"
        # Password accepted -> 2-Step Verification OTP next.
        otp_deadline = time.monotonic() + 30
        while time.monotonic() < otp_deadline:
            kind, _ = await _find_otp_input(page)
            if kind is not None:
                break
            if any(m in page.url for m in SUCCESS_URL_MARKERS):
                await _fill_address_if_present(page, address, emit=emit)
                return "logged_in"
            if await page.locator(
                    "text=/incorrect|wrong password|try again/i"
                    ).first.is_visible():
                return "bad_credentials"
            await asyncio.sleep(1.0)
        else:
            await screenshot(page, "login_no_otp_modal")
            return "failed"

    if branch is None:
        await screenshot(page, "login_no_branch")
        return "failed"
    # branch == "otp": passwordless flow, OTP input already present.

    _notify(emit, "otp_waiting", {})
    started = time.monotonic()
    tried: set[str] = set()
    last_resend = 0.0
    while time.monotonic() - started < otp_total_wait_s:
        code = await poll_otp()
        if code and code not in tried:
            tried.add(code)
            _notify(emit, "otp_received", {"code": code})
            if await _enter_otp(page, code):
                for _ in range(3):
                    await asyncio.sleep(2.0)
                    await handle_cloudflare(page)
                    if any(m in page.url for m in SUCCESS_URL_MARKERS):
                        await _fill_address_if_present(page, address,
                                                       emit=emit)
                        return "logged_in"
                if await _resend(page, emit):  # expired/rejected → fresh code
                    last_resend = time.monotonic()
                continue
            return "otp_failed"
        if (time.monotonic() - started > resend_after_s
                and time.monotonic() - last_resend > resend_after_s):
            if await _resend(page, emit):
                last_resend = time.monotonic()
        await asyncio.sleep(3.0)
    return "otp_timeout"
