"""Phone-number → OTP login via SeleniumBase CDP (beats the login CF Turnstile).

The consumer LOGIN page now fronts a Cloudflare Turnstile that plain Playwright
can't reliably clear (the form fields sit behind it). The signup path already
solves this with SeleniumBase CDP + the captcha ladder (uc_gui_click_captcha
clicks the real Turnstile checkbox). This reuses that machinery for LOGIN:

  open login -> clear CF (ladder, gui_captcha=True: login's gate IS a clickable
  Turnstile, unlike signup's server reject) -> switch to "Login with phone
  number" -> type the 10-digit number (os_input) -> Continue -> the account's
  rented api.cc number gets a 6-digit code -> enter it (os_input) -> /home.

SYNC (SeleniumBase). Returns:
    {"outcome": "logged_in"|"otp_timeout"|"no_phone_field"|"failed",
     "storage_state": {...}|None}

⚠️ os_input drives the REAL shared cursor — runs must be hands-off.
"""
from __future__ import annotations

import time
from typing import Any, Callable

from backend.browser.cdp_signup import (
    SUCCESS_URL_MARKERS,
    _cdp_source,
    _cdp_url,
    _enter_otp,
    _export_storage,
    _fill_home_address,
    _gui_click_in_view,
    _modal_gone_from_source,
    clear_captcha_ladder,
    focus_signup_window,
)
from backend.browser.delivery_prefs import pick_instruction

# The consumer login page (email-first; we switch it to phone).
LOGIN_URL = "https://www.doordash.com/consumer/login/"
HOME_URL = "https://www.doordash.com/home"

# Post-login delivery-pref controls (CDP, in the SAME CF-cleared session).
YOUR_ADDRESS_BTN = ('button:contains("Your Address")',
                    'button:contains("Address")')
HAND_IT_SELECTORS = ('label:contains("Hand it to me")',
                     'button:contains("Hand it to me")',
                     'div:contains("Hand it to me")')
SAVE_SELECTORS = ('button:contains("Save")', 'button:contains("Continue")',
                  'button:contains("Done")')

# Control that switches the login form to phone-number entry. DoorDash labels it
# a few ways; key on text. Also the tel field once we're there.
PHONE_TAB_SELECTORS = (
    'button:contains("Use phone")',
    'button:contains("phone number")',
    'button:contains("Sign in with phone")',
    'button:contains("Continue with phone")',
)
PHONE_INPUT_SELECTORS = ('input[type="tel"]', 'input[autocomplete="tel"]',
                         'input[name*="phone" i]', 'input[id*="phone" i]')
# The email/phone field on the email-first screen also accepts a phone number.
EMAIL_OR_PHONE_SELECTORS = ('input[type="email"]', 'input[type="tel"]',
                            'input[name="email"]',
                            'input[placeholder*="email" i]')
# The submit button. EXACT DoorDash text only — a broad contains("Continue")
# wrongly matches "Continue with Google" (the live derail). Never the social
# buttons (Google/Apple/Facebook).
CONTINUE_SELECTORS = ('button:contains("Continue to Sign In")',
                      'button:contains("Continue to Login")')


def _emit_factory(emit):
    def _e(t: str, d: dict | None = None) -> None:
        if emit:
            try:
                emit(t, d or {})
            except Exception:
                pass
    return _e


def _find(sb: Any, selectors: tuple[str, ...]):
    for sel in selectors:
        try:
            if sb.cdp.find_element(sel):
                return sel
        except Exception:
            continue
    return None


def _is_logged_in(sb: Any) -> bool:
    """True only on a DoorDash post-login page.

    Guards against two live false-positives: a social-OAuth page
    (accounts.google.com) whose URL can carry a success substring, and the login
    page itself (consumer/LOGIN matches the 'consumer/' success marker). So:
    require the doordash domain AND a success marker AND not a login/auth path.
    """
    url = _cdp_url(sb).lower()
    if "doordash.com" not in url:
        return False
    if any(s in url for s in ("/login", "/auth", "identity.")):
        return False
    return any(m in url for m in SUCCESS_URL_MARKERS)


def _set_prefs_via_cdp(sb: Any, full_address: str, instruction: str,
                       os_input: bool, emit) -> dict[str, Any]:
    """After login, in the SAME CF-cleared session: set the delivery address,
    choose 'Hand it to me', fill the dasher instruction. Best-effort."""
    _e = _emit_factory(emit)
    out = {"address_set": False, "hand_it_to_me": False,
           "instruction_set": False, "instruction": instruction}
    # After OTP we're ALREADY on /home (the address modal renders there). Do NOT
    # re-open /home — a fresh navigation re-triggers the Cloudflare Turnstile and
    # the prefs run against the gate (the live failure). Only navigate if we're
    # somehow off doordash entirely, and clear CF if a gate is showing.
    try:
        if "doordash.com" not in _cdp_url(sb):
            sb.cdp.open(HOME_URL)
            time.sleep(3.0)
        clear_captcha_ladder(sb, emit=emit, gui_captcha=True)
    except Exception:
        pass
    time.sleep(2.0)  # let the home modal settle
    if os_input:
        focus_signup_window(sb, emit=emit)

    # Open the address control if the home modal isn't already showing.
    if _find(sb, ("#HomeAddressAutocomplete",
                  'input[placeholder*="delivery address" i]')) is None:
        btn = _find(sb, YOUR_ADDRESS_BTN)
        if btn:
            try:
                sb.cdp.click(btn)
                time.sleep(2.0)
            except Exception:
                pass

    # Address (reuse the signup module's focus-independent CDP filler).
    if _fill_home_address(sb, full_address):
        out["address_set"] = True
        _e("delivery_address_set", {"address": full_address[:60]})
    time.sleep(1.5)

    # "Hand it to me".
    hand = _find(sb, HAND_IT_SELECTORS)
    if hand:
        try:
            sb.cdp.click(hand)
            out["hand_it_to_me"] = True
            _e("delivery_hand_it_to_me", {})
        except Exception:
            pass

    # Dasher instructions textarea.
    try:
        if sb.cdp.find_element("textarea"):
            sb.cdp.click("textarea")
            sb.cdp.press_keys("textarea", instruction)
            out["instruction_set"] = True
            _e("delivery_instruction_set", {"instruction": instruction[:60]})
    except Exception:
        pass

    save = _find(sb, SAVE_SELECTORS)
    if save:
        try:
            sb.cdp.click(save)
            time.sleep(1.5)
        except Exception:
            pass
    _e("delivery_prefs_done", out)
    return out


def phone_login_via_cdp(phone10: str, *,
                        poll_otp: Callable[[], str],
                        proxy: str | None = None,
                        headless: bool = False,
                        os_input: bool = True,
                        otp_total_wait_s: float = 240.0,
                        set_address: str | None = None,
                        instruction: str | None = None,
                        emit: Callable[[str, dict], None] | None = None,
                        screenshot_dir: str | None = None) -> dict[str, Any]:
    """Drive a phone-number→OTP login. SYNC; call via asyncio.to_thread.

    If set_address is given, ALSO sets the delivery address + "Hand it to me" +
    a dasher instruction (random if instruction is None) in the SAME session,
    avoiding a second context that would re-hit the login CF gate.
    """
    from seleniumbase import SB

    _e = _emit_factory(emit)
    shots = {"n": 0}

    def _shot(tag: str) -> None:
        if not screenshot_dir:
            return
        try:
            import os
            os.makedirs(screenshot_dir, exist_ok=True)
            shots["n"] += 1
            sb_ref[0].save_screenshot(
                os.path.join(screenshot_dir, f"login_{shots['n']:02d}_{tag}.png"))
        except Exception:
            pass

    sb_ref: list[Any] = [None]
    result: dict[str, Any] = {"outcome": "failed", "storage_state": None}
    note = instruction or (pick_instruction() if set_address else "")

    def _finalize(sb: Any) -> dict[str, Any]:
        """Mark logged-in, optionally set delivery prefs, export the session."""
        result["outcome"] = "logged_in"
        if set_address:
            try:
                result["prefs"] = _set_prefs_via_cdp(
                    sb, set_address, note, os_input, emit)
            except Exception:
                pass
        result["storage_state"] = _export_storage(sb)
        _shot("06_logged_in")
        return result

    kwargs: dict[str, Any] = dict(uc=True, headless=headless,
                                  window_size="1200,1000")
    if proxy:
        kwargs["proxy"] = proxy

    try:
        with SB(**kwargs) as sb:
            sb_ref[0] = sb
            sb.activate_cdp_mode(LOGIN_URL)
            time.sleep(3.0)
            _shot("01_open")

            # Already logged in?
            if _is_logged_in(sb):
                return _finalize(sb)

            # Clear the login CF Turnstile (clickable checkbox -> gui_captcha).
            clear_captcha_ladder(sb, emit=emit, gui_captcha=True)
            time.sleep(1.0)
            _shot("02_post_cf")

            if os_input:
                focus_signup_window(sb, emit=emit)

            # Prefer a dedicated phone tab + tel field; else fall back to the
            # email-first screen's email/phone field (it accepts a phone number).
            if _find(sb, PHONE_INPUT_SELECTORS) is None:
                tab = _find(sb, PHONE_TAB_SELECTORS)
                if tab:
                    if os_input:
                        _gui_click_in_view(sb, tab)
                    else:
                        sb.cdp.click(tab)
                    time.sleep(2.0)

            phone_sel = (_find(sb, PHONE_INPUT_SELECTORS)
                         or _find(sb, EMAIL_OR_PHONE_SELECTORS))
            if phone_sel is None:
                _shot("03_no_phone_field")
                result["outcome"] = "no_phone_field"
                return result

            # Enter the 10-digit number.
            try:
                if os_input:
                    sb.cdp.gui_click_element(phone_sel)
                    time.sleep(0.4)
                    sb.cdp.gui_write(phone10)
                else:
                    sb.cdp.click(phone_sel)
                    sb.cdp.press_keys(phone_sel, phone10)
            except Exception:
                pass
            time.sleep(1.0)
            _shot("04_phone_entered")

            # Continue to trigger the SMS code.
            cont = _find(sb, CONTINUE_SELECTORS)
            if cont:
                if os_input:
                    _gui_click_in_view(sb, cont)
                else:
                    sb.cdp.click(cont)
            time.sleep(3.0)
            _shot("05_submitted")

            if _is_logged_in(sb):
                return _finalize(sb)

            # Poll + enter OTP.
            started = time.time()
            tried: set[str] = set()
            while time.time() - started < otp_total_wait_s:
                code = poll_otp()
                if code and code not in tried:
                    tried.add(code)
                    _e("login_otp_received", {"code": code})
                    if os_input:
                        focus_signup_window(sb, emit=emit)
                    _enter_otp(sb, code, os_input)
                    post = time.time() + 60
                    while time.time() < post:
                        url = _cdp_url(sb)
                        if _is_logged_in(sb):
                            return _finalize(sb)
                        # modal-gone only counts if we LEFT the verify/auth step
                        # and stayed on doordash (not a blank/error page). The
                        # shared helper rejects an empty source so a transient
                        # CDP read can't falsely flag success.
                        modal_gone = _modal_gone_from_source(_cdp_source(sb))
                        left_auth = not any(
                            s in url.lower()
                            for s in ("verify", "/auth", "login"))
                        if modal_gone and left_auth and "doordash.com" in url:
                            return _finalize(sb)
                        time.sleep(2.0)
                time.sleep(3.0)
            result["outcome"] = "otp_timeout"
            _shot("06_otp_timeout")
            return result
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"[:200]
        if proxy and "@" in proxy:
            pwd = proxy.split("@", 1)[0].rsplit(":", 1)[-1]
            if pwd:
                err = err.replace(pwd, "<redacted>")
        _e("login_error", {"error": err})
        return result
