"""DoorDash signup via SeleniumBase UC mode (undetected Chromium).

DoorDash's signup endpoint fingerprints normal Playwright/automation and returns
HTTP 403 ``{"statusCode":"user_assessment_bot"}`` (verified live 2026-06-12).
SeleniumBase's UC mode passes that bot assessment where Playwright cannot, so
the *signup* (account creation) path uses this driver instead of Playwright. The
rest of the app (login, scraping, refunds) stays on Playwright — only fresh
account creation needs the undetected browser.

This module is SYNCHRONOUS (SeleniumBase/Selenium are sync). The async layer
calls ``signup_via_uc`` through ``asyncio.to_thread``. It fills the form, submits
past the bot check, enters the api.cc OTP, and on success writes a Playwright-
compatible storage_state JSON (cookies) so the new account drops straight into
the existing per-customer profile/session machinery.

Field selectors (live 2026-06-12) key on the stable ``autocomplete`` attribute —
the name inputs have no id/name/aria-label, only autocomplete.
"""
from __future__ import annotations

import re
import time
from typing import Any, Callable

# Live signup URL (same as the Playwright path).
SIGNUP_URL = (
    "https://identity.doordash.com/auth/user/signup"
    "?client_id=1666519390426295040&intl=en-US&layout=consumer_web"
    "&prompt=none&redirect_uri=https%3A%2F%2Fwww.doordash.com%2Fpost-login%2F"
    "&response_type=code&scope=%2A&state=%2Fhome"
)

# autocomplete-based field selectors (verified live: name inputs expose only
# autocomplete, not id/name/aria-label).
SEL_FIRST = 'input[autocomplete="given-name"]'
SEL_LAST = 'input[autocomplete="family-name"]'
SEL_EMAIL = 'input[autocomplete="email"]'
SEL_PHONE = 'input[autocomplete="tel"]'
SEL_PASSWORD = 'input[autocomplete="new-password"]'
SEL_SUBMIT = 'button:contains("Sign Up")'

BOT_BLOCK_MARKERS = ("user_assessment_bot", "something went wrong")
VERIFY_MARKERS = ("verification", "enter the", "we sent", "6 digit",
                  "6-digit", "verify your")
SUCCESS_URL_MARKERS = ("/post-login", "/home", "consumer/")

# OTP boxes on the verify step. The 6-box split input uses these.
OTP_DIGIT_BOXES = 'input[inputmode="numeric"], input[maxlength="1"], ' \
                  'input[type="number"]'


def normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    return digits[-10:] if len(digits) >= 10 else digits


def _page_has(driver: Any, markers: tuple[str, ...]) -> bool:
    try:
        src = driver.get_page_source().lower()
    except Exception:
        return False
    return any(m in src for m in markers)


def _fill_humanlike(driver: Any, selector: str, value: str) -> bool:
    """Type a field char-by-char (SeleniumBase types with per-key delay)."""
    try:
        driver.wait_for_element_visible(selector, timeout=8)
        driver.click(selector)
        time.sleep(0.4)
        driver.type(selector, value)
        time.sleep(0.4)
        return True
    except Exception:
        return False


def _enter_otp(driver: Any, code: str) -> bool:
    """Enter the OTP into the verify step (single field or 6 split boxes)."""
    digits = re.sub(r"\D", "", code)
    if not digits:
        return False
    try:
        boxes = driver.find_elements(OTP_DIGIT_BOXES)
    except Exception:
        boxes = []
    try:
        if len(boxes) >= 4:
            # 6-box split input: click the first box and send all digits to it.
            # These widgets auto-advance focus per keystroke and fan the digits
            # across the boxes (proven behaviour). If a box rejects the burst,
            # the per-box fallback below distributes them one at a time.
            boxes[0].click()
            time.sleep(0.2)
            boxes[0].send_keys(digits)
            time.sleep(0.5)
            # Verify it took; else distribute per box.
            filled = "".join(
                (b.get_attribute("value") or "") for b in boxes)
            if len(re.sub(r"\D", "", filled)) < min(len(digits), len(boxes)):
                for box, ch in zip(boxes, digits):
                    box.click()
                    box.send_keys(ch)
                    time.sleep(0.1)
        else:
            single = boxes[0] if boxes else driver.find_element("input")
            single.click()
            single.send_keys(digits)
        time.sleep(1.0)
        return True
    except Exception:
        return False


def signup_via_uc(identity: dict[str, Any], *,
                  poll_otp: Callable[[], str],
                  headless: bool = False,
                  emit: Callable[[str, dict], None] | None = None,
                  otp_total_wait_s: float = 180.0,
                  reconnect_time: float = 4.0,
                  ) -> dict[str, Any]:
    """Create one DoorDash account via UC mode. SYNC — run in a thread.

    Returns {"outcome": "created"|"bot_blocked"|"otp_timeout"|"failed",
             "storage_state": {...}|None, "phone10": "..."}.
    ``poll_otp()`` returns the current api.cc code ('' if not arrived yet).
    On "created" the returned storage_state is Playwright-compatible (cookies).
    """
    from seleniumbase import Driver

    def _emit(t: str, d: dict | None = None) -> None:
        if emit:
            try:
                emit(t, d or {})
            except Exception:
                pass

    phone10 = normalize_phone(identity.get("phone_number", ""))
    result = {"outcome": "failed", "storage_state": None, "phone10": phone10}

    driver = Driver(uc=True, headless=headless)
    try:
        driver.uc_open_with_reconnect(SIGNUP_URL, reconnect_time=reconnect_time)
        time.sleep(2.5)
        _emit("signup_form_open", {})

        _fill_humanlike(driver, SEL_FIRST, identity.get("first_name", ""))
        _fill_humanlike(driver, SEL_LAST, identity.get("last_name", ""))
        _fill_humanlike(driver, SEL_EMAIL, identity.get("email", ""))
        _fill_humanlike(driver, SEL_PHONE, phone10)
        _fill_humanlike(driver, SEL_PASSWORD, identity.get("password", ""))
        _emit("signup_form_filled", {})

        try:
            driver.click(SEL_SUBMIT)
        except Exception:
            driver.click('button[type="submit"]')
        _emit("signup_submitting", {})
        time.sleep(5)

        if _page_has(driver, BOT_BLOCK_MARKERS):
            result["outcome"] = "bot_blocked"
            _emit("signup_bot_blocked", {})
            return result

        # Wait for the verify/OTP step.
        deadline = time.time() + 40
        while time.time() < deadline:
            if _page_has(driver, VERIFY_MARKERS):
                break
            if any(m in driver.current_url for m in SUCCESS_URL_MARKERS):
                result["outcome"] = "created"
                result["storage_state"] = _export_storage(driver)
                return result
            time.sleep(1.5)
        else:
            result["outcome"] = "failed"
            _emit("signup_no_verify", {})
            return result

        _emit("otp_waiting", {})
        started = time.time()
        tried: set[str] = set()
        while time.time() - started < otp_total_wait_s:
            code = poll_otp()
            if code and code not in tried:
                tried.add(code)
                _emit("otp_received", {"code": code})
                if _enter_otp(driver, code):
                    for _ in range(6):
                        time.sleep(2.0)
                        if any(m in driver.current_url
                               for m in SUCCESS_URL_MARKERS):
                            result["outcome"] = "created"
                            result["storage_state"] = _export_storage(driver)
                            _emit("signup_created", {})
                            return result
                    # entered but not logged in — wait for a fresh code
            time.sleep(3.0)
        result["outcome"] = "otp_timeout"
        return result
    finally:
        # Caller decides whether to keep the window; default close on return.
        if not result.get("_keep_open"):
            try:
                driver.quit()
            except Exception:
                pass


def _export_storage(driver: Any) -> dict[str, Any]:
    """Export cookies in Playwright storage_state shape (cookies only).

    Playwright's add_cookies accepts name/value/domain/path/expires/httpOnly/
    secure/sameSite. Selenium cookies map cleanly; sameSite is normalized.
    """
    cookies = []
    try:
        for c in driver.get_cookies():
            ck = {
                "name": c.get("name", ""),
                "value": c.get("value", ""),
                "domain": c.get("domain", ""),
                "path": c.get("path", "/"),
                "httpOnly": bool(c.get("httpOnly", False)),
                "secure": bool(c.get("secure", False)),
            }
            if c.get("expiry") is not None:
                ck["expires"] = int(c["expiry"])
            ss = (c.get("sameSite") or "Lax")
            ck["sameSite"] = ss if ss in ("Strict", "Lax", "None") else "Lax"
            cookies.append(ck)
    except Exception:
        pass
    return {"cookies": cookies, "origins": []}
