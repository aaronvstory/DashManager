"""DoorDash signup via SeleniumBase **CDP Mode** + residential proxy.

This is the second, stronger attempt at automated account creation. The earlier
``uc_signup.py`` used the DEPRECATED UC path (``Driver(uc=True)`` +
``uc_open_with_reconnect``) from a non-residential IP — both of which
SeleniumBase's author (Mintz, GH #3925) calls out as no-longer-stealthy. This
module fixes BOTH root causes at once:

  1. **CDP Mode** — ``sb.activate_cdp_mode(url)`` (disconnects WebDriver, the
     stealthy modern path) + the captcha-clearing ladder
     (``sb.cdp.solve_captcha`` → ``sb.uc_gui_click_captcha`` PyAutoGUI fallback).
     Phase-0 smoke (2026-06-15) proved this ladder clears a live Cloudflare
     Turnstile gate on gitlab.com where solve_captcha alone did NOT — the
     ``uc_gui_click_captcha`` rung was the one that broke through.
  2. **Residential egress** — launched with ``proxy=user:pass@host:port`` (the
     LightningProxies HTTP gateway), so the browser exits from a US residential
     IP. Browser-scoped only — the PC's own IP is untouched.

SYNCHRONOUS (SeleniumBase/Selenium are sync); the async layer calls
``signup_via_cdp`` through ``asyncio.to_thread``. Return shape MATCHES
``uc_signup.signup_via_uc`` so ``account_creator`` can swap drivers without
touching the orchestration:

    {"outcome": "created"|"bot_blocked"|"otp_timeout"|"failed",
     "storage_state": {...}|None, "phone10": "..."}

Selectors + OTP logic are reused from the known-good ``uc_signup`` module.
"""
from __future__ import annotations

import re
import time
from typing import Any, Callable

from backend.browser.uc_signup import (BOT_BLOCK_MARKERS, SIGNUP_URL,
                                       SUCCESS_URL_MARKERS, VERIFY_MARKERS,
                                       normalize_phone)

# Old working scripts reached signup via the LOGIN page, then clicked "Sign Up"
# — never a cold /signup land (which scores as a low-trust signal).
LOGIN_URL = "https://www.doordash.com/consumer/login/"
# Ground-truth confirmation: the account's own profile page must show the
# identity we signed up with. This is THE success check (not just a URL redirect).
EDIT_PROFILE_URL = "https://www.doordash.com/consumer/edit_profile"

# CDP field selectors — same stable autocomplete attributes as uc_signup, but
# CDP's press_keys/select use CSS, so we keep the CSS forms here.
SEL_FIRST = 'input[autocomplete="given-name"]'
SEL_LAST = 'input[autocomplete="family-name"]'
SEL_EMAIL = 'input[autocomplete="email"]'
SEL_PHONE = 'input[autocomplete="tel"]'
SEL_PASSWORD = 'input[autocomplete="new-password"]'
# CDP's :contains() is supported; keep a type=submit fallback.
SEL_SUBMIT = 'button:contains("Sign Up")'
SEL_SUBMIT_FALLBACK = 'button[type="submit"]'

# OTP boxes on the verify step (same cascade idea as uc_signup).
OTP_DIGIT_BOXES = ('input[inputmode="numeric"], input[maxlength="1"], '
                   'input[type="number"]')

# CF challenge markers — if these persist after the ladder, the gate held.
CF_MARKERS = ("just a moment", "verify you are human",
              "performing security verification",
              "checking if the site connection is secure")

# IP-REPUTATION block (NOT the fingerprint gate). If we see THIS, it's actually
# GOOD news: we got PAST PerimeterX's behavioral/fingerprint check and only hit
# an IP-rep block — the fix is to rotate the home IP (the user runs Mullvad on
# their home network), then retry the SAME driver. Distinct outcome "ip_blocked"
# so the caller tells the user to swap IP rather than re-engineering stealth.
# The ENEMY is BOT_BLOCK_MARKERS ("something went wrong" = user_assessment_bot
# fingerprint reject); ip_blocked is a milestone past it.
IP_BLOCK_MARKERS = ("ip address has been blocked", "ip has been blocked",
                    "your ip", "access from your ip",
                    "blocked your ip", "unusual traffic from your")

# iOS Safari UA — recipe B from the old working scripts (most-copied variant).
IOS_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) "
          "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 "
          "Mobile/15E148 Safari/604.1")
# iPhone 14-class metrics "CSSWidth,CSSHeight,PixelRatio" for --mobile emulation.
IOS_METRICS = "390,844,3"


def resolve_proxy() -> str | None:
    """Best-effort residential proxy as ``user:pass@host:port`` (or None).

    Prefers the PR-A ``proxy_pool`` module (liveness-checked) when present; if
    that branch isn't merged yet, falls back to a direct parse of the gitignored
    ``working-proxies.txt`` so signup still gets a residential exit IP. Returns
    None only when no proxy line is configured (signup then runs direct — a
    diagnostics-only path that WILL hit the bot gate).
    """
    # Prefer proxy_pool (liveness-aware) if it's on this branch.
    try:
        from backend.browser import proxy_pool as pp  # type: ignore
        px = pp.dedup_proxies(pp.load_proxies())
        if px:
            return pp.format_sb_proxy(px[0])
    except Exception:
        pass
    # Fallback: parse working-proxies.txt directly.
    try:
        from pathlib import Path
        root = Path(__file__).resolve().parents[2]
        f = root / "working-proxies.txt"
        if f.exists():
            for ln in f.read_text(encoding="utf-8").splitlines():
                ln = ln.strip()
                if ln and not ln.startswith("#"):
                    raw = ln.split("://", 1)[-1]
                    parts = raw.split(":", 3)
                    if len(parts) == 4:
                        host, port, user, pwd = parts
                        return f"{user}:{pwd}@{host}:{port}"
    except Exception:
        pass
    return None


def _cdp_source(sb: Any) -> str:
    """Lower-cased page source via CDP (falls back to the sync getter)."""
    try:
        return (sb.cdp.get_page_source() or "").lower()
    except Exception:
        try:
            return (sb.get_page_source() or "").lower()
        except Exception:
            return ""


def _cdp_url(sb: Any) -> str:
    try:
        return sb.cdp.get_current_url() or ""
    except Exception:
        try:
            return sb.get_current_url() or ""
        except Exception:
            return ""


def _page_has(sb: Any, markers: tuple[str, ...]) -> bool:
    src = _cdp_source(sb)
    return any(m in src for m in markers)


def clear_captcha_ladder(sb: Any, *, emit: Callable[[str, dict], None] | None,
                         settle_s: float = 8.0,
                         gui_captcha: bool = True) -> bool:
    """Run the mouse-free-first captcha ladder. Returns True if CF cleared.

    Rungs (verified Phase-0 2026-06-15): wait for auto-bypass →
    ``cdp.solve_captcha`` → ``uc_gui_click_captcha`` (PyAutoGUI — grabs the real
    mouse, so this needs the window foreground). Generous post-click waits
    because CF's verification + redirect is slow.

    ``gui_captcha=False`` skips the PyAutoGUI rung — DoorDash's signup gate is a
    server-side ``user_assessment_bot`` reject, NOT a CF Turnstile checkbox (no
    checkbox renders), so the GUI rung only blocks the real mouse for nothing.
    """
    def _emit(t: str, d: dict | None = None) -> None:
        if emit:
            try:
                emit(t, d or {})
            except Exception:
                pass

    if not _page_has(sb, CF_MARKERS):
        return True  # nothing to clear

    # Rung 1 — mouse-free auto solver.
    try:
        sb.cdp.solve_captcha()
        _emit("signup_captcha_solve", {"rung": "cdp.solve_captcha"})
    except Exception:
        pass
    time.sleep(settle_s)
    if not _page_has(sb, CF_MARKERS):
        return True

    # Rung 2 — PyAutoGUI clicks the real CF checkbox (the rung that worked in
    # Phase 0). Needs the browser window in the foreground. Skipped when
    # gui_captcha=False (DoorDash signup has no Turnstile checkbox to click).
    if gui_captcha:
        try:
            sb.uc_gui_click_captcha()
            _emit("signup_captcha_solve", {"rung": "uc_gui_click_captcha"})
        except Exception:
            pass
        time.sleep(settle_s)

    # Final settle — CF can take several extra seconds to redirect.
    for _ in range(6):
        if not _page_has(sb, CF_MARKERS):
            return True
        time.sleep(3.0)
    return not _page_has(sb, CF_MARKERS)


def _field_value(sb: Any, selector: str) -> str:
    """Current .value of the first matching input (or '' if none/error)."""
    try:
        el = sb.cdp.find_element(selector)
        return (el.get_attribute("value") or "") if el else ""
    except Exception:
        return ""


def _press(sb: Any, selector: str, value: str) -> bool:
    """Type a field at human speed via CDP press_keys (per-key delay).

    Returns True only if the value actually LANDED in the field — the mobile
    signup form silently no-ops a press if the field isn't ready/visible yet, so
    a return value that isn't checked = an empty required field (the live bug
    that left First/Last name blank). Retries once after a short settle.
    """
    for attempt in range(2):
        try:
            sb.cdp.click(selector)
            time.sleep(0.4)
            sb.cdp.press_keys(selector, value)
            time.sleep(0.4)
            if _field_value(sb, selector).strip():
                return True
        except Exception:
            pass
        time.sleep(0.6)  # let a late-rendering field settle, then retry
    return _field_value(sb, selector).strip() != ""


# Multi-selector cascades — the mobile signup form doesn't always carry the same
# autocomplete attrs as desktop, so try a few stable anchors per field and use
# the first that exists. Keyed on autocomplete → name → id → placeholder, never
# hashed styled-component classes.
NAME_FIRST_SELECTORS = (
    'input[autocomplete="given-name"]', 'input[name="firstName"]',
    'input[name="first_name"]', 'input[id*="irst" i]',
    'input[placeholder*="First" i]', 'input[aria-label*="First" i]')
NAME_LAST_SELECTORS = (
    'input[autocomplete="family-name"]', 'input[name="lastName"]',
    'input[name="last_name"]', 'input[id*="ast" i]',
    'input[placeholder*="Last" i]', 'input[aria-label*="Last" i]')


def _press_any(sb: Any, selectors: tuple[str, ...], value: str) -> bool:
    """Fill via the first selector that EXISTS *and* accepts the value.

    Tries the next selector when a press doesn't land (a selector existing but
    no-op'ing on press_keys is exactly how the first-name field failed), so we
    don't give up after committing to one anchor.
    """
    for sel in selectors:
        try:
            if not sb.cdp.find_element(sel):
                continue
        except Exception:
            continue
        if _press(sb, sel, value):
            return True
    return False


def _gui_press(sb: Any, selector: str, value: str) -> bool:
    """Fill a field with REAL OS-level input (PyAutoGUI via SeleniumBase).

    PerimeterX weights genuine hardware mouse/keyboard telemetry; CDP
    press_keys is synthetic and gets the "something went wrong" reject. This
    moves the real cursor to the field's screen center, clicks (real button
    event), then types with real keystrokes (gui_write). Verifies the value
    landed in the DOM.
    """
    try:
        # screen-center click via real OS mouse, then real keystrokes
        sb.cdp.gui_click_element(selector)
        time.sleep(0.35)
    except Exception:
        # fall back to coords if the element-center helper isn't available
        try:
            x, y = sb.get_gui_element_center(selector)
            sb.cdp.gui_click_x_y(x, y)
            time.sleep(0.35)
        except Exception:
            return False
    try:
        sb.cdp.gui_write(value)
        time.sleep(0.35)
    except Exception:
        return False
    return _field_value(sb, selector).strip() != ""


def _gui_press_any(sb: Any, selectors: tuple[str, ...], value: str) -> bool:
    """_press_any but using real OS-level input (_gui_press)."""
    for sel in selectors:
        try:
            if not sb.cdp.find_element(sel):
                continue
        except Exception:
            continue
        if _gui_press(sb, sel, value):
            return True
    return False


def _enter_otp(sb: Any, code: str, os_input: bool = False) -> bool:
    """Enter the OTP into the verify modal AND click its Submit button.

    The verify modal is a SEPARATE step that can take ~20s to render — the
    caller must wait for it before calling this. After entering the digits we
    must click the modal's "Submit" (entering the code alone does NOT advance —
    the live run stalled here). Uses OS-level input when os_input so the OTP +
    submit carry the same human telemetry that passed the gate.
    """
    digits = re.sub(r"\D", "", code)
    if not digits:
        return False

    def _otp_landed() -> bool:
        # the combined value across boxes should contain all the digits
        try:
            boxes = sb.cdp.find_elements(OTP_DIGIT_BOXES) or []
            joined = "".join((b.get_attribute("value") or "") for b in boxes)
            return re.sub(r"\D", "", joined) == digits
        except Exception:
            return False

    # Up to 3 entry passes — focus can be stolen (the live OTP once typed into
    # another window). Re-click the box each pass and VERIFY the digits landed
    # before clicking Submit. Prefer DOM send_keys (focus-immune) over gui_write.
    for attempt in range(3):
        try:
            boxes = sb.cdp.find_elements(OTP_DIGIT_BOXES) or []
        except Exception:
            boxes = []
        try:
            if boxes:
                boxes[0].click()
                time.sleep(0.25)
                # clear any partial/wrong text first
                try:
                    boxes[0].clear()
                except Exception:
                    pass
            if os_input and boxes:
                # focus the box via real click, then real keystrokes
                try:
                    sb.cdp.gui_click_element(OTP_DIGIT_BOXES)
                    time.sleep(0.2)
                except Exception:
                    pass
                try:
                    sb.cdp.gui_write(digits)
                except Exception:
                    boxes[0].send_keys(digits)
            elif boxes:
                boxes[0].send_keys(digits)
            else:
                try:
                    sb.cdp.press_keys(OTP_DIGIT_BOXES, digits)
                except Exception:
                    return False
            time.sleep(1.0)
            if _otp_landed():
                _click_submit_button(sb, os_input)
                time.sleep(1.5)
                return True
            # didn't land (focus steal / no-op) — retry
            time.sleep(0.6)
        except Exception:
            time.sleep(0.6)
            continue
    # last-ditch: try submit anyway in case the value is there but unverifiable
    _click_submit_button(sb, os_input)
    time.sleep(1.5)
    return _otp_landed()


# OTP-modal / address-step controls.
OTP_SUBMIT_SELECTORS = ('button:contains("Submit")', 'button[type="submit"]')
ADDR_SELECTORS = ('input[placeholder*="delivery address" i]',
                  'input[aria-label*="address" i]',
                  'input[id*="ddress" i]', 'input[autocomplete="off"]')
SKIP_SELECTORS = ('button:contains("Skip")', 'button:contains("Not now")',
                  'button:contains("Maybe later")', 'a:contains("Skip")')


def _finish_account(sb: Any, identity: dict[str, Any], os_input: bool,
                    emit: Any, shot: Any) -> dict[str, Any]:
    """Post-verification: the account exists but lands on the home page with an
    'Enter delivery address' prompt + a possible DashPass upsell. Fill the
    address (press Enter to pick the first suggestion) then skip the upsell, so
    the account is fully set up. Best-effort — the account is already created;
    these steps just finish onboarding. The page can load slowly (~20s), so we
    wait for the address field before filling."""
    try:
        full_address = (identity.get("full_address")
                        or identity.get("address") or "")
        # wait for the address field to render (slow ~20s post-redirect)
        deadline = time.time() + 30
        have_addr = False
        while time.time() < deadline:
            for sel in ADDR_SELECTORS:
                try:
                    if sb.cdp.find_element(sel):
                        have_addr = True
                        break
                except Exception:
                    continue
            if have_addr:
                break
            time.sleep(1.5)
        if have_addr and full_address:
            if _fill_address(sb, full_address, os_input):
                emit("signup_address_filled", {"address": full_address[:60]})
                shot("07_address")
        # a DashPass upsell may appear before or after the address step
        time.sleep(1.5)
        _skip_upsell(sb, os_input)
        shot("08_finished")
        emit("signup_onboarding_done", {})
        # GROUND-TRUTH confirmation: profile page must show our identity.
        confirm = _confirm_edit_profile(sb, identity)
        shot("09_edit_profile")
        emit("signup_profile_confirmed", confirm)
        return confirm
    except Exception as exc:
        emit("signup_onboarding_warn",
             {"error": f"{type(exc).__name__}: {exc}"[:120]})
    return {"confirmed": False, "matched": [], "url": ""}


def _confirm_edit_profile(sb: Any, identity: dict[str, Any]) -> dict[str, Any]:
    """Navigate to /consumer/edit_profile and confirm OUR identity shows there.

    This is the ground-truth success check: a created account's profile page
    renders the email/first/last we signed up with. Returns
    {"confirmed": bool, "matched": [...], "url": ...}. Best-effort; never raises.
    """
    out: dict[str, Any] = {"confirmed": False, "matched": [], "url": ""}
    try:
        try:
            sb.cdp.open(EDIT_PROFILE_URL)
        except Exception:
            sb.open(EDIT_PROFILE_URL)   # sb.cdp has no .get(); sb.open does
        time.sleep(4.0)
        out["url"] = _cdp_url(sb)
        # the profile fields are inputs whose .value holds our data; also scan
        # the body text as a fallback.
        email = (identity.get("email") or "").lower()
        first = (identity.get("first_name") or "").lower()
        last = (identity.get("last_name") or "").lower()
        haystack = ""
        try:
            haystack += (_body_text_cdp(sb) or "").lower()
        except Exception:
            pass
        for sel in ('input[autocomplete="email"]', 'input[type="email"]',
                    'input[autocomplete="given-name"]',
                    'input[autocomplete="family-name"]', "input"):
            try:
                for el in (sb.cdp.find_elements(sel) or []):
                    try:
                        haystack += " " + (el.get_attribute("value") or "").lower()
                    except Exception:
                        continue
            except Exception:
                continue
        matched = [k for k, v in (("email", email), ("first", first),
                                  ("last", last)) if v and v in haystack]
        out["matched"] = matched
        # email is the strong signal; first OR last as corroboration
        out["confirmed"] = ("email" in matched) or (
            "first" in matched and "last" in matched)
    except Exception:
        pass
    return out


def _body_text_cdp(sb: Any) -> str:
    try:
        return sb.cdp.get_text("body") or ""
    except Exception:
        try:
            return sb.get_text("body") or ""
        except Exception:
            return ""


def _click_submit_button(sb: Any, os_input: bool = False) -> bool:
    """Click a "Submit"/submit button (OS-click when os_input)."""
    for sel in OTP_SUBMIT_SELECTORS:
        try:
            if not sb.cdp.find_element(sel):
                continue
        except Exception:
            continue
        try:
            if os_input:
                sb.cdp.gui_click_element(sel)
            else:
                sb.cdp.click(sel)
            return True
        except Exception:
            continue
    return False


def _fill_address(sb: Any, full_address: str, os_input: bool = False) -> bool:
    """Fill the post-verify delivery-address field + press Enter to pick the
    first suggestion. VERIFIES the value landed and retries (one acct missed the
    address because the single attempt silently no-op'd). Returns True only if
    the field actually shows the address."""
    if not full_address:
        return False
    for sel in ADDR_SELECTORS:
        try:
            if not sb.cdp.find_element(sel):
                continue
        except Exception:
            continue
        for attempt in range(3):
            try:
                if os_input:
                    sb.cdp.gui_click_element(sel)
                    time.sleep(0.4)
                    sb.cdp.gui_write(full_address)
                else:
                    sb.cdp.click(sel)
                    sb.cdp.press_keys(sel, full_address)
                time.sleep(2.5)  # let autocomplete suggestions populate
                # verify the value landed before committing with Enter
                if not _field_value(sb, sel).strip():
                    time.sleep(0.6)
                    continue  # no-op'd — retry this selector
                try:
                    sb.cdp.press_keys(sel, "\n")
                except Exception:
                    pass
                time.sleep(2.0)
                return True
            except Exception:
                time.sleep(0.5)
                continue
    return False


def _skip_upsell(sb: Any, os_input: bool = False) -> None:
    """Click a "Skip"/"Not now" on a DashPass upsell if one is present."""
    for sel in SKIP_SELECTORS:
        try:
            if not sb.cdp.find_element(sel):
                continue
        except Exception:
            continue
        try:
            if os_input:
                sb.cdp.gui_click_element(sel)
            else:
                sb.cdp.click(sel)
            time.sleep(1.5)
            return
        except Exception:
            continue


def signup_via_cdp(identity: dict[str, Any], *,
                   poll_otp: Callable[[], str],
                   proxy: str | None = None,
                   headless: bool = False,
                   use_chromium: bool = False,
                   ios_mobile: bool = False,
                   os_input: bool = False,
                   pre_submit_dwell_s: float = 0.0,
                   emit: Callable[[str, dict], None] | None = None,
                   otp_total_wait_s: float = 180.0,
                   screenshot_dir: str | None = None,
                   ) -> dict[str, Any]:
    """Create one DoorDash account via CDP Mode. SYNC — run in a thread.

    ``proxy`` is the ``user:pass@host:port`` residential gateway string, or
    None to run DIRECT on the home IP — which is what EVERY old working script
    actually did (recipe A/B). The proxy path kept hanging/blocking, so direct
    home-IP is now first-class, not diagnostics-only.

    ``ios_mobile`` reproduces the old recipe B: iOS Safari UA + iPhone mobile
    emulation (Chrome device metrics). The most-copied historical variant.

    Returns the uc_signup-compatible dict, with an extra possible outcome
    ``"ip_blocked"`` — which is GOOD (past the fingerprint gate; just rotate the
    home IP). Captures a screenshot at each stage into ``screenshot_dir``.
    """
    from seleniumbase import SB

    def _emit(t: str, d: dict | None = None) -> None:
        if emit:
            try:
                emit(t, d or {})
            except Exception:
                pass

    def _shot(tag: str) -> None:
        if not screenshot_dir or sb is None:
            return
        try:
            import os
            os.makedirs(screenshot_dir, exist_ok=True)
            sb.save_screenshot(os.path.join(screenshot_dir,
                                            f"signup_{tag}.png"))
        except Exception:
            pass

    phone10 = normalize_phone(identity.get("phone_number", ""))
    result: dict[str, Any] = {"outcome": "failed", "storage_state": None,
                              "phone10": phone10}

    kwargs: dict[str, Any] = dict(uc=True, test=True, locale="en",
                                  headless=headless)
    if proxy:
        kwargs["proxy"] = proxy
    if use_chromium:
        # Mintz: unbranded Chromium (no "Google Chrome" branding) is stealthier
        # and dodges some bot/reCAPTCHA fingerprints.
        kwargs["use_chromium"] = True
    if ios_mobile:
        # Recipe B: iOS Safari UA + Chrome mobile emulation at iPhone metrics.
        kwargs["agent"] = IOS_UA
        kwargs["mobile"] = True
        kwargs["device_metrics"] = IOS_METRICS
    else:
        # Desktop: a modest, watchable window (don't fill the user's monitor).
        kwargs["window_size"] = "1200,820"

    sb = None
    try:
        with SB(**kwargs) as sb:
            # Warm the session on the consumer LOGIN page first (trust cookies),
            # then navigate DIRECTLY to the consumer signup URL. We do NOT click
            # a generic "Sign Up" link — the login page's "Sign Up" goes to the
            # DASHER (driver) signup (dasher.doordash.com), the wrong form.
            sb.activate_cdp_mode(LOGIN_URL)
            time.sleep(2.5)
            try:
                sb.cdp.open(SIGNUP_URL)
            except Exception:
                sb.open(SIGNUP_URL)   # sb.cdp has no .get(); sb.open does
            time.sleep(3.0)
            # Guard: never proceed on the dasher signup form.
            try:
                if "dasher.doordash.com" in _cdp_url(sb):
                    sb.cdp.open(SIGNUP_URL)
                    time.sleep(2.0)
            except Exception:
                pass
            _emit("signup_form_open", {"url": _cdp_url(sb)[:80]})
            _shot("01_open")

            # The bot/CF gate can fire on LOAD (rare) or SUBMIT (our usual 403).
            # gui_captcha=False: signup's gate is a server-side reject, not a
            # clickable Turnstile — the PyAutoGUI rung would only hang the mouse.
            clear_captcha_ladder(sb, emit=emit, gui_captcha=False)

            # The mobile form no-ops the FIRST press_keys after each focus change
            # (input handlers bind a beat late), so single-pass fill drops
            # whichever field is typed first. Fix: define all fields, then make
            # up to 3 passes, re-pressing only the ones whose .value is still
            # empty — order-independent and self-healing.
            FIELDS = [
                ("first", NAME_FIRST_SELECTORS, identity.get("first_name", "")),
                ("last", NAME_LAST_SELECTORS, identity.get("last_name", "")),
                ("email", (SEL_EMAIL,), identity.get("email", "")),
                ("phone", (SEL_PHONE,), phone10),
                ("password", (SEL_PASSWORD,), identity.get("password", "")),
            ]
            # os_input=True uses REAL OS-level mouse+keyboard (PyAutoGUI) — the
            # synthetic CDP press_keys is what PerimeterX rejects; real hardware
            # telemetry is the behavioral fix being tested.
            filler = _gui_press_any if os_input else _press_any
            fills = {k: (not val) for k, _sels, val in FIELDS}  # empty val = ok
            for _pass in range(3):
                for key, sels, val in FIELDS:
                    if fills[key]:
                        continue  # already landed
                    fills[key] = filler(sb, sels, val)
                if all(fills.values()):
                    break
                time.sleep(0.5)  # let late-binding fields settle, re-press
            _emit("signup_form_filled", dict(fills, mode="os" if os_input
                                             else "cdp"))
            _shot("02_filled")

            # Don't submit a form with empty REQUIRED fields — that's what hit
            # "First name is required" and dead-ended at signup_no_verify. Fail
            # loudly with which field(s) didn't land so it's fixable, not silent.
            missing = [k for k, v in fills.items() if not v]
            if missing:
                result["outcome"] = "failed"
                result["fill_missing"] = missing
                _emit("signup_fill_incomplete", {"missing": missing})
                _shot("02b_fill_incomplete")
                return result

            # Human dwell before submit — a real person pauses after typing the
            # last field. Instant submit is itself a bot tell.
            if pre_submit_dwell_s > 0:
                time.sleep(pre_submit_dwell_s)

            # Submit via real OS click too when os_input (consistent telemetry).
            submitted = False
            if os_input:
                for sel in (SEL_SUBMIT, SEL_SUBMIT_FALLBACK):
                    try:
                        if sb.cdp.find_element(sel):
                            sb.cdp.gui_click_element(sel)
                            submitted = True
                            break
                    except Exception:
                        continue
            if not submitted:
                try:
                    sb.cdp.click(SEL_SUBMIT)
                except Exception:
                    try:
                        sb.cdp.click(SEL_SUBMIT_FALLBACK)
                    except Exception:
                        pass
            _emit("signup_submitting", {})
            time.sleep(5.0)
            _shot("03_submitted")

            # The bot gate fires HERE (403 user_assessment_bot) — the key
            # unknown vs Mintz's read-only demos. Try the captcha ladder once
            # (the gate sometimes shows a CF interstitial we can clear), then
            # check for the hard bot-block body.
            # IP-rep block = GOOD (past the fingerprint gate). Check it FIRST so
            # we don't misreport it as the bot gate — the action differs: rotate
            # the home IP and retry, not re-engineer stealth.
            if _page_has(sb, IP_BLOCK_MARKERS):
                result["outcome"] = "ip_blocked"
                _emit("signup_ip_blocked",
                      {"note": "PAST the fingerprint gate — rotate home IP "
                               "(Mullvad) and retry"})
                _shot("04_ip_blocked")
                return result
            if _page_has(sb, BOT_BLOCK_MARKERS):
                # Maybe a CF challenge we can clear — try the ladder, re-check.
                clear_captcha_ladder(sb, emit=emit, gui_captcha=False)
                time.sleep(2.0)
            if _page_has(sb, IP_BLOCK_MARKERS):
                result["outcome"] = "ip_blocked"
                _emit("signup_ip_blocked",
                      {"note": "PAST the fingerprint gate — rotate home IP "
                               "(Mullvad) and retry"})
                _shot("04_ip_blocked")
                return result
            if _page_has(sb, BOT_BLOCK_MARKERS):
                result["outcome"] = "bot_blocked"
                _emit("signup_bot_blocked", {})
                _shot("04_bot_blocked")
                return result

            # Wait for the verify/OTP MODAL — it renders as a separate step and
            # can take ~20s+ to appear, so wait generously (was 45s, the modal
            # sometimes hadn't shown yet).
            deadline = time.time() + 75
            while time.time() < deadline:
                if _page_has(sb, VERIFY_MARKERS):
                    break
                if any(m in _cdp_url(sb) for m in SUCCESS_URL_MARKERS):
                    result["outcome"] = "created"
                    result["storage_state"] = _export_storage(sb)
                    result["profile_confirmed"] = _finish_account(
                        sb, identity, os_input, _emit, _shot)
                    _shot("05_created_nootp")
                    return result
                if _page_has(sb, BOT_BLOCK_MARKERS):
                    result["outcome"] = "bot_blocked"
                    _emit("signup_bot_blocked", {})
                    _shot("04_bot_blocked")
                    return result
                time.sleep(1.5)
            else:
                result["outcome"] = "failed"
                _emit("signup_no_verify", {})
                _shot("04_no_verify")
                return result

            _emit("otp_waiting", {})
            _shot("05_otp_step")
            started = time.time()
            tried: set[str] = set()
            while time.time() - started < otp_total_wait_s:
                code = poll_otp()
                if code and code not in tried:
                    tried.add(code)
                    _emit("otp_received", {"code": code})
                    # _enter_otp now also CLICKS the modal's Submit button (the
                    # live run stalled because entering the code alone didn't
                    # advance) and uses OS input when os_input.
                    if _enter_otp(sb, code, os_input):
                        for _ in range(8):  # success redirect can be slow
                            time.sleep(2.5)
                            if any(m in _cdp_url(sb)
                                   for m in SUCCESS_URL_MARKERS):
                                result["outcome"] = "created"
                                result["storage_state"] = _export_storage(sb)
                                _emit("signup_created", {})
                                _shot("06_created")
                                # post-verify: fill delivery address + skip the
                                # DashPass upsell so the account is fully usable.
                                result["profile_confirmed"] = _finish_account(
                                    sb, identity, os_input, _emit, _shot)
                                return result
                time.sleep(3.0)
            result["outcome"] = "otp_timeout"
            _shot("06_otp_timeout")
            return result
    except Exception as exc:  # noqa: BLE001 — surfaced to the caller as outcome
        err = f"{type(exc).__name__}: {exc}"[:200]
        # A Chrome/SB init error can embed the --proxy-server value (creds).
        # Redact the proxy password before it reaches any emit handler / UI.
        if proxy and "@" in proxy:
            pwd = proxy.split("@", 1)[0].rsplit(":", 1)[-1]
            if pwd:
                err = err.replace(pwd, "<redacted>")
        _emit("signup_error", {"error": err})
        return result


def _export_storage(sb: Any) -> dict[str, Any]:
    """Export cookies in Playwright storage_state shape (cookies only).

    CDP Mode exposes cookies via ``sb.cdp.get_all_cookies()`` (or the sync
    ``sb.get_cookies()`` after reconnecting). Normalizes sameSite like
    uc_signup._export_storage so the new account drops into the Playwright
    session machinery unchanged.
    """
    cookies = []
    raw: list[dict] = []
    try:
        raw = sb.cdp.get_all_cookies() or []
    except Exception:
        try:
            raw = sb.get_cookies() or []
        except Exception:
            raw = []
    for c in raw:
        # CDP cookie objects may be attrs or dicts — normalize.
        def _g(key: str, default: Any = "") -> Any:
            if isinstance(c, dict):
                return c.get(key, default)
            return getattr(c, key, default)

        ck = {
            "name": _g("name", ""),
            "value": _g("value", ""),
            "domain": _g("domain", ""),
            "path": _g("path", "/") or "/",
            "httpOnly": bool(_g("httpOnly", _g("http_only", False))),
            "secure": bool(_g("secure", False)),
        }
        expires = _g("expires", _g("expiry", None))
        if expires is not None:
            try:
                ck["expires"] = int(expires)
            except (TypeError, ValueError):
                pass
        ck["sameSite"] = _norm_same_site(_g("sameSite", _g("same_site", "Lax")))
        cookies.append(ck)
    return {"cookies": cookies, "origins": []}


def _norm_same_site(raw: Any) -> str:
    """Normalize a cookie sameSite to Playwright's {Strict, Lax, None}.

    CDP cookies from ``sb.cdp.get_all_cookies()`` carry ``same_site`` as a
    ``mycdp.network.CookieSameSite`` ENUM — ``str(enum)`` yields
    ``"CookieSameSite.LAX"`` (NOT ``"Lax"``), which would silently collapse
    EVERY cookie to the Lax default and break any ``sameSite=None`` cookie
    cross-site. Pull ``.value`` from an enum; otherwise capitalize the string.
    """
    import enum
    if isinstance(raw, enum.Enum):
        raw = raw.value  # 'Strict' | 'Lax' | 'None'
    ss = str(raw or "Lax").capitalize()
    # Map the lowercased enum-value spellings too ('none' -> 'None').
    return ss if ss in ("Strict", "Lax", "None") else "Lax"
