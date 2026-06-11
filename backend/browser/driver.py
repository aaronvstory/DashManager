"""Playwright plumbing shared by session, orders, and chat drivers.

Ported from the proven ddtr app: stealth launch args, storage-state-first
session replay (cookies-only fallback), Cloudflare wait-and-reload, and
best-effort screenshots. Session files here are plain JSON — the old app's
encryption layer is intentionally dropped.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from playwright.async_api import Browser, BrowserContext, Page, Playwright

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


async def launch_browser(p: Playwright, headless: bool) -> Browser:
    return await p.chromium.launch(headless=headless, args=CHROMIUM_ARGS)


async def new_customer_context(
    browser: Browser,
    storage_state_path: str,
    cookies_path: str | None,
    viewport: tuple[int, int] = (1400, 900),
) -> BrowserContext:
    """Replay a saved customer session.

    Full storage_state (cookies + localStorage) is preferred; a cookies-only
    context is the legacy fallback. Raises SessionExpiredError when neither
    file is usable so callers handle it like any other dead session.
    """
    vp = {"width": viewport[0], "height": viewport[1]}
    if storage_state_path and Path(storage_state_path).exists():
        try:
            return await browser.new_context(
                viewport=vp, user_agent=UA, storage_state=storage_state_path)
        except Exception:
            # Corrupted/truncated storage_state (e.g. capture killed
            # mid-write) — treat like any dead session: try cookies next.
            pass
    if not cookies_path or not Path(cookies_path).exists():
        raise SessionExpiredError(
            "no usable session files (missing storage state and cookies)")
    try:
        cookies = json.loads(Path(cookies_path).read_text("utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise SessionExpiredError(
            f"corrupted cookie file {cookies_path}: {exc}") from exc
    if not cookies:
        raise SessionExpiredError(f"empty cookie file: {cookies_path}")
    ctx = await browser.new_context(viewport=vp, user_agent=UA)
    try:
        await ctx.add_cookies(cookies)
    except Exception as exc:
        await ctx.close()
        raise SessionExpiredError(
            f"cookie replay rejected ({cookies_path}): {exc}") from exc
    return ctx


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
