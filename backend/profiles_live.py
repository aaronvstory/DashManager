"""Keep customer Chromium profiles open (headed) and remember which are 'live'.

Each customer owns a persistent Chromium user-data-dir at ``data/profiles/{id}``
(see ``backend.browser.driver``). Because it's persistent, a login written once
survives on disk — reopening that profile later is instant and already
logged-in, no re-login, no Firefox containers. This module adds two things on
top of that:

  1. ``keep_open(ids, headless=False)`` — launch each customer's profile in its
     own headed window and HOLD them open until cancelled (Ctrl-C / process
     stop). Used right after /dash-create so the user's N accounts stay open.

  2. A tiny live-state file, ``data/open_profiles.json``, recording which
     customer ids the user wants kept open. Any session or skill can read it and
     reopen exactly those — so "keep them open for me" persists across chats and
     restarts.

Single-writer reality (important): one persistent user-data-dir = ONE Chromium
at a time (Chromium locks the dir). So a profile can't be open in two processes
at once. "Reuse the same open profiles for the refund check" therefore means:
the refund run uses the SAME persistent profile (already logged in) — if a
keep-open window for that id is currently running, stop it first (or let the run
adopt it), don't launch a second copy of the same id.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from backend import config
from backend.browser.driver import open_customer_profile, profile_dir

STATE_PATH = config.DATA_DIR / "open_profiles.json"


# ── live-state file ──────────────────────────────────────────────────────────


def read_open_ids() -> list[int]:
    """Customer ids the user has marked 'keep open' (durable across sessions)."""
    try:
        data = json.loads(STATE_PATH.read_text("utf-8"))
        ids = data.get("open_ids", []) if isinstance(data, dict) else []
        return [int(i) for i in ids]
    except (FileNotFoundError, json.JSONDecodeError, ValueError, TypeError):
        return []


def write_open_ids(ids: list[int]) -> Path:
    """Replace the set of kept-open ids; returns the state file path."""
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    uniq = sorted({int(i) for i in ids})
    STATE_PATH.write_text(
        json.dumps({"open_ids": uniq}, indent=2), encoding="utf-8")
    return STATE_PATH


def mark_open(ids: list[int]) -> list[int]:
    """Add ids to the kept-open set (union). Returns the new full set."""
    merged = sorted(set(read_open_ids()) | {int(i) for i in ids})
    write_open_ids(merged)
    return merged


def mark_closed(ids: list[int]) -> list[int]:
    """Remove ids from the kept-open set. Returns the remaining set."""
    drop = {int(i) for i in ids}
    remaining = sorted(set(read_open_ids()) - drop)
    write_open_ids(remaining)
    return remaining


# ── keep windows open ────────────────────────────────────────────────────────


async def keep_open(ids: list[int], *, headless: bool = False,
                    landing_url: str | None = "https://www.doordash.com/orders",
                    record_state: bool = True) -> None:
    """Open each customer's persistent profile in its own window; hold open.

    Launches one headed persistent context per id (each its own browser window,
    already logged in from the saved profile), optionally navigates to a landing
    page, records the ids in the live-state file, then sleeps forever so the
    windows stay up. Cancel the task / stop the process to close them — the
    on-disk login persists regardless, so they reopen instantly next time.

    Resilient: a profile that fails to launch (e.g. its dir is locked by another
    Chromium already showing that id) is skipped with a note, not fatal.
    """
    from playwright.async_api import async_playwright

    if record_state:
        mark_open(ids)

    async with async_playwright() as p:
        contexts: list[tuple[int, Any]] = []
        for cid in ids:
            if not profile_dir(cid).exists():
                print(f"[keep_open] customer {cid}: no profile dir yet, skip")
                continue
            try:
                ctx = await open_customer_profile(p, cid, headless)
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                if landing_url:
                    try:
                        await page.goto(landing_url,
                                        wait_until="domcontentloaded")
                    except Exception:
                        pass  # a window that won't navigate is still usable
                contexts.append((cid, ctx))
                print(f"[keep_open] customer {cid}: window open")
            except Exception as exc:
                # Most common: "profile already in use" — a window for this id
                # is already up elsewhere. That's fine; it's still open for the
                # user, just not owned by this process.
                print(f"[keep_open] customer {cid}: could not open "
                      f"({type(exc).__name__}: {exc})")

        if not contexts:
            print("[keep_open] no windows opened.")
            return

        print(f"[keep_open] {len(contexts)} window(s) open: "
              f"{[c for c, _ in contexts]}. Holding — stop the process to close.")
        try:
            while True:
                await asyncio.sleep(3600)
        except (asyncio.CancelledError, KeyboardInterrupt):
            pass
        finally:
            for cid, ctx in contexts:
                try:
                    await ctx.close()
                except Exception:
                    pass
            print("[keep_open] windows closed (on-disk logins persist).")
