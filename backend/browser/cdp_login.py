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
    _modal_gone_from_source,
    clear_captcha_ladder,
    focus_signup_window,
)
from backend.browser.delivery_prefs import pick_instruction

# The consumer login page (email-first → passwordless OTP for these accounts).
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

# Login-screen controls, verified against the live DOM 2026-06-17.
EMAIL_INPUT_SEL = 'input[type="email"]'
PASSWORD_SEL = 'input[type="password"]'
# Any OTP "Verify code" input — the 6 split digit boxes (inputmode/maxlength) OR
# a single code field. Matches what _enter_otp targets.
OTP_ANY_SEL = ('input[inputmode="numeric"], input[maxlength="1"], '
               'input[autocomplete="one-time-code"]')
# The submit button. The stable id #guided-submit-button = "Continue to Sign In"
# (also the password "Sign In" step). EXACT text fallbacks — NEVER the social
# buttons (a broad contains("Continue") wrongly matches "Continue with Google").
CONTINUE_SELECTORS = ('#guided-submit-button',
                      'button:contains("Continue to Sign In")',
                      'button:contains("Sign In")')
# OTP-screen escape hatches when the SMS code isn't arriving (verified live
# 2026-06-18): switch to password auth, or re-trigger the SMS.
USE_PASSWORD_SELECTORS = ('button:contains("Use password instead")',
                          'a:contains("Use password instead")',
                          'div:contains("Use password instead")',
                          '*:contains("Use password instead")')
RESEND_CODE_SELECTORS = ('button:contains("Resend code")',
                         'a:contains("Resend code")',
                         '*:contains("Resend code")')


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


def _submit_password(sb: Any, password: str, emit_e) -> None:
    """Type the password + submit robustly (Enter → button → JS form-submit).

    The "Sign In" button id varies and a CDP click on it can no-op, so press
    Enter in the field first (submits the form regardless of button impl),
    then fall back to clicking the button, then to a JS requestSubmit().
    """
    try:
        sb.cdp.click(PASSWORD_SEL)
        sb.cdp.press_keys(PASSWORD_SEL, password)
        time.sleep(0.5)
        submitted = False
        try:
            sb.cdp.press_keys(PASSWORD_SEL, "\n")
            submitted = True
        except Exception:
            pass
        if not _click_first(sb, CONTINUE_SELECTORS + ('button[type="submit"]',)) \
                and not submitted:
            try:
                sb.cdp.evaluate(
                    "document.querySelector('input[type=\"password\"]')"
                    ".form.requestSubmit()")
            except Exception:
                pass
        emit_e("login_password_submitted", {})
    except Exception as exc:
        emit_e("login_warn", {"step": "password", "error": str(exc)[:100]})


def _click_first(sb: Any, selectors: tuple[str, ...]) -> bool:
    """Click the first selector that matches. Returns True if a click fired."""
    sel = _find(sb, selectors)
    if sel is None:
        return False
    try:
        sb.cdp.click(sel)
        return True
    except Exception:
        return False


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
    if any(m in url for m in SUCCESS_URL_MARKERS):
        return True
    # The post-OTP landing can be the bare doordash.com/ root showing the
    # logged-in home (the "$0 delivery fee" address modal). That URL lacks the
    # markers above, so also accept a doordash page whose content is the home
    # address-entry modal (only rendered when logged in) — verified live.
    # _cdp_source already lower-cases; the explicit .lower() here keeps the
    # case-insensitive contract local + robust if that ever changes (the
    # negative "continue to sign in" guard must reliably suppress the login page).
    src = _cdp_source(sb).lower()
    return ("enter delivery address" in src
            and "delivery fee" in src
            and "continue to sign in" not in src)


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


def phone_login_via_cdp(email: str, *,
                        poll_otp: Callable[[], str],
                        password: str | None = None,
                        proxy: str | None = None,
                        headless: bool = False,
                        os_input: bool = True,
                        otp_total_wait_s: float = 240.0,
                        set_address: str | None = None,
                        instruction: str | None = None,
                        emit: Callable[[str, dict], None] | None = None,
                        screenshot_dir: str | None = None,
                        after_login: Callable[[Any], Any] | None = None
                        ) -> dict[str, Any]:
    """Log in via EMAIL → OTP using SeleniumBase CDP (beats the login CF gate).

    The flow verified live 2026-06-17: clear the Cloudflare Turnstile, enter the
    EMAIL, "Continue to Sign In" → DoorDash goes passwordless and sends a 6-digit
    code to the account's PHONE (its rented api.cc number), which ``poll_otp``
    fetches. Enter the code → /home. (No password needed — these accounts route
    to the OTP-first "Verify code" screen.) os_input is unused for typing here
    (CDP press_keys is focus-independent) but kept for signature stability.

    If set_address is given, ALSO sets the delivery address + "Hand it to me" +
    a dasher instruction in the SAME session (a second context would re-hit CF).

    SYNC; call via asyncio.to_thread.
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
        if after_login is not None:
            # Run caller work IN-SESSION (past Cloudflare) before SB() closes —
            # e.g. scrape /orders or drive a support chat in the same browser.
            try:
                result["after_login"] = after_login(sb)
            except Exception as exc:
                _e("after_login_error", {"error": f"{type(exc).__name__}: {exc}"[:200]})
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
            # activate_cdp_mode can land on a stale/previous tab (verified live
            # 2026-06-18 — opened on a leftover Google search), so explicitly
            # (re)navigate to the login URL and settle before proceeding.
            try:
                sb.cdp.open(LOGIN_URL)
                time.sleep(4.0)
            except Exception:
                pass
            _shot("01_open")

            # Already logged in?
            if _is_logged_in(sb):
                return _finalize(sb)

            # Clear the login CF Turnstile (clickable checkbox -> gui_captcha).
            # It can re-appear after the email step, so we re-clear below too.
            clear_captcha_ladder(sb, emit=emit, gui_captcha=True)
            time.sleep(1.0)
            _shot("02_post_cf")

            def _wait_for(sel: str, secs: float = 20.0) -> bool:
                end = time.time() + secs
                while time.time() < end:
                    if _find(sb, (sel,)):
                        return True
                    time.sleep(1.0)
                return False

            # STEP 1: enter the EMAIL, "Continue to Sign In". The email field can
            # lag a beat behind the CF clear, so wait for it. If still missing,
            # re-open the login URL once (the UC tab can mis-land) before bailing.
            if not _wait_for(EMAIL_INPUT_SEL, 20):
                sb.cdp.open(LOGIN_URL)
                time.sleep(4.0)
                clear_captcha_ladder(sb, emit=emit, gui_captcha=True)
                if _is_logged_in(sb):
                    return _finalize(sb)
                if not _wait_for(EMAIL_INPUT_SEL, 20):
                    _shot("03_no_email_field")
                    result["outcome"] = "no_email_field"
                    return result
            try:
                sb.cdp.click(EMAIL_INPUT_SEL)
                sb.cdp.press_keys(EMAIL_INPUT_SEL, email)
                time.sleep(0.5)
                _click_first(sb, CONTINUE_SELECTORS)
                _e("login_email_submitted", {})
            except Exception as exc:
                _e("login_warn", {"step": "email", "error": str(exc)[:100]})
            time.sleep(3.0)
            clear_captcha_ladder(sb, emit=emit, gui_captcha=True)
            _shot("04_email_submitted")

            if _is_logged_in(sb):
                return _finalize(sb)

            # "Incorrect email — we couldn't find an account" = the account was
            # never finalized (signup OTP-timed-out). Distinct from a real
            # password/OTP screen, so report it precisely (verified live).
            src = _cdp_source(sb)
            if "couldn't find an account" in src or "incorrect email" in src:
                _shot("04_no_account")
                result["outcome"] = "no_account"
                return result

            # STEP 2: a real account goes passwordless → "Verify code" (OTP sent
            # to the account's phone). Poll api.cc + enter the code via the shared
            # _enter_otp. If a real PASSWORD prompt shows instead (no OTP boxes
            # AND a "welcome back / enter your password" screen), enter the saved
            # password + Sign In, re-clear any CF re-gate, then fall through to the
            # OTP wait (DoorDash often does password THEN a phone OTP). Only bail
            # as needs_password if we have no password to type.
            if not _wait_for(OTP_ANY_SEL, 25):
                src = _cdp_source(sb)
                if _find(sb, (PASSWORD_SEL,)) and (
                        "welcome back" in src or "enter your password" in src):
                    if not password:
                        _shot("04_wants_password")
                        result["outcome"] = "needs_password"
                        return result
                    _shot("04_password_screen")
                    _submit_password(sb, password, _e)
                    time.sleep(3.0)
                    clear_captcha_ladder(sb, emit=emit, gui_captcha=True)
                    _shot("05_post_password")
                    # Password alone may complete the login (no OTP step).
                    if _is_logged_in(sb):
                        return _finalize(sb)
                    # Otherwise wait for the OTP boxes the password step triggers.
                    _wait_for(OTP_ANY_SEL, 25)

            # STEP 3: poll + enter OTP, but DON'T wait blindly. Poll api.cc on a
            # tight loop; if the SMS isn't arriving, resend once, and finally
            # fall back to "Use password instead" (when we have a password) —
            # mirrors what a human does on this screen (verified live 2026-06-18).
            started = time.time()
            tried: set[str] = set()
            resent = False
            pw_fallback_done = False
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
                        modal_gone = _modal_gone_from_source(_cdp_source(sb))
                        left_auth = not any(
                            s in url.lower()
                            for s in ("verify", "/auth", "login"))
                        if modal_gone and left_auth and "doordash.com" in url:
                            return _finalize(sb)
                        time.sleep(2.0)
                else:
                    elapsed = time.time() - started
                    # ~30s in with no code: resend the SMS once.
                    if not resent and elapsed > 30 and not tried:
                        if _click_first(sb, RESEND_CODE_SELECTORS):
                            _e("login_otp_resend", {})
                        resent = True
                    # ~60s in, still nothing, and we have a password: switch to
                    # "Use password instead" and complete via the password path.
                    elif (not pw_fallback_done and password and elapsed > 60
                          and not tried):
                        pw_fallback_done = True
                        if _click_first(sb, USE_PASSWORD_SELECTORS):
                            _e("login_use_password_instead", {})
                            time.sleep(2.0)
                            if _wait_for(PASSWORD_SEL, 10):
                                _shot("05_password_fallback")
                                _submit_password(sb, password, _e)
                                time.sleep(3.0)
                                clear_captcha_ladder(sb, emit=emit,
                                                     gui_captcha=True)
                                if _is_logged_in(sb):
                                    return _finalize(sb)
                                # password may itself trigger a fresh OTP — loop
                                # continues and will pick up the new code.
                                _wait_for(OTP_ANY_SEL, 15)
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


def scrape_orders_via_cdp(sb: Any, *,
                          emit: Callable[[str, dict], None] | None = None,
                          scroll_iters: int = 8) -> list[dict[str, Any]]:
    """Read /orders in a LOGGED-IN SeleniumBase CDP session, past Cloudflare.

    Plain Playwright re-trips the CF Turnstile on /orders even with valid login
    cookies (CF fingerprints the engine, not just the session). The CDP/UC
    browser is already past CF, so we navigate + extract HERE, reusing the pure
    card parsers from ``orders.py``. If /orders DOES re-gate, we detect it and
    run the GUI captcha ladder (autonomous recovery — no manual click needed).

    Returns a list of dicts: {order_uuid, receipt_url, store_name, price,
    cancelled, pending_claim, claimable_from_card, description, items_count}.
    SYNC; call via asyncio.to_thread.
    """
    from backend.browser.orders import (_EXTRACT_CARDS_JS, extract_order_uuid,
                                         parse_card_text)
    from backend.browser.selectors import (ORDER_CARD_SELECTORS,
                                            ORDER_LINK_SELECTOR, ORDERS_URL)
    _e = _emit_factory(emit)

    sb.cdp.open(ORDERS_URL)
    time.sleep(4.0)
    # Autonomous CF recovery: /orders can re-gate; clear it in-session.
    clear_captcha_ladder(sb, emit=emit, gui_captcha=True)
    time.sleep(3.0)

    # Pick the first card selector that matches, scrolling to lazy-load all.
    chosen = None
    for sel in ORDER_CARD_SELECTORS:
        try:
            if sb.cdp.find_elements(sel):
                chosen = sel
                break
        except Exception:
            continue
    if not chosen:
        _e("cdp_orders_none", {})
        return []

    prev = stable = 0
    for _ in range(scroll_iters):
        try:
            sb.cdp.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            pass
        time.sleep(1.0)
        try:
            count = len(sb.cdp.find_elements(chosen) or [])
        except Exception:
            count = prev
        if count == prev and count > 0:
            stable += 1
            if stable >= 2:
                break
        else:
            stable = 0
        prev = count

    raw = sb.cdp.evaluate(
        _EXTRACT_CARDS_JS.replace("(sel) =>", "() =>").replace(
            "sel.card", repr(chosen)).replace(
            "sel.link", repr(ORDER_LINK_SELECTOR))) or []

    orders: list[dict[str, Any]] = []
    seen: set[str] = set()
    claim_idx = 0
    for item in raw:
        href = (item or {}).get("href") or ""
        text = (item or {}).get("text") or ""
        parsed = parse_card_text(text)
        uuid = extract_order_uuid(href)
        if uuid is None:
            if not parsed.get("pending_claim"):
                continue
            claim_idx += 1
            uuid = f"pendingclaim:{parsed['store_name']}:{claim_idx}"
            if uuid in seen:
                continue
            seen.add(uuid)
            orders.append({**parsed, "order_uuid": uuid, "receipt_url": "",
                           "claimable_from_card": True})
            continue
        if uuid in seen:
            continue
        seen.add(uuid)
        orders.append({**parsed, "order_uuid": uuid, "receipt_url": href,
                       "claimable_from_card": parsed.get("pending_claim",
                                                         False)})
    _e("cdp_orders_scraped", {"count": len(orders)})
    return orders


# JS that extracts the cost-breakdown rows (Subtotal/Total/Refund/...) as clean
# "label\n$value" pairs. DoorDash renders each row as label+value in sibling
# leaf elements, and get_text("body") collapses the whole receipt onto ONE line
# (so the line-pair detector sees no bare "Total"). This walks the DOM to the
# row's grandparent and pulls the last money token — yielding detector-friendly
# text the pure detect() parses correctly. Verified live 2026-06-18.
_RECEIPT_BREAKDOWN_JS = r"""
(() => {
  // Dedup non-Refund labels (a "Total savings" footer can repeat "Total"), but
  // emit EVERY "Refund" row — a partial-then-full refund renders two Refund
  // lines and detect() needs the last/largest, so we must not drop the second.
  const labels = ['Subtotal','Delivery Fee','Service Fee','Estimated Tax',
                  'Dasher Tip','Total'];
  const REFUND = 'Refund';
  const out = [];
  const seen = new Set();
  [...document.querySelectorAll('*')].forEach(e => {
    if (e.children.length !== 0) return;
    const t = (e.textContent || '').trim();
    const isRefund = (t === REFUND);
    if (!isRefund && (!labels.includes(t) || seen.has(t))) return;
    const row = (e.parentElement && e.parentElement.parentElement)
              ? e.parentElement.parentElement.textContent : '';
    const m = row.match(/-?\$[0-9,]+\.[0-9]{2}/g);
    if (m) { out.push(t); out.push(m[m.length - 1]); if (!isRefund) seen.add(t); }
  });
  return out.join('\n');
})()
"""


def read_receipt_via_cdp(sb: Any, receipt_url: str, *,
                         emit: Callable[[str, dict], None] | None = None,
                         settle_s: float = 7.0) -> str:
    """Open one receipt in the logged-in CDP session (past CF) and return text
    the pure detect() can parse. Extracts the cost breakdown via DOM (since
    get_text collapses the receipt onto one line) and appends the raw body for
    cancelled/pending/remake prose detection. Returns "" if unreadable.

    The receipt loads slowly and a too-early read catches a store-page redirect
    or an unrendered shell — so settle generously and clear any CF re-gate.
    """
    _e = _emit_factory(emit)
    sb.cdp.open(receipt_url)
    time.sleep(settle_s)
    clear_captcha_ladder(sb, emit=emit, gui_captcha=True)
    time.sleep(2.0)
    try:
        body = sb.cdp.get_text("body") or ""
    except Exception as exc:
        _e("cdp_receipt_error", {"error": str(exc)[:120]})
        return ""
    low = body.lower()
    if "just a moment" in low or "verify you are human" in low:
        clear_captcha_ladder(sb, emit=emit, gui_captcha=True)
        time.sleep(2.5)
        try:
            body = sb.cdp.get_text("body") or ""
        except Exception:
            return ""
    # If the nav bounced to the store menu (slow receipt load), retry once.
    if "/store/" in (_cdp_url(sb) or "") or "Menu & Prices" in body:
        sb.cdp.open(receipt_url)
        time.sleep(settle_s + 2)
        try:
            body = sb.cdp.get_text("body") or ""
        except Exception:
            body = ""
    breakdown = ""
    try:
        breakdown = sb.cdp.evaluate(_RECEIPT_BREAKDOWN_JS) or ""
    except Exception:
        pass
    # Breakdown (clean label\nvalue pairs) first so detect() parses Total/Refund;
    # body after it for cancelled/pending/remake prose.
    return (breakdown + "\n" + body) if breakdown else body
