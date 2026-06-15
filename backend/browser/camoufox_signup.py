"""DoorDash signup via **Camoufox** (stealth-hardened Firefox) — the third,
evidence-aligned attempt at automated account creation.

WHY FIREFOX, WHY NOW (see memory [[doordash-signup-bot-detection]]):
DoorDash's signup gate is **PerimeterX (HUMAN Security)**, NOT Cloudflare — its
primary signal is **behavioral biometrics** (mouse/input telemetry) on top of
TLS/JA3 + fingerprint scoring. Every prior automated attempt was Chromium
(Playwright / SeleniumBase UC / CDP Mode) and got the "Something went wrong"
PerimeterX reject on submit. The ONE proven success was the user's real
**Firefox**. Camoufox is a stealth-hardened Firefox built to mimic real Firefox
at the engine/TLS/fingerprint level — so it matches the one thing that worked.

This driver applies the expert-recommended PerimeterX counters together:
  1. **Firefox engine** (Camoufox) — clean Firefox TLS/JA3 + navigator surface.
  2. **humanize=True** — real cursor-movement telemetry (PerimeterX's PRIMARY
     signal); plus we add explicit pre-submit mouse moves.
  3. **Sticky single residential IP** (one proxy line, NOT a rotating gateway —
     PerimeterX wants one dedicated IP per account, gotcha #E1) + geoip=True so
     timezone/locale/WebRTC match the exit IP.
  4. **Session warmup** — browse the homepage first to earn a good _px3 trust
     cookie BEFORE hitting /signup (cold submits score low).
  5. **Human-paced fill** via Playwright type() with per-key delay.

Camoufox is Playwright-async, so this module is async (unlike the sync
SeleniumBase drivers). Return shape MATCHES uc_signup.signup_via_uc /
cdp_signup.signup_via_cdp so account_creator can swap drivers untouched:

    {"outcome": "created"|"bot_blocked"|"otp_timeout"|"failed",
     "storage_state": {...}|None, "phone10": "..."}
"""
from __future__ import annotations

import asyncio
import random
import re
from typing import Any, Callable

from backend.browser.uc_signup import (BOT_BLOCK_MARKERS, SIGNUP_URL,
                                       SUCCESS_URL_MARKERS, VERIFY_MARKERS,
                                       normalize_phone)

HOMEPAGE = "https://www.doordash.com/"

# Playwright CSS selectors (same stable autocomplete attrs as the other drivers).
SEL_FIRST = 'input[autocomplete="given-name"]'
SEL_LAST = 'input[autocomplete="family-name"]'
SEL_EMAIL = 'input[autocomplete="email"]'
SEL_PHONE = 'input[autocomplete="tel"]'
SEL_PASSWORD = 'input[autocomplete="new-password"]'
SEL_SUBMIT = 'button:has-text("Sign Up")'
SEL_SUBMIT_FALLBACK = 'button[type="submit"]'

OTP_DIGIT_BOXES = ('input[inputmode="numeric"], input[maxlength="1"], '
                   'input[type="number"]')

Emit = Callable[[str, dict], None]


def resolve_sticky_proxy() -> dict[str, str] | None:
    """One STICKY residential proxy as a Playwright proxy dict (or None).

    PerimeterX wants a single dedicated IP per account (rotation hurts trust —
    gotcha #E1), so we take the FIRST working-proxies.txt line and keep it for
    the whole signup. Returns {"server","username","password"} (Camoufox/
    Playwright shape) — NOT the inline user:pass@host form. Creds live in the
    gitignored working-proxies.txt; never logged.
    """
    try:
        from pathlib import Path
        root = Path(__file__).resolve().parents[2]
        f = root / "working-proxies.txt"
        if not f.exists():
            return None
        for ln in f.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln or ln.startswith("#"):
                continue
            raw = ln.split("://", 1)
            scheme = raw[0] if len(raw) == 2 else "http"
            body = raw[-1]
            parts = body.split(":", 3)
            if len(parts) == 4:
                host, port, user, pwd = parts
                return {"server": f"{scheme}://{host}:{port}",
                        "username": user, "password": pwd}
    except Exception:
        pass
    return None


async def _human_type(page: Any, selector: str, value: str) -> bool:
    """Click + type at human speed (per-key jittered delay)."""
    try:
        el = page.locator(selector).first
        await el.wait_for(state="visible", timeout=8000)
        await el.click()
        await asyncio.sleep(random.uniform(0.25, 0.6))
        # Playwright type() with delay emits real per-key events; jitter on top.
        for ch in value:
            await el.type(ch, delay=random.uniform(40, 130))
        await asyncio.sleep(random.uniform(0.3, 0.7))
        return True
    except Exception:
        return False


async def _wander_mouse(page: Any) -> None:
    """Move the cursor along a few human-ish points — PerimeterX scores mouse
    telemetry as a primary signal, so a cold page with zero movement is a tell.
    (Camoufox humanize=True also moves the cursor on clicks; this adds idle
    movement before submit.)"""
    try:
        w = await page.evaluate("() => window.innerWidth") or 1200
        h = await page.evaluate("() => window.innerHeight") or 800
        for _ in range(random.randint(3, 6)):
            x = random.randint(int(w * 0.2), int(w * 0.8))
            y = random.randint(int(h * 0.2), int(h * 0.8))
            await page.mouse.move(x, y, steps=random.randint(8, 20))
            await asyncio.sleep(random.uniform(0.15, 0.5))
    except Exception:
        pass


async def _body_text(page: Any) -> str:
    try:
        return (await page.evaluate(
            "() => document.body ? document.body.innerText : ''") or "").lower()
    except Exception:
        return ""


def _norm_same_site(raw: Any) -> str:
    """Playwright cookie sameSite → {Strict, Lax, None} (storage_state shape)."""
    import enum
    if isinstance(raw, enum.Enum):
        raw = raw.value
    ss = str(raw or "Lax").capitalize()
    return ss if ss in ("Strict", "Lax", "None") else "Lax"


async def signup_via_camoufox(
    identity: dict[str, Any], *,
    poll_otp: Callable[[], Any],
    headless: bool = False,
    emit: Emit | None = None,
    otp_total_wait_s: float = 180.0,
    warmup_s: float = 6.0,
    screenshot_dir: str | None = None,
) -> dict[str, Any]:
    """Create one DoorDash account via Camoufox (Firefox stealth). Async.

    ``poll_otp`` is an async-or-sync callable returning the current api.cc code
    ('' if not arrived). On "created" the returned storage_state is a
    Playwright-compatible cookies dict for the per-customer profile machinery.
    """
    from camoufox.async_api import AsyncCamoufox

    def _emit(t: str, d: dict | None = None) -> None:
        if emit:
            try:
                emit(t, d or {})
            except Exception:
                pass

    async def _poll() -> str:
        res = poll_otp()
        if asyncio.iscoroutine(res):
            res = await res
        return res or ""

    phone10 = normalize_phone(identity.get("phone_number", ""))
    result: dict[str, Any] = {"outcome": "failed", "storage_state": None,
                              "phone10": phone10}
    proxy = resolve_sticky_proxy()
    _emit("signup_proxy", {"using_proxy": bool(proxy)})

    shot_n = [0]

    async def _shot(page: Any, tag: str) -> None:
        if not screenshot_dir:
            return
        try:
            import os
            os.makedirs(screenshot_dir, exist_ok=True)
            shot_n[0] += 1
            await page.screenshot(
                path=os.path.join(screenshot_dir,
                                  f"cf_{shot_n[0]:02d}_{tag}.png"))
        except Exception:
            pass

    # geoip=True only works when a proxy is set (it derives geo from the exit IP).
    cam_kwargs: dict[str, Any] = {"headless": headless, "humanize": True,
                                  "os": "windows"}
    if proxy:
        cam_kwargs["proxy"] = proxy
        cam_kwargs["geoip"] = True

    try:
        async with AsyncCamoufox(**cam_kwargs) as browser:
            page = await browser.new_page()

            # ── 1) WARMUP: earn a _px3 trust cookie before touching /signup ──
            _emit("signup_warmup", {})
            try:
                await page.goto(HOMEPAGE, wait_until="domcontentloaded",
                                timeout=45000)
            except Exception:
                pass
            await asyncio.sleep(warmup_s)
            await _wander_mouse(page)
            # a little scroll, like a real visitor sizing up the page
            try:
                await page.mouse.wheel(0, random.randint(300, 900))
                await asyncio.sleep(random.uniform(1.0, 2.0))
                await page.mouse.wheel(0, random.randint(200, 600))
            except Exception:
                pass
            await _shot(page, "warmup")

            # ── 2) Go to signup ──
            await page.goto(SIGNUP_URL, wait_until="domcontentloaded",
                            timeout=45000)
            await asyncio.sleep(random.uniform(2.0, 3.5))
            await _wander_mouse(page)
            _emit("signup_form_open", {})
            await _shot(page, "form_open")

            # bot block can fire on load
            load_body = await _body_text(page)
            if any(m in load_body for m in BOT_BLOCK_MARKERS):
                result["outcome"] = "bot_blocked"
                _emit("signup_bot_blocked", {"at": "load"})
                await _shot(page, "bot_blocked_load")
                return result

            # ── 3) Fill the form, human-paced ──
            await _human_type(page, SEL_FIRST, identity.get("first_name", ""))
            await _human_type(page, SEL_LAST, identity.get("last_name", ""))
            await _human_type(page, SEL_EMAIL, identity.get("email", ""))
            await _human_type(page, SEL_PHONE, phone10)
            await _human_type(page, SEL_PASSWORD, identity.get("password", ""))
            _emit("signup_form_filled", {})
            await _shot(page, "filled")

            # ── 4) Human dwell + mouse wander, THEN submit ──
            await _wander_mouse(page)
            await asyncio.sleep(random.uniform(0.8, 1.8))
            try:
                btn = page.locator(SEL_SUBMIT).first
                if await btn.count() == 0:
                    btn = page.locator(SEL_SUBMIT_FALLBACK).first
                await btn.click()
            except Exception:
                try:
                    await page.locator(SEL_SUBMIT_FALLBACK).first.click()
                except Exception:
                    result["outcome"] = "failed"
                    _emit("signup_no_submit", {})
                    return result
            _emit("signup_submitting", {})
            await asyncio.sleep(5.0)
            await _shot(page, "after_submit")

            # ── 5) Outcome: bot-block? verify step? already in? ──
            body = await _body_text(page)
            if any(m in body for m in BOT_BLOCK_MARKERS):
                result["outcome"] = "bot_blocked"
                _emit("signup_bot_blocked", {"at": "submit"})
                await _shot(page, "bot_blocked_submit")
                return result

            # wait for the verify/OTP step or a success redirect
            deadline = asyncio.get_event_loop().time() + 40
            reached_verify = False
            while asyncio.get_event_loop().time() < deadline:
                url = page.url
                if any(m in url for m in SUCCESS_URL_MARKERS):
                    result["outcome"] = "created"
                    result["storage_state"] = await _export_storage(page)
                    _emit("signup_created", {"at": "post_submit"})
                    await _shot(page, "created")
                    return result
                verify_body = await _body_text(page)
                if any(m in verify_body for m in VERIFY_MARKERS):
                    reached_verify = True
                    break
                await asyncio.sleep(1.5)
            if not reached_verify:
                result["outcome"] = "failed"
                _emit("signup_no_verify", {})
                await _shot(page, "no_verify")
                return result

            # ── 6) OTP step ──
            _emit("otp_waiting", {})
            await _shot(page, "verify")
            started = asyncio.get_event_loop().time()
            tried: set[str] = set()
            while asyncio.get_event_loop().time() - started < otp_total_wait_s:
                code = await _poll()
                if code and code not in tried:
                    tried.add(code)
                    _emit("otp_received", {"code": code})
                    if await _enter_otp(page, code):
                        for _ in range(6):
                            await asyncio.sleep(2.0)
                            if any(m in page.url for m in SUCCESS_URL_MARKERS):
                                result["outcome"] = "created"
                                result["storage_state"] = \
                                    await _export_storage(page)
                                _emit("signup_created", {"at": "otp"})
                                await _shot(page, "created")
                                return result
                await asyncio.sleep(3.0)
            result["outcome"] = "otp_timeout"
            await _shot(page, "otp_timeout")
            return result
    except Exception as exc:  # never raise — surface as outcome
        msg = f"{type(exc).__name__}: {exc}"[:200]
        if proxy and proxy.get("password"):
            msg = msg.replace(proxy["password"], "<redacted>")
        _emit("signup_error", {"error": msg})
        return result


async def _enter_otp(page: Any, code: str) -> bool:
    """Enter the OTP into the verify step (6 split boxes or one field)."""
    digits = re.sub(r"\D", "", code)
    if not digits:
        return False
    try:
        boxes = page.locator(OTP_DIGIT_BOXES)
        n = await boxes.count()
        if n >= 4:
            await boxes.nth(0).click()
            await boxes.nth(0).type(digits, delay=80)
        elif n >= 1:
            await boxes.nth(0).click()
            await boxes.nth(0).type(digits, delay=80)
        else:
            return False
        await asyncio.sleep(1.0)
        return True
    except Exception:
        return False


async def _export_storage(page: Any) -> dict[str, Any]:
    """Export cookies in Playwright storage_state shape from the live context."""
    try:
        ctx = page.context
        state = await ctx.storage_state()
        # storage_state() already returns the right shape; normalize sameSite.
        for ck in state.get("cookies", []):
            ck["sameSite"] = _norm_same_site(ck.get("sameSite"))
        return state
    except Exception:
        return {"cookies": [], "origins": []}
