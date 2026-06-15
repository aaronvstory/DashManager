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


def _press(sb: Any, selector: str, value: str) -> bool:
    """Type a field at human speed via CDP press_keys (per-key delay)."""
    try:
        sb.cdp.click(selector)
        time.sleep(0.4)
        sb.cdp.press_keys(selector, value)
        time.sleep(0.4)
        return True
    except Exception:
        return False


def _enter_otp(sb: Any, code: str) -> bool:
    """Enter the OTP into the verify step (single field or 6 split boxes).

    Mirrors uc_signup._enter_otp but uses CDP element handles.
    """
    digits = re.sub(r"\D", "", code)
    if not digits:
        return False
    try:
        boxes = sb.cdp.find_elements(OTP_DIGIT_BOXES)
    except Exception:
        boxes = []
    try:
        if boxes and len(boxes) >= 4:
            boxes[0].click()
            time.sleep(0.2)
            boxes[0].send_keys(digits)
            time.sleep(0.5)
        else:
            # Single combined field.
            try:
                sb.cdp.press_keys(OTP_DIGIT_BOXES, digits)
            except Exception:
                if boxes:
                    boxes[0].send_keys(digits)
                else:
                    return False
        time.sleep(1.0)
        return True
    except Exception:
        return False


def signup_via_cdp(identity: dict[str, Any], *,
                   poll_otp: Callable[[], str],
                   proxy: str | None = None,
                   headless: bool = False,
                   use_chromium: bool = False,
                   pre_submit_dwell_s: float = 0.0,
                   emit: Callable[[str, dict], None] | None = None,
                   otp_total_wait_s: float = 180.0,
                   screenshot_dir: str | None = None,
                   ) -> dict[str, Any]:
    """Create one DoorDash account via CDP Mode. SYNC — run in a thread.

    ``proxy`` is the ``user:pass@host:port`` residential gateway string (or
    None to run direct — only for diagnostics; signup needs the proxy). Returns
    the uc_signup-compatible dict. Captures a screenshot at each stage into
    ``screenshot_dir`` when given (the audit trail the handoff asks for).
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

    sb = None
    try:
        with SB(**kwargs) as sb:
            sb.activate_cdp_mode(SIGNUP_URL)
            time.sleep(3.0)
            _emit("signup_form_open", {})
            _shot("01_open")

            # The bot/CF gate can fire on LOAD (rare) or SUBMIT (our usual 403).
            # gui_captcha=False: signup's gate is a server-side reject, not a
            # clickable Turnstile — the PyAutoGUI rung would only hang the mouse.
            clear_captcha_ladder(sb, emit=emit, gui_captcha=False)

            _press(sb, SEL_FIRST, identity.get("first_name", ""))
            _press(sb, SEL_LAST, identity.get("last_name", ""))
            _press(sb, SEL_EMAIL, identity.get("email", ""))
            _press(sb, SEL_PHONE, phone10)
            _press(sb, SEL_PASSWORD, identity.get("password", ""))
            _emit("signup_form_filled", {})
            _shot("02_filled")

            # Human dwell before submit — a real person pauses after typing the
            # last field. Instant submit is itself a bot tell.
            if pre_submit_dwell_s > 0:
                time.sleep(pre_submit_dwell_s)

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
            if _page_has(sb, BOT_BLOCK_MARKERS):
                # Maybe a CF challenge we can clear — try the ladder, re-check.
                clear_captcha_ladder(sb, emit=emit, gui_captcha=False)
                time.sleep(2.0)
            if _page_has(sb, BOT_BLOCK_MARKERS):
                result["outcome"] = "bot_blocked"
                _emit("signup_bot_blocked", {})
                _shot("04_bot_blocked")
                return result

            # Wait for the verify/OTP step (or an immediate logged-in redirect).
            deadline = time.time() + 45
            while time.time() < deadline:
                if _page_has(sb, VERIFY_MARKERS):
                    break
                if any(m in _cdp_url(sb) for m in SUCCESS_URL_MARKERS):
                    result["outcome"] = "created"
                    result["storage_state"] = _export_storage(sb)
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
                    if _enter_otp(sb, code):
                        for _ in range(6):
                            time.sleep(2.0)
                            if any(m in _cdp_url(sb)
                                   for m in SUCCESS_URL_MARKERS):
                                result["outcome"] = "created"
                                result["storage_state"] = _export_storage(sb)
                                _emit("signup_created", {})
                                _shot("06_created")
                                return result
                time.sleep(3.0)
            result["outcome"] = "otp_timeout"
            _shot("06_otp_timeout")
            return result
    except Exception as exc:  # noqa: BLE001 — surfaced to the caller as outcome
        _emit("signup_error", {"error": f"{type(exc).__name__}: {exc}"[:200]})
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
        ss = (_g("sameSite", _g("same_site", "Lax")) or "Lax")
        ss = str(ss).capitalize()
        ck["sameSite"] = ss if ss in ("Strict", "Lax", "None") else "Lax"
        cookies.append(ck)
    return {"cookies": cookies, "origins": []}
