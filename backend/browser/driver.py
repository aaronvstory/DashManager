"""Playwright plumbing shared by session, orders, and chat drivers.

Each customer gets its own persistent Chromium profile (own user-data-dir on
disk: cookies, cache, localStorage). This gives true per-account isolation —
several customers can be logged in and run concurrently without cross-
contamination — and the session survives restarts (no replay needed). A
portable storage_state JSON is still exported as a backup so a profile can be
reseeded if its dir is lost.

Ported from the proven ddtr app: stealth launch args, Cloudflare
wait-and-reload, best-effort screenshots. No encryption layer (intentional).
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path

from playwright.async_api import BrowserContext, Page, Playwright

from backend import config
from backend.browser.pacing import human_pause
from backend.browser.selectors import (
    CHROMIUM_ARGS,
    CLOUDFLARE_B_MAX_WAIT_S,
    CLOUDFLARE_B_POLL_S,
    CLOUDFLARE_B_TEXTS,
    CLOUDFLARE_TEXT,
    CLOUDFLARE_WAIT_S,
    UA,
)

_FILENAME_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")

# Chromium locks a profile's user-data-dir while a context is open, so two
# operations on the SAME customer (e.g. a run iterating them + a manual
# test-session/relogin) would crash. One lock per customer serializes access;
# different customers still run fully concurrently.
_profile_locks: dict[int, asyncio.Lock] = {}


def profile_lock(customer_id: int) -> asyncio.Lock:
    lock = _profile_locks.get(customer_id)
    if lock is None:
        lock = _profile_locks[customer_id] = asyncio.Lock()
    return lock


class SessionExpiredError(Exception):
    """The saved session no longer authenticates (redirect to login/identity)."""


def profile_dir(customer_id: int) -> Path:
    """The on-disk Chromium user-data-dir for one customer (gitignored)."""
    return config.PROFILES_DIR / str(customer_id)


def profile_exists(customer_id: int) -> bool:
    d = profile_dir(customer_id)
    # A non-empty profile dir means Chromium has written a session here.
    return d.exists() and any(d.iterdir())


def remove_profile(customer_id: int) -> None:
    shutil.rmtree(profile_dir(customer_id), ignore_errors=True)


async def open_customer_profile(
    p: Playwright,
    customer_id: int,
    headless: bool,
    *,
    seed_storage_state: str | None = None,
    viewport: tuple[int, int] = (1200, 720),
) -> BrowserContext:
    """Open the customer's persistent profile as an isolated context.

    Returns a BrowserContext (which, for a persistent context, owns the whole
    browser — close THE CONTEXT to clean up). When the profile dir is empty
    and a `seed_storage_state` file is given, its cookies are injected so a
    portable backup can repopulate a fresh profile.

    Default size is 1200x720 (not 1400x900): when the user resizes the headed
    window to fit their screen, a taller window cuts off at the bottom. The OS
    window is sized to the viewport (+chrome) via --window-size.
    """
    d = profile_dir(customer_id)
    d.mkdir(parents=True, exist_ok=True)
    fresh = not any(d.iterdir())
    win_args = [*CHROMIUM_ARGS,
                f"--window-size={viewport[0]},{viewport[1] + 40}"]
    ctx = await p.chromium.launch_persistent_context(
        str(d), headless=headless, args=win_args, user_agent=UA,
        viewport={"width": viewport[0], "height": viewport[1]})
    if fresh and seed_storage_state and Path(seed_storage_state).exists():
        try:
            state = json.loads(Path(seed_storage_state).read_text("utf-8"))
            cookies = state.get("cookies", [])
            if cookies:
                await ctx.add_cookies(cookies)
        except Exception:
            pass  # backup seed is best-effort; a real login still works
    return ctx


@asynccontextmanager
async def customer_profile(
    p: Playwright, customer_id: int, headless: bool, *,
    seed_storage_state: str | None = None,
    viewport: tuple[int, int] = (1400, 900),
):
    """Open a customer profile under their per-customer lock; close on exit.

    Holding the lock for the whole open→use→close span prevents two operations
    on the same customer from fighting over Chromium's user-data-dir lock.
    Yields the BrowserContext.
    """
    async with profile_lock(customer_id):
        ctx = await open_customer_profile(
            p, customer_id, headless, seed_storage_state=seed_storage_state,
            viewport=viewport)
        try:
            yield ctx
        finally:
            await ctx.close()


async def export_storage_state(ctx: BrowserContext, customer_id: int) -> str:
    """Write a portable storage_state backup for the customer; returns path."""
    config.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    path = config.SESSIONS_DIR / f"{customer_id}_storage.json"
    try:
        await ctx.storage_state(path=str(path))
        return str(path)
    except Exception:
        return ""


def classify_cloudflare(text: str) -> str:
    """Pure: which Cloudflare gate (if any) a page's body text shows.

    Returns "" (none), "a" (soft "Verifying you are human" — clears on
    wait+reload), or "b" (the harder Turnstile "security verification" gate
    that does NOT clear on a reload). Variant B is checked first because its
    page can also contain generic verifying copy.
    """
    lo = (text or "").lower()
    if any(t in lo for t in CLOUDFLARE_B_TEXTS):
        return "b"
    if CLOUDFLARE_TEXT.lower() in lo:
        return "a"
    return ""


async def _page_text(page: Page) -> str:
    try:
        return await page.evaluate(
            "() => document.body ? document.body.innerText : ''") or ""
    except Exception:
        # Page mid-navigation / context destroyed — caller treats as no gate.
        return ""


async def handle_cloudflare(page: Page) -> bool:
    """Clear a Cloudflare gate if one is showing; True if a gate was present.

    Variant A: wait + reload (harvest-proven).
    Variant B: a harder Turnstile gate. Reloading does NOT help and can make
    it worse, so we instead wait it out (Turnstile auto-solves in 30-60s),
    polling the body text until the challenge copy is gone. If it persists,
    try ONE fresh navigation to the same URL (a clean nav often passes where a
    reload doesn't). If it STILL persists, return True and leave the gate up —
    the caller decides whether to relogin or surface to the user.
    """
    variant = classify_cloudflare(await _page_text(page))
    if variant == "":
        return False
    if variant == "a":
        await asyncio.sleep(CLOUDFLARE_WAIT_S)
        try:
            await page.reload(wait_until="domcontentloaded")
            await asyncio.sleep(3)
        except Exception:
            pass
        return True

    # ── Variant B: wait it out, don't hammer reload ──
    target_url = page.url
    deadline = time.monotonic() + CLOUDFLARE_B_MAX_WAIT_S
    while time.monotonic() < deadline:
        await asyncio.sleep(CLOUDFLARE_B_POLL_S)
        variant = classify_cloudflare(await _page_text(page))
        if variant == "":
            return True  # fully cleared (no gate of any kind)
        if variant == "a":
            # B downgraded to the soft A gate — finish it with A's wait+reload.
            await asyncio.sleep(CLOUDFLARE_WAIT_S)
            try:
                await page.reload(wait_until="domcontentloaded")
                await asyncio.sleep(3)
            except Exception:
                pass
            return True
    # Still stuck on B — try exactly one fresh navigation (not a reload).
    try:
        await page.goto(target_url, wait_until="domcontentloaded")
    except Exception:
        return True
    await human_pause(2.0, 4.0)  # let the fresh page settle before re-reading
    # Whether it cleared or not, we're done trying here; caller handles a
    # lingering gate (relogin / manual surface).
    return True


async def screenshot(page: Page, name: str) -> str:
    """Best-effort viewport screenshot; never raises (used on error paths).

    Returns the saved path, or "" when the capture itself failed.
    """
    safe = _FILENAME_UNSAFE.sub("_", name).strip("_") or "screenshot"
    path = config.SCREENSHOTS_DIR / f"{safe}.png"
    try:
        await page.screenshot(path=str(path), full_page=False)
        return str(path)
    except Exception:
        return ""


async def proof_screenshot(page: Page, bucket: str, customer_id: int,
                           name: str, *, full_page: bool = True) -> str:
    """Capture a FULL-PAGE proof screenshot into data/screenshots/<bucket>/.

    Used for the visual audit trail (orders page per customer, receipt per
    order, claim/chat outcome). Returns the saved path ("" on failure). Never
    raises — proof is best-effort and must not break the run.
    """
    safe = _FILENAME_UNSAFE.sub("_", name).strip("_") or "shot"
    out_dir = config.SCREENSHOTS_DIR / bucket
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"c{customer_id}_{safe}.png"
        await page.screenshot(path=str(path), full_page=full_page)
        return str(path)
    except Exception:
        return ""
