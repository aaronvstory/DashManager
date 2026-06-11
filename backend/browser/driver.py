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
from pathlib import Path

from playwright.async_api import BrowserContext, Page, Playwright

from backend import config
from backend.browser.selectors import (
    CHROMIUM_ARGS,
    CLOUDFLARE_TEXT,
    CLOUDFLARE_WAIT_S,
    UA,
)

_FILENAME_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


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
    viewport: tuple[int, int] = (1400, 900),
) -> BrowserContext:
    """Open the customer's persistent profile as an isolated context.

    Returns a BrowserContext (which, for a persistent context, owns the whole
    browser — close THE CONTEXT to clean up). When the profile dir is empty
    and a `seed_storage_state` file is given, its cookies are injected so a
    portable backup can repopulate a fresh profile.
    """
    d = profile_dir(customer_id)
    d.mkdir(parents=True, exist_ok=True)
    fresh = not any(d.iterdir())
    ctx = await p.chromium.launch_persistent_context(
        str(d), headless=headless, args=CHROMIUM_ARGS, user_agent=UA,
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


async def export_storage_state(ctx: BrowserContext, customer_id: int) -> str:
    """Write a portable storage_state backup for the customer; returns path."""
    config.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    path = config.SESSIONS_DIR / f"{customer_id}_storage.json"
    try:
        await ctx.storage_state(path=str(path))
        return str(path)
    except Exception:
        return ""


async def handle_cloudflare(page: Page) -> bool:
    """Wait out the 'Verifying you are human' gate; True if it was present."""
    try:
        text = await page.evaluate(
            "() => document.body ? document.body.innerText : ''")
    except Exception:
        # Page mid-navigation / context destroyed — no gate we can act on.
        return False
    if CLOUDFLARE_TEXT not in text:
        return False
    await asyncio.sleep(CLOUDFLARE_WAIT_S)
    try:
        await page.reload(wait_until="domcontentloaded")
        await asyncio.sleep(3)
    except Exception:
        pass
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
