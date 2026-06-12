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
from backend.browser.pacing import key_delay_ms, pause_seconds

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

# OTP "Phone Number Verification" modal — VERIFIED LIVE 2026-06-12.
# A single numeric input inside a dialog, aria-label "Enter your 6-digit code".
# The cascade keeps fallbacks first in case DoorDash A/B-tests a split variant.
OTP_INPUT_SELECTORS = [
    "div[role='dialog'] input[aria-label*='digit' i]",  # exact live match
    "input[aria-label*='digit code' i]",
    "div[role='dialog'] input[type='number']",
    "input[autocomplete='one-time-code']",
    "input[aria-label*='code' i]",
    "input[inputmode='numeric']",
]
# Split 6-box variant. Two real-world shapes (verified live 2026-06-12):
#   • maxlength=1 boxes (classic), and
#   • input[type='number'] boxes whose aria-label is the box INDEX ("0".."5")
#     and that carry NO maxlength — the passwordless OTP-first login uses these,
#     so the maxlength selector alone MISSES them. Both are matched below.
OTP_DIGIT_SELECTOR = "input[maxlength='1']"
OTP_DIGIT_NUMBER_SELECTOR = "input[type='number'][aria-label]"
# Verify button label varies: "Submit" in the signup/2-step dialog, "Sign In"
# on the passwordless OTP-first login screen (verified live 2026-06-12).
OTP_SUBMIT_SELECTORS = [
    "div[role='dialog'] button:has-text('Submit')",
    "button:has-text('Submit')",
    "button:has-text('Verify')",
    "button:has-text('Sign In')",
    "button:has-text('Continue')",
]
OTP_RESEND_SELECTORS = [
    "button:has-text('Resend')",
    "text=/resend code/i",
]
# Post-OTP "Unlock $0 delivery" address modal — VERIFIED LIVE 2026-06-12.
# A combobox; type the address, then click the matching autocomplete row.
ADDRESS_INPUT_SELECTORS = [
    "div[role='dialog'] input[placeholder*='delivery address' i]",
    "input[placeholder*='delivery address' i]",
    "input[aria-label*='delivery address' i]",
]
# Post-address DashPass upsell ("Start 30-day free trial") — we always Skip
# (starting the trial would attempt a charge). Appears ~30s after the address;
# verified live 2026-06-12. Also covers a generic modal close as a fallback.
DASHPASS_SKIP_SELECTORS = [
    "button:has-text('Skip')",
    "text=/^Skip$/",
    "[aria-label='Close']",
    "button[aria-label*='close' i]",
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
    """Type a field like a human — per-key delay, not an instant fill().

    DoorDash's signup form flags instant `fill()` value-setting as a bot and
    returns "Something went wrong, please refresh and retry." Typing each
    character with a jittered delay (and a small settle after focusing the
    field) reads like a real person. `clear()` first so a re-attempt doesn't
    append to a partially-filled field.
    """
    loc = page.get_by_role("textbox", name=name).first
    await loc.click()
    await asyncio.sleep(pause_seconds(0.2, 0.5))
    try:
        await loc.clear()
    except Exception:
        pass  # empty field / clear unsupported — typing still overwrites
    await loc.type(value, delay=key_delay_ms())


def looks_like_split_otp(aria_labels: list[str], min_boxes: int = 4) -> bool:
    """Pure: do these input[type=number] aria-labels look like split OTP boxes?

    The passwordless 6-box login labels each box with its INDEX ("0".."5").
    We treat >= min_boxes numeric/short index-like labels as the split variant.
    Empty labels (a single numeric code field has aria-label "...digit code")
    are excluded by requiring the label be a bare small integer.
    """
    indices = [lbl for lbl in aria_labels
               if lbl.strip().isdigit() and len(lbl.strip()) <= 2]
    return len(indices) >= min_boxes


async def _find_otp_input(page: Page):
    # Check split digit-boxes FIRST: the passwordless OTP-first screen uses 6
    # separate inputs, and a broad single-input selector below would otherwise
    # match one box and stuff all 6 digits into it. Two split shapes exist:
    #   (1) maxlength=1 boxes, and
    #   (2) input[type=number] boxes whose aria-label is the box index.
    digits = page.locator(OTP_DIGIT_SELECTOR)
    try:
        if await digits.count() >= 4 and await digits.first.is_visible():
            return ("digits", digits)
    except Exception:
        pass
    num_boxes = page.locator(OTP_DIGIT_NUMBER_SELECTOR)
    try:
        count = await num_boxes.count()
        if count >= 4:
            labels = []
            for i in range(count):
                try:
                    labels.append(
                        (await num_boxes.nth(i).get_attribute("aria-label"))
                        or "")
                except Exception:
                    labels.append("")
            if (looks_like_split_otp(labels)
                    and await num_boxes.first.is_visible()):
                return ("digits", num_boxes)
    except Exception:
        pass
    for sel in OTP_INPUT_SELECTORS:
        loc = page.locator(sel).first
        try:
            if await loc.is_visible():
                return ("single", loc)
        except Exception:
            continue
    return (None, None)


async def _enter_otp(page: Page, code: str) -> bool:
    kind, loc = await _find_otp_input(page)
    if kind is None or loc is None:
        return False
    try:
        if kind == "single":
            await loc.click()
            await loc.fill(code)
        else:  # split digit boxes
            # Live-proven sequence (2026-06-12): click the FIRST box, press the
            # first digit, then keyboard.type the rest — the widget auto-
            # advances focus per keystroke and auto-submits on the last box.
            # (Per-box .press also works but relies on each box being clickable;
            # type-from-box0 matches what the site's keypress handlers expect.)
            n = await loc.count()
            digits = code[:n]
            if not digits:
                return False
            await loc.first.click()
            await page.keyboard.press(digits[0])
            if len(digits) > 1:
                # Type with a small per-key delay so React/Redux-backed boxes
                # register each digit + auto-advance focus (a 0ms burst can
                # drop digits on these controlled inputs).
                await page.keyboard.type(digits[1:], delay=80)
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
                            address: dict[str, Any] | None = None,
                            emit: Emit | None = None,
                            otp_total_wait_s: float = 180,
                            resend_after_s: float = 75) -> str:
    """Click Sign Up, wait for the OTP modal, poll + enter the code.

    Returns 'created' | 'otp_timeout' | 'otp_failed' | 'blocked' | 'failed'.
    poll_otp() returns the current code ('' = not arrived yet). On success the
    post-signup delivery-address modal is filled from `address` (best effort).
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
    tried_codes: set[str] = set()
    last_resend = 0.0
    # Live-observed failure mode: a code can EXPIRE between arrival and submit,
    # leaving the modal in place (no logged-in redirect). The fix proven live
    # is to resend (free) and submit the next code fast. So: on a submitted
    # code that doesn't reach success, resend and wait for a fresh code.
    while time.monotonic() - started < otp_total_wait_s:
        code = await poll_otp()
        if code and code not in tried_codes:
            tried_codes.add(code)
            _notify(emit, "otp_received", {"code": code})
            if await _enter_otp(page, code):
                # Up to 10s — slow networks/proxies can take a while; a
                # premature resend would invalidate this good code.
                for _ in range(5):
                    await asyncio.sleep(2.0)
                    await handle_cloudflare(page)
                    if any(m in page.url for m in SUCCESS_URL_MARKERS):
                        await _fill_address_if_present(page, address, emit=emit)
                        return "created"
                # Submitted but not logged in (expired/rejected) → resend.
                if await _resend(page, emit):
                    last_resend = time.monotonic()
                    tried_codes.clear()  # a fresh code may reuse the digits
                continue
            return "otp_failed"
        # No fresh code yet; resend if the wait is dragging.
        if (time.monotonic() - started > resend_after_s
                and time.monotonic() - last_resend > resend_after_s):
            if await _resend(page, emit):
                last_resend = time.monotonic()
                tried_codes.clear()  # a fresh code may reuse the digits
        await asyncio.sleep(3.0)
    return "otp_timeout"


async def _resend(page: Page, emit: Emit | None) -> bool:
    for sel in OTP_RESEND_SELECTORS:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible():
                await btn.click()
                _notify(emit, "otp_resent", {})
                return True
        except Exception:
            continue
    return False


async def _fill_address_if_present(page: Page, address: dict[str, Any] | None,
                                   emit: Emit | None = None) -> None:
    """Post-OTP 'Unlock $0 delivery' address modal (verified live 2026-06-12).

    Type the full address, then click the matching autocomplete row. Best
    effort: the account is already created, so a missed address never fails
    the flow (it can be set on first use). Skipped when no address is given.
    """
    full = (address or {}).get("full_address")
    if not full:
        return
    inp = None
    # The "Unlock $0 delivery" modal can take up to ~30s to appear after OTP
    # (verified live 2026-06-12) — wait generously, the account is already in.
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        for sel in ADDRESS_INPUT_SELECTORS:
            loc = page.locator(sel).first
            try:
                if await loc.is_visible():
                    inp = loc
                    break
            except Exception:
                continue
        if inp:
            break
        await asyncio.sleep(1.0)
    if inp is None:
        return  # no modal this run — fine
    try:
        await inp.click()
        await inp.fill(full)
        await asyncio.sleep(2.5)  # let autocomplete populate
        # Pressing Enter accepts the top suggestion and works ~9/10 times when
        # the address exists (user-confirmed) — simpler and more reliable than
        # clicking a row by text. Fall back to clicking the matching row.
        await page.keyboard.press("Enter")
        await asyncio.sleep(1.5)
        for sel in ADDRESS_INPUT_SELECTORS:
            try:
                if await page.locator(sel).first.is_visible():
                    # Still on the modal — Enter didn't take; click a row.
                    street_no = re.match(r"\d+", full)
                    option = page.locator(
                        "div[role='dialog'] [role='option'], "
                        # NOT a bare 'a' — that matches Terms/Privacy/Close and
                        # could navigate away. List items / option rows only.
                        "div[role='dialog'] li, "
                        "div[role='dialog'] [role='button'][data-anchor-id]")
                    n = await option.count()
                    for i in range(min(n, 8)):
                        row = option.nth(i)
                        try:
                            txt = (await row.inner_text()).strip()
                        except Exception:
                            continue
                        if street_no and txt.startswith(street_no.group()):
                            await row.click()
                            break
                    else:
                        if n:
                            await option.first.click()
                    break
            except Exception:
                continue
        _notify(emit, "address_set", {"address": full})
    except Exception:
        pass  # account exists regardless
    await _dismiss_dashpass(page, emit)


async def _dismiss_dashpass(page: Page, emit: Emit | None = None,
                            wait_s: float = 35) -> None:
    """Skip the post-address DashPass trial upsell if it appears.

    We never start the trial (it would attempt a charge) — click Skip/close.
    The modal can take ~30s to render, so poll up to wait_s, but return the
    instant we find and click it. Silent no-op when it never appears.
    """
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        for sel in DASHPASS_SKIP_SELECTORS:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible():
                    await btn.click()
                    _notify(emit, "log",
                            {"message": "skipped DashPass upsell"})
                    return
            except Exception:
                continue
        await asyncio.sleep(2.0)
