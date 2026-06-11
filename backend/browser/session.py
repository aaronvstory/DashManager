"""Manual login capture plus identity scrape from the edit-profile page.

The browser is always headed here — a human completes the DoorDash login
(password/2FA) by hand; we only watch the URL and persist the session. The
identity scrape afterwards is best-effort: a captured login must never be
lost because profile parsing hiccupped, so any failure returns a partial or
empty IdentityProfile. The caller renames the pending_* files after creating
the customer row.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

from playwright.async_api import Page, async_playwright

from backend import config, db
from backend.browser.selectors import CHROMIUM_ARGS, EDIT_PROFILE_URL, LOGIN_URL, UA
from backend.models import IdentityProfile

EmitFn = Callable[[str, dict[str, Any]], None]

PENDING_STORAGE = "pending_storage.json"
PENDING_COOKIES = "pending_cookies.json"


async def login_and_capture(
    emit: EmitFn | None = None,
) -> tuple[str, str, IdentityProfile]:
    """Open a headed browser, wait for manual login, save session + identity.

    Returns (storage_state_path, cookies_path, profile). Session files are
    plain JSON written to config.SESSIONS_DIR under pending_* names.
    """
    storage_path = config.SESSIONS_DIR / PENDING_STORAGE
    cookies_path = config.SESSIONS_DIR / PENDING_COOKIES
    profile = IdentityProfile()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=CHROMIUM_ARGS)
        try:
            ctx = await browser.new_context(
                viewport={"width": 1400, "height": 900}, user_agent=UA)
            page = await ctx.new_page()
            await page.goto(LOGIN_URL, wait_until="domcontentloaded")
            if emit:
                emit("login_waiting", {})
            # Harvested predicate: any doordash.com URL that is neither the
            # login page nor the identity.doordash.com flow means logged in.
            await page.wait_for_url(
                lambda u: ("doordash.com" in u and "login" not in u
                           and "identity" not in u),
                timeout=300_000)
            await asyncio.sleep(3)  # let post-login cookies settle (harvest)

            await ctx.storage_state(path=str(storage_path))
            cookies = await ctx.cookies()
            cookies_path.write_text(json.dumps(cookies, indent=2), "utf-8")

            try:
                profile = await _capture_identity(page)
            except Exception as exc:
                if emit:
                    emit("log", {"message": f"identity capture failed: {exc}"})
        finally:
            await browser.close()

    return str(storage_path), str(cookies_path), profile


async def _capture_identity(page: Page) -> IdentityProfile:
    try:
        cfg = await db.get_setting("identity_capture")
    except Exception:  # DB not initialized — defaults still work
        cfg = config.DEFAULT_SETTINGS["identity_capture"]
    labels: dict[str, str] = cfg["labels"]

    await page.goto(cfg.get("url", EDIT_PROFILE_URL),
                    wait_until="domcontentloaded")
    await asyncio.sleep(2)  # form values hydrate client-side after DOM load

    values = {field: await _read_labeled_input(page, label)
              for field, label in labels.items()}
    if not any(values.values()):
        # Labels not wired at all — fall back to visible inputs in form order.
        values = await _read_inputs_in_form_order(page, list(labels))
    return IdentityProfile(**values)


async def _read_labeled_input(page: Page, label: str) -> str:
    try:
        return await page.get_by_label(label).first.input_value(timeout=2000)
    except Exception:
        pass
    try:
        # Label text not associated via for=/aria — take the nearest input
        # that follows the visible label text in document order.
        loc = page.locator(
            f'xpath=//*[(self::label or self::span or self::p or self::div)'
            f' and normalize-space(text())="{label}"]/following::input[1]')
        return await loc.first.input_value(timeout=2000)
    except Exception:
        return ""


async def _read_inputs_in_form_order(
    page: Page, fields: list[str],
) -> dict[str, str]:
    try:
        found: list[str] = await page.evaluate(
            """() => [...document.querySelectorAll(
                   "input[type='text'], input[type='email'],"
                   + " input[type='tel'], input:not([type])")]
               .filter(i => i.offsetParent !== null)
               .map(i => i.value || '')""")
    except Exception:
        found = []
    return {f: (found[i] if i < len(found) else "")
            for i, f in enumerate(fields)}
