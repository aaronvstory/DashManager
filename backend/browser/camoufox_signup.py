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
# The old working scripts reached signup via the LOGIN page, then clicked
# "Sign Up" — never a cold /signup land (which scores as low-trust).
LOGIN_URL = "https://www.doordash.com/consumer/login/"

# A modest, watchable window so the headed run doesn't fill the user's monitor
# (the supervised refund/signup sessions standardize on ~1200x720).
WINDOW_SIZE = (1200, 720)
# Per-navigation Playwright timeout, plus an OUTER asyncio timeout on warmup so a
# dead/slow residential proxy can never hang the whole run (it stalled before).
GOTO_TIMEOUT_MS = 45000
WARMUP_TIMEOUT_S = 25.0
# Per-proxy launch+warmup retries — the first bring-up through a residential
# proxy can throw a transient TargetClosedError; retry the SAME proxy (sticky IP)
# before falling through to the next candidate.
WARMUP_ATTEMPTS = 2

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


def iter_proxies() -> list[dict[str, str]]:
    """All working-proxies.txt lines as Playwright proxy dicts, in file order.

    PerimeterX wants ONE sticky residential IP per account (rotation hurts
    trust — gotcha #E1), so the signup keeps a SINGLE proxy for the whole run.
    But a dead/slow proxy must not hang the run, so we return the ordered list
    and the driver warms up against each until one actually loads the homepage,
    then stays sticky on THAT one. Each dict is {"server","username","password"}
    (Camoufox/Playwright shape). Creds live in the gitignored working-proxies.txt;
    never logged.
    """
    out: list[dict[str, str]] = []
    try:
        from pathlib import Path
        root = Path(__file__).resolve().parents[2]
        f = root / "working-proxies.txt"
        if not f.exists():
            return out
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
                out.append({"server": f"{scheme}://{host}:{port}",
                            "username": user, "password": pwd})
    except Exception:
        pass
    return out


def resolve_sticky_proxy() -> dict[str, str] | None:
    """First working proxy (back-compat shim for tests). Prefer iter_proxies."""
    proxies = iter_proxies()
    return proxies[0] if proxies else None


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
    force_direct: bool = False,
    target_os: str = "windows",
) -> dict[str, Any]:
    """Create one DoorDash account via Camoufox (Firefox stealth). Async.

    ``poll_otp`` is an async-or-sync callable returning the current api.cc code
    ('' if not arrived). On "created" the returned storage_state is a
    Playwright-compatible cookies dict for the per-customer profile machinery.

    ``force_direct`` skips ALL proxies and runs on the home IP (what every old
    working script did; the LightningProxies gateway was hurting). ``target_os``
    picks the consistent desktop fingerprint OS ("windows"/"macos"/"linux") —
    Camoufox has no mobile fingerprint, so desktop-consistent is the design.
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

    # ── The post-warmup flow (form → submit → outcome → OTP), run on the page
    #    of whichever proxy successfully warmed up. Mutates `result`. ──────────
    async def _run_flow(page: Any) -> dict[str, Any]:
        # ── 2) Reach signup with a warm session: visit the consumer LOGIN page
        #    first (earns trust cookies), settle, THEN navigate directly to the
        #    known-correct CONSUMER signup URL. We do NOT click a generic
        #    "Sign Up" link — the login page's "Sign Up" goes to the DASHER
        #    (driver) signup at dasher.doordash.com, the wrong form. Going
        #    straight to SIGNUP_URL after the login visit avoids that trap while
        #    still avoiding a stone-cold /signup land. ──
        try:
            await page.goto(LOGIN_URL, wait_until="domcontentloaded",
                            timeout=GOTO_TIMEOUT_MS)
            await asyncio.sleep(random.uniform(1.5, 2.8))
            await _wander_mouse(page)
        except Exception:
            pass
        await page.goto(SIGNUP_URL, wait_until="domcontentloaded",
                        timeout=GOTO_TIMEOUT_MS)
        await asyncio.sleep(random.uniform(2.0, 3.5))
        await _wander_mouse(page)
        # Guard: never proceed on the dasher signup form (wrong target).
        if "dasher.doordash.com" in page.url:
            await page.goto(SIGNUP_URL, wait_until="domcontentloaded",
                            timeout=GOTO_TIMEOUT_MS)
            await asyncio.sleep(1.5)
        _emit("signup_form_open", {"url": page.url[:80]})
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
        # Try the primary submit, then the fallback selector — but only retry the
        # fallback if it's a DIFFERENT locator than the one that just failed (no
        # redundant double-click of the same button).
        used_primary = False
        try:
            btn = page.locator(SEL_SUBMIT).first
            if await btn.count() > 0:
                used_primary = True
            else:
                btn = page.locator(SEL_SUBMIT_FALLBACK).first
            await btn.click()
        except Exception:
            clicked = False
            if used_primary:  # primary was the one that failed — try fallback
                try:
                    await page.locator(SEL_SUBMIT_FALLBACK).first.click()
                    clicked = True
                except Exception:
                    pass
            if not clicked:
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
        deadline = asyncio.get_running_loop().time() + 40
        reached_verify = False
        while asyncio.get_running_loop().time() < deadline:
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
        started = asyncio.get_running_loop().time()
        tried: set[str] = set()
        while asyncio.get_running_loop().time() - started < otp_total_wait_s:
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

    # ── WARMUP with a HARD timeout: load the homepage to earn a _px3 trust
    #    cookie before /signup. Returns True only if the homepage actually
    #    loaded (so a dead/slow proxy can never hang the run — we move on). ──
    async def _warmup(page: Any) -> bool:
        _emit("signup_warmup", {})
        # Use Playwright's OWN navigation timeout (fails cleanly) — do NOT wrap
        # in asyncio.wait_for: cancelling page.goto mid-navigation corrupts the
        # Camoufox page and the NEXT goto throws TargetClosedError (the live
        # crash). "commit" fires as soon as the response starts (enough to earn
        # the _px3 cookie) without waiting for the heavy homepage to fully parse.
        try:
            await page.goto(HOMEPAGE, wait_until="commit",
                            timeout=GOTO_TIMEOUT_MS)
        except Exception:
            return False
        await asyncio.sleep(warmup_s)
        await _wander_mouse(page)
        try:  # a little scroll, like a real visitor sizing up the page
            await page.mouse.wheel(0, random.randint(300, 900))
            await asyncio.sleep(random.uniform(1.0, 2.0))
            await page.mouse.wheel(0, random.randint(200, 600))
        except Exception:
            pass
        await _shot(page, "warmup")
        return True

    # Sticky-IP with liveness fallback: try each proxy in file order until one
    # actually loads the homepage, then stay on it for the WHOLE signup (never
    # rotate mid-account — PerimeterX gotcha #E1). Direct (None) is the last
    # resort, logged loudly. geoip=True only when a proxy is set.
    if force_direct:
        candidates: list[dict[str, str] | None] = [None]  # home IP only
    else:
        candidates = list(iter_proxies())
        candidates.append(None)  # direct as final fallback

    for proxy in candidates:
        cam_kwargs: dict[str, Any] = {
            "headless": headless, "humanize": True, "os": target_os,
            # Fixed modest window so it doesn't fill the user's monitor. (Camoufox
            # warns fixed sizes are slightly more fingerprintable, but an
            # un-watchable giant window is worse for a headed, supervised run.)
            "window": WINDOW_SIZE,
        }
        if proxy:
            cam_kwargs["proxy"] = proxy
            cam_kwargs["geoip"] = True
        server = proxy["server"] if proxy else "direct"
        _emit("signup_proxy", {"using_proxy": bool(proxy), "server": server})

        # The bring-up (launch + new_page + first goto through a residential
        # proxy) can throw a TRANSIENT TargetClosedError — the diagnostic proved
        # the exact config works, so a single close is flaky bring-up, not a dead
        # proxy. Retry the SAME proxy once (keeps the IP sticky) before moving on.
        warmed = False
        for attempt in range(WARMUP_ATTEMPTS):
            try:
                async with AsyncCamoufox(**cam_kwargs) as browser:
                    page = await browser.new_page()
                    if not await _warmup(page):
                        # clean "didn't load" — likely genuinely slow; don't
                        # waste a second launch on the same proxy.
                        break
                    warmed = True
                    return await _run_flow(page)
            except Exception as exc:  # transient bring-up close, or real error
                msg = f"{type(exc).__name__}: {exc}"[:200]
                if proxy and proxy.get("password"):
                    msg = msg.replace(proxy["password"], "<redacted>")
                _emit("signup_warmup_retry",
                      {"server": server, "attempt": attempt + 1, "error": msg})
                await asyncio.sleep(1.5)
                continue
        if not warmed:
            _emit("signup_proxy_dead", {"server": server})
            continue  # exhausted retries on this proxy — next candidate

    # Every candidate (all proxies + direct) failed to even warm up.
    result["outcome"] = "failed"
    _emit("signup_all_proxies_dead", {})
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
