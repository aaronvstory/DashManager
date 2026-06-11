"""DoorDash account-creation driver.

Drives the identity signup form with a CustomerDaisy-generated identity, then
waits for the SMS-verification modal and enters the OTP polled live from
api.cc (via the DaisyBridge). On success the browser is logged in, so the
caller captures storage_state + cookies exactly like the manual-login flow.

Selectors captured live 2026-06-12 against
https://identity.doordash.com/auth/user/signup — the form fields use stable
role+accessible-name locators. The OTP-modal input selectors are a CASCADE
(verified/extended during the first live run; see OTP_INPUT_SELECTORS).
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any, Awaitable, Callable

from playwright.async_api import Page

from backend.browser.driver import handle_cloudflare, screenshot

# Signup form — direct URL avoids the homepage modal ambiguity. state is a
# throwaway value; DoorDash issues a real one during the flow.
SIGNUP_URL = (
    "https://identity.doordash.com/auth/user/signup"
    "?client_id=1666519390426295040&intl=en-US&layout=consumer_web"
    "&prompt=none&redirect_uri=https%3A%2F%2Fwww.doordash.com%2Fpost-login%2F"
    "&response_type=code&scope=%2A&state=%2Fhome"
)

# Field accessible names (all role=textbox). Captured live 2026-06-12.
FIELD_FIRST = "First Name"
FIELD_LAST = "Last Name"
FIELD_EMAIL = "Email"
FIELD_MOBILE = "Mobile Number"
FIELD_PASSWORD = "Password"
SUBMIT_BUTTON = "Sign Up"

# OTP-verification modal. DoorDash texts a code after Sign Up; the modal may
# render a single input or split digit boxes. Tried in order; first visible
# wins. Extend with the exact selector captured on the first live run.
OTP_INPUT_SELECTORS = [
    "input[autocomplete='one-time-code']",
    "input[name*='code' i]",
    "input[aria-label*='code' i]",
    "input[placeholder*='code' i]",
    "input[inputmode='numeric']",
    "input[type='tel']",
]
# Split 6-box variant: many DoorDash flows use individual digit inputs.
OTP_DIGIT_SELECTOR = "input[maxlength='1']"
OTP_SUBMIT_SELECTORS = [
    "button:has-text('Verify')",
    "button:has-text('Continue')",
    "button:has-text('Submit')",
    "button[type='submit']",
]
OTP_RESEND_SELECTORS = [
    "button:has-text('Resend')",
    "text=/resend/i",
]
# Logged-in success markers in the post-signup URL.
SUCCESS_URL_MARKERS = ["/post-login", "doordash.com/home", "/orders"]

Emit = Callable[[str, dict[str, Any]], None]
# Async OTP poll: () -> code-or-"" ; supplied by the orchestrator (DaisyBridge).
OtpPoller = Callable[[], Awaitable[str]]


def _notify(emit: Emit | None, type: str, data: dict[str, Any]) -> None:
    if emit is not None:
        emit(type, data)


def normalize_phone(phone: str) -> str:
    """DoorDash's tel field has +1 preselected — type the 10 bare digits."""
    digits = re.sub(r"\D", "", phone or "")
    return digits[-10:] if len(digits) >= 10 else digits


async def _fill_textbox(page: Page, name: str, value: str) -> None:
    loc = page.get_by_role("textbox", name=name).first
    await loc.click()
    await loc.fill(value)


async def _find_otp_input(page: Page):
    for sel in OTP_INPUT_SELECTORS:
        loc = page.locator(sel).first
        try:
            if await loc.is_visible():
                return ("single", loc)
        except Exception:
            continue
    digits = page.locator(OTP_DIGIT_SELECTOR)
    try:
        if await digits.count() >= 4 and await digits.first.is_visible():
            return ("digits", digits)
    except Exception:
        pass
    return (None, None)


async def _enter_otp(page: Page, code: str) -> bool:
    kind, loc = await _find_otp_input(page)
    if kind is None or loc is None:
        return False
    try:
        if kind == "single":
            await loc.click()
            await loc.fill(code)
        else:  # split digit boxes — type one char per input
            n = await loc.count()
            for i, ch in enumerate(code[:n]):
                await loc.nth(i).fill(ch)
        # Submit (some flows auto-advance on the last digit).
        for sel in OTP_SUBMIT_SELECTORS:
            btn = page.locator(sel).first
            try:
                if await btn.is_visible():
                    await btn.click()
                    return True
            except Exception:
                continue
        await page.keyboard.press("Enter")
        return True
    except Exception:
        return False


async def fill_signup_form(page: Page, identity: dict[str, Any],
                           emit: Emit | None = None) -> None:
    await page.goto(SIGNUP_URL, wait_until="domcontentloaded")
    await handle_cloudflare(page)
    await asyncio.sleep(1.0)
    await _fill_textbox(page, FIELD_FIRST, identity["first_name"])
    await _fill_textbox(page, FIELD_LAST, identity["last_name"])
    await _fill_textbox(page, FIELD_EMAIL, identity["email"])
    await _fill_textbox(page, FIELD_MOBILE,
                        normalize_phone(identity.get("phone_number", "")))
    await _fill_textbox(page, FIELD_PASSWORD, identity["password"])
    _notify(emit, "log", {"message": "signup form filled"})


async def submit_and_verify(page: Page, poll_otp: OtpPoller, *,
                            emit: Emit | None = None,
                            otp_total_wait_s: float = 180,
                            resend_after_s: float = 75) -> str:
    """Click Sign Up, wait for the OTP modal, poll + enter the code.

    Returns 'created' | 'otp_timeout' | 'otp_failed' | 'blocked' | 'failed'.
    poll_otp() returns the current code ('' = not arrived yet).
    """
    await page.get_by_role("button", name=SUBMIT_BUTTON).first.click()
    await asyncio.sleep(2.5)
    await handle_cloudflare(page)  # signup submit can re-trigger the gate

    # Wait for the OTP input to appear.
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        kind, _ = await _find_otp_input(page)
        if kind is not None:
            break
        if any(m in page.url for m in SUCCESS_URL_MARKERS):
            return "created"  # some flows skip OTP entirely
        await asyncio.sleep(1.0)
    else:
        await screenshot(page, "signup_no_otp_modal")
        return "failed"

    _notify(emit, "otp_waiting", {})
    started = time.monotonic()
    resent = False
    while time.monotonic() - started < otp_total_wait_s:
        code = await poll_otp()
        if code:
            _notify(emit, "otp_received", {"code": code})
            if await _enter_otp(page, code):
                await asyncio.sleep(3.0)
                await handle_cloudflare(page)
                if any(m in page.url for m in SUCCESS_URL_MARKERS):
                    return "created"
                # Code entered but not obviously logged in — give it a moment.
                await asyncio.sleep(3.0)
                if any(m in page.url for m in SUCCESS_URL_MARKERS):
                    return "created"
                return "otp_failed"
            return "otp_failed"
        # Free resend if the first code is slow.
        if not resent and time.monotonic() - started > resend_after_s:
            for sel in OTP_RESEND_SELECTORS:
                try:
                    btn = page.locator(sel).first
                    if await btn.is_visible():
                        await btn.click()
                        resent = True
                        _notify(emit, "otp_resent", {})
                        break
                except Exception:
                    continue
        await asyncio.sleep(3.0)
    return "otp_timeout"
