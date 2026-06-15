"""DoorDash signup via RAW undetected_chromedriver — a 1:1 reproduction of the
OLD WORKING scripts (F:\\iCloudDrive\\F\\dash\\script\\run.py et al.).

WHY THIS EXISTS: every prior attempt used SeleniumBase's UC Mode (a reimplemented
fork) or Playwright/Camoufox — all hit DoorDash's "something went wrong"
(PerimeterX) reject on submit. The scripts that DEMONSTRABLY worked used the RAW
``undetected_chromedriver`` package directly:

    import undetected_chromedriver.v2 as uc
    options = uc.ChromeOptions()
    options.add_argument('--disable-notifications')
    options.add_argument('--disable-infobars')
    options.add_argument('--mute-audio')
    options.add_argument('--start-maximized')
    options.add_argument('--disable-blink-features=AutomationControlled')
    driver = uc.Chrome(options=options, use_subprocess=True)

Raw UC patches Chrome at the cdc_/navigator level differently than SeleniumBase's
UC, which is the untried variable. This module reproduces that stack exactly:
real Chrome, the same 5 flags, ``use_subprocess=True``, NO proxy (home IP — what
the old scripts used), and the consumer login→signup navigation. Optional iOS UA.

SYNCHRONOUS (Selenium). The async layer calls ``signup_via_raw_uc`` through
``asyncio.to_thread``. Return shape MATCHES the other drivers:

    {"outcome": "created"|"bot_blocked"|"ip_blocked"|"otp_timeout"|"failed",
     "storage_state": {...}|None, "phone10": "..."}
"""
from __future__ import annotations

import re
import time
from typing import Any, Callable

from backend.browser.cdp_signup import (IOS_UA, IP_BLOCK_MARKERS, LOGIN_URL)
from backend.browser.uc_signup import (BOT_BLOCK_MARKERS, SIGNUP_URL,
                                       SUCCESS_URL_MARKERS, VERIFY_MARKERS,
                                       normalize_phone)

# Selenium By-CSS field selectors (same stable autocomplete anchors).
SEL_FIRST = ('input[autocomplete="given-name"]', 'input[name="firstName"]',
             'input[placeholder*="First" i]')
SEL_LAST = ('input[autocomplete="family-name"]', 'input[name="lastName"]',
            'input[placeholder*="Last" i]')
SEL_EMAIL = ('input[autocomplete="email"]', 'input[type="email"]')
SEL_PHONE = ('input[autocomplete="tel"]', 'input[type="tel"]')
SEL_PASSWORD = ('input[autocomplete="new-password"]', 'input[type="password"]')
OTP_DIGIT_BOXES = ('input[inputmode="numeric"], input[maxlength="1"], '
                   'input[type="number"]')


def _find(driver: Any, selectors: tuple[str, ...]) -> Any | None:
    from selenium.webdriver.common.by import By
    # implicitly_wait(8) is set globally; without disabling it here, each
    # find_elements for a NON-matching selector blocks the full 8s, so a
    # multi-selector cascade across a multi-pass fill stalls for minutes.
    # Drop it to 0 for these existence probes, restore after.
    try:
        driver.implicitly_wait(0)
    except Exception:
        pass
    try:
        for sel in selectors:
            try:
                els = driver.find_elements(By.CSS_SELECTOR, sel)
                for el in els:
                    if el.is_displayed():
                        return el
            except Exception:
                continue
    finally:
        try:
            driver.implicitly_wait(8)
        except Exception:
            pass
    return None


def _human_fill(driver: Any, selectors: tuple[str, ...], value: str) -> bool:
    """Click + per-key send_keys (real Selenium key events). Verify it landed."""
    if not value:
        return True
    el = _find(driver, selectors)
    if el is None:
        return False
    try:
        el.click()
        time.sleep(0.2)
        el.clear()
    except Exception:
        pass
    import random
    for ch in value:
        try:
            el.send_keys(ch)
            time.sleep(random.uniform(0.04, 0.14))
        except Exception:
            return False
    try:
        return bool((el.get_attribute("value") or "").strip())
    except Exception:
        return True


def _body_text(driver: Any) -> str:
    try:
        from selenium.webdriver.common.by import By
        return (driver.find_element(By.TAG_NAME, "body").text or "").lower()
    except Exception:
        return ""


def _has(driver: Any, markers: tuple[str, ...]) -> bool:
    body = _body_text(driver)
    return any(m in body for m in markers)


def signup_via_raw_uc(identity: dict[str, Any], *,
                      poll_otp: Callable[[], str],
                      headless: bool = False,
                      ios_mobile: bool = False,
                      pre_submit_dwell_s: float = 1.5,
                      emit: Callable[[str, dict], None] | None = None,
                      otp_total_wait_s: float = 180.0,
                      screenshot_dir: str | None = None,
                      ) -> dict[str, Any]:
    """Create one DoorDash account via RAW undetected_chromedriver. SYNC.

    Faithful to the old working ``run.py`` recipe. NO proxy (home IP). Returns
    the shared driver-result dict (incl. the ``ip_blocked`` milestone outcome).
    """
    import undetected_chromedriver as uc

    def _emit(t: str, d: dict | None = None) -> None:
        if emit:
            try:
                emit(t, d or {})
            except Exception:
                pass

    shot_n = [0]

    def _shot(driver: Any, tag: str) -> None:
        if not screenshot_dir:
            return
        try:
            import os
            os.makedirs(screenshot_dir, exist_ok=True)
            shot_n[0] += 1
            driver.save_screenshot(
                os.path.join(screenshot_dir, f"ruc_{shot_n[0]:02d}_{tag}.png"))
        except Exception:
            pass

    phone10 = normalize_phone(identity.get("phone_number", ""))
    result: dict[str, Any] = {"outcome": "failed", "storage_state": None,
                              "phone10": phone10}

    # ── Exact old-script options ──────────────────────────────────────────────
    options = uc.ChromeOptions()
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-infobars")
    options.add_argument("--mute-audio")
    options.add_argument("--disable-blink-features=AutomationControlled")
    if ios_mobile:
        options.add_argument("--user-agent=" + IOS_UA)

    driver = None
    try:
        driver = uc.Chrome(options=options, use_subprocess=True,
                           headless=headless)
        driver.implicitly_wait(8)
        # Size the window AFTER launch (the old scripts' way — UC ignores the
        # --window-size arg and otherwise maximizes onto the whole monitor).
        # iOS: a real iPhone-ish viewport; desktop: the old scripts' 1023x919.
        try:
            if ios_mobile:
                driver.set_window_size(414, 896)
            else:
                driver.set_window_size(1023, 919)
            driver.set_window_position(0, 37)
        except Exception:
            pass

        # warm session on the consumer LOGIN page, then go to consumer signup
        try:
            driver.get(LOGIN_URL)
            time.sleep(3.0)
        except Exception:
            pass
        driver.get(SIGNUP_URL)
        time.sleep(3.5)
        if "dasher.doordash.com" in (driver.current_url or ""):
            driver.get(SIGNUP_URL)
            time.sleep(2.0)
        _emit("signup_form_open", {"url": (driver.current_url or "")[:80]})
        _shot(driver, "01_open")

        if _has(driver, BOT_BLOCK_MARKERS):
            result["outcome"] = "bot_blocked"
            _emit("signup_bot_blocked", {"at": "load"})
            _shot(driver, "bot_blocked_load")
            return result

        # ── fill (multi-pass, verify) ───────────────────────────────────────
        FIELDS = [
            ("first", SEL_FIRST, identity.get("first_name", "")),
            ("last", SEL_LAST, identity.get("last_name", "")),
            ("email", SEL_EMAIL, identity.get("email", "")),
            ("phone", SEL_PHONE, phone10),
            ("password", SEL_PASSWORD, identity.get("password", "")),
        ]
        fills = {k: (not v) for k, _s, v in FIELDS}
        for _ in range(3):
            for key, sels, val in FIELDS:
                if fills[key]:
                    continue
                fills[key] = _human_fill(driver, sels, val)
            if all(fills.values()):
                break
            time.sleep(0.5)
        _emit("signup_form_filled", dict(fills))
        _shot(driver, "02_filled")

        missing = [k for k, v in fills.items() if not v]
        if missing:
            result["outcome"] = "failed"
            result["fill_missing"] = missing
            _emit("signup_fill_incomplete", {"missing": missing})
            _shot(driver, "02b_fill_incomplete")
            return result

        if pre_submit_dwell_s > 0:
            time.sleep(pre_submit_dwell_s)

        # submit
        from selenium.webdriver.common.by import By
        clicked = False
        for xp in ('//button[contains(., "Sign Up")]',
                   '//button[@type="submit"]'):
            try:
                btns = driver.find_elements(By.XPATH, xp)
                for b in btns:
                    if b.is_displayed():
                        b.click()
                        clicked = True
                        break
            except Exception:
                continue
            if clicked:
                break
        _emit("signup_submitting", {"clicked": clicked})
        time.sleep(5.0)
        _shot(driver, "03_submitted")

        # ── outcome: ip_blocked (GOOD) checked before bot_blocked (enemy) ────
        if _has(driver, IP_BLOCK_MARKERS):
            result["outcome"] = "ip_blocked"
            _emit("signup_ip_blocked",
                  {"note": "PAST the gate — rotate home IP (Mullvad) and retry"})
            _shot(driver, "04_ip_blocked")
            return result
        if _has(driver, BOT_BLOCK_MARKERS):
            result["outcome"] = "bot_blocked"
            _emit("signup_bot_blocked", {"at": "submit"})
            _shot(driver, "04_bot_blocked")
            return result

        # wait for verify/OTP or success redirect
        deadline = time.time() + 45
        reached_verify = False
        while time.time() < deadline:
            url = driver.current_url or ""
            if any(m in url for m in SUCCESS_URL_MARKERS):
                result["outcome"] = "created"
                result["storage_state"] = _export_storage(driver)
                _emit("signup_created", {"at": "post_submit"})
                _shot(driver, "05_created")
                return result
            if _has(driver, IP_BLOCK_MARKERS):
                result["outcome"] = "ip_blocked"
                _emit("signup_ip_blocked", {"note": "rotate home IP"})
                _shot(driver, "04_ip_blocked")
                return result
            if _has(driver, BOT_BLOCK_MARKERS):
                result["outcome"] = "bot_blocked"
                _emit("signup_bot_blocked", {"at": "wait"})
                _shot(driver, "04_bot_blocked")
                return result
            if _has(driver, VERIFY_MARKERS):
                reached_verify = True
                break
            time.sleep(1.5)
        if not reached_verify:
            result["outcome"] = "failed"
            _emit("signup_no_verify", {})
            _shot(driver, "04_no_verify")
            return result

        # ── OTP ─────────────────────────────────────────────────────────────
        _emit("otp_waiting", {})
        _shot(driver, "05_otp_step")
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
                        if any(m in (driver.current_url or "")
                               for m in SUCCESS_URL_MARKERS):
                            result["outcome"] = "created"
                            result["storage_state"] = _export_storage(driver)
                            _emit("signup_created", {"at": "otp"})
                            _shot(driver, "06_created")
                            return result
            time.sleep(3.0)
        result["outcome"] = "otp_timeout"
        _shot(driver, "06_otp_timeout")
        return result
    except Exception as exc:  # noqa: BLE001 — surface as outcome
        _emit("signup_error", {"error": f"{type(exc).__name__}: {exc}"[:200]})
        return result
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass


def _enter_otp(driver: Any, code: str) -> bool:
    from selenium.webdriver.common.by import By
    digits = re.sub(r"\D", "", code)
    if not digits:
        return False
    try:
        boxes = [e for e in driver.find_elements(By.CSS_SELECTOR, OTP_DIGIT_BOXES)
                 if e.is_displayed()]
    except Exception:
        boxes = []
    try:
        if boxes and len(boxes) >= 4:
            for i, ch in enumerate(digits[:len(boxes)]):
                boxes[i].send_keys(ch)
                time.sleep(0.1)
        elif boxes:
            boxes[0].click()
            boxes[0].send_keys(digits)
        else:
            return False
        time.sleep(1.0)
        return True
    except Exception:
        return False


def _export_storage(driver: Any) -> dict[str, Any]:
    """Selenium cookies → Playwright storage_state shape (cookies only)."""
    try:
        cookies = driver.get_cookies()
    except Exception:
        cookies = []
    out = []
    for c in cookies:
        ss = str(c.get("sameSite", "Lax")).capitalize()
        if ss not in ("Strict", "Lax", "None"):
            ss = "Lax"
        out.append({
            "name": c.get("name", ""), "value": c.get("value", ""),
            "domain": c.get("domain", ""), "path": c.get("path", "/"),
            "httpOnly": bool(c.get("httpOnly", False)),
            "secure": bool(c.get("secure", False)), "sameSite": ss,
            "expires": c.get("expiry", -1),
        })
    return {"cookies": out, "origins": []}
