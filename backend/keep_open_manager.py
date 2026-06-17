"""App-owned "keep browsers open" manager.

``profiles_live.keep_open`` is a CLI helper: it opens windows then blocks
forever (``while True: sleep``), so a web request can't drive it. This module
is the request-callable equivalent — a single long-lived manager (mirroring
``runner.RunManager``) that holds a dict of open Playwright contexts under one
``async_playwright`` lifetime and lets routes open/close them on demand.

Contention with the refund run (the important invariant):
  A customer's persistent Chromium user-data-dir can only be open ONCE
  (Chromium locks the dir). ``driver.profile_lock(cid)`` serializes access. The
  refund run acquires that lock before it opens a profile
  (``runner._process_customer``). So keep-open HOLDS ``profile_lock(cid)`` for
  each window it keeps up — which correctly BLOCKS a run from double-opening it.
  Before a run starts, the route layer calls ``close(ids)`` to release those
  locks so the run can adopt the same on-disk (already-logged-in) profile.

Lifetime: in-process, dies on server restart (by design — Decision 1A). The
on-disk logins persist regardless, so windows reopen instantly next time. A
shutdown handler calls ``close_all`` for a clean teardown.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from playwright.async_api import BrowserContext, Playwright, async_playwright

from backend import db, profiles_live
from backend.browser.driver import (
    open_customer_profile,
    profile_dir,
    profile_lock,
)
from backend.events import bus

# Same headed window size as the refund run + the CLI keep_open, so a window
# kept open looks identical whether the run or keep-open owns it.
_VIEWPORT = (1200, 720)

# Where a kept-open window lands by default. The whole point is to eyeball /
# log into the account, so open straight to the logged-in orders page — NOT
# about:blank (a blank window is useless). Callers can override per request.
_DEFAULT_LANDING_URL = "https://www.doordash.com/orders"


class KeepOpenManager:
    """Holds customer Chromium windows open between runs (singleton)."""

    def __init__(self) -> None:
        self._pw: Playwright | None = None
        self._contexts: dict[int, BrowserContext] = {}
        # The profile_lock we hold for each open id — released on close so a run
        # (or a fresh open) can take it. Tracking OUR acquisition explicitly:
        # `lock.locked()` is True even when another task holds it, so we must
        # never release a lock we didn't acquire.
        self._held: dict[int, asyncio.Lock] = {}
        # Serializes open/close so two concurrent requests can't race on the
        # contexts dict or the shared Playwright lifetime.
        self._gate = asyncio.Lock()

    # ── lifetime ──────────────────────────────────────────────────────────

    async def _ensure_pw(self) -> Playwright:
        """Start the shared Playwright lifetime lazily; keep it for the process."""
        if self._pw is None:
            self._pw = await async_playwright().start()
        return self._pw

    # ── open / close ──────────────────────────────────────────────────────

    async def _seed_for(self, cid: int) -> str | None:
        """A storage_state file to seed a fresh profile for a customer that has
        no profile dir yet (a just-signed-up account). Returns the path if the
        customer has a captured storage_state on disk, else None."""
        row = await db.get_customer(cid)
        if not row:
            return None
        path = (row.get("storage_state_path") or "").strip()
        if path and Path(path).exists():
            return path
        return None

    async def open(
        self,
        ids: list[int],
        *,
        headless: bool = False,
        landing_url: str | None = _DEFAULT_LANDING_URL,
    ) -> dict[str, list[int]]:
        """Open a window per id and hold it. Returns {opened, skipped}.

        Skips an id that is already kept open, or whose profile_lock is held by
        something else (a run in flight, a manual session) — never double-opens
        one user-data-dir. Each successfully opened id has its profile_lock held
        until ``close``.
        """
        opened: list[int] = []
        skipped: list[int] = []
        async with self._gate:
            pw = await self._ensure_pw()
            for cid in ids:
                if cid in self._contexts:
                    skipped.append(cid)  # already ours, nothing to do
                    continue
                # A profile dir means a prior interactive login/run; open it
                # directly. A freshly SIGNED-UP account has no profile dir yet —
                # only captured cookies (storage_state_path) — so seed a fresh
                # profile from those so it opens already logged in. Skip only
                # when there's neither.
                d = profile_dir(cid)
                has_profile = d.exists() and any(d.iterdir())
                seed = None if has_profile else await self._seed_for(cid)
                if not has_profile and seed is None:
                    skipped.append(cid)  # nothing to open it from yet
                    continue
                lock = profile_lock(cid)
                # Don't block — a held lock means a run/manual op owns this
                # profile right now; skip rather than queue behind it.
                if lock.locked():
                    skipped.append(cid)
                    continue
                await lock.acquire()
                try:
                    ctx = await open_customer_profile(
                        pw, cid, headless, viewport=_VIEWPORT,
                        seed_storage_state=seed)
                except Exception:
                    # Launch failed (e.g. a stale OS-level dir lock). Release
                    # the asyncio lock so we don't strand the profile.
                    lock.release()
                    skipped.append(cid)
                    continue
                if landing_url:
                    try:
                        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                        await page.goto(landing_url, wait_until="domcontentloaded")
                    except Exception:
                        pass  # a window that won't navigate is still usable
                self._contexts[cid] = ctx
                self._held[cid] = lock
                # If the USER closes the window manually, Playwright fires
                # "close" — without this we'd leak state and, worse, never
                # release the profile_lock (blocking every future run for this
                # customer). Clean up reactively. (Our own close() pops the
                # entry BEFORE awaiting ctx.close(), so this no-ops then.)
                ctx.on("close", lambda _ctx=None, _cid=cid: self._on_context_closed(_cid))
                opened.append(cid)

        if opened:
            profiles_live.mark_open(opened)
            bus.publish("keep_open_started", {"ids": opened})
        if skipped:
            bus.publish("keep_open_skipped", {"ids": skipped})
        return {"opened": opened, "skipped": skipped}

    async def close(self, ids: list[int] | None = None) -> list[int]:
        """Close the given kept-open windows (or all if ids is None).

        Closes each context, releases the held profile_lock, and clears it from
        the live-state file. Returns the ids actually closed.
        """
        closed: list[int] = []
        async with self._gate:
            targets = (
                list(self._contexts.keys())
                if ids is None
                else [cid for cid in ids if cid in self._contexts]
            )
            for cid in targets:
                # Pop + release FIRST so the ctx "close" handler (which fires
                # during await ctx.close()) sees the entry already gone and
                # no-ops — no double release.
                ctx = self._contexts.pop(cid, None)
                lock = self._held.pop(cid, None)
                if lock is not None and lock.locked():
                    lock.release()
                if ctx is not None:
                    try:
                        await ctx.close()
                    except Exception:
                        pass  # already gone; the lock is already released
                closed.append(cid)

        if closed:
            profiles_live.mark_closed(closed)
            bus.publish("keep_open_closed", {"ids": closed})
        return closed

    def _on_context_closed(self, cid: int) -> None:
        """Sync handler for Playwright's ctx "close" — fires on manual close.

        Releases the held lock and clears state for a window the user closed
        themselves. Idempotent: a no-op when our own close() already popped the
        entry (it pops before awaiting ctx.close()). All work here is sync
        (dict ops, lock.release, file write, publish) — safe in a sync callback.
        """
        if cid not in self._contexts:
            return  # our own close() already handled it
        self._contexts.pop(cid, None)
        lock = self._held.pop(cid, None)
        if lock is not None and lock.locked():
            lock.release()
        profiles_live.mark_closed([cid])
        bus.publish("keep_open_closed", {"ids": [cid]})

    async def close_all(self) -> list[int]:
        """Close every kept-open window + stop Playwright — for shutdown."""
        closed = await self.close(None)
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception:
                pass
            self._pw = None
        return closed

    # ── status ────────────────────────────────────────────────────────────

    def status(self) -> dict[str, list[int]]:
        """Live + durable view of which profiles are open.

        ``open_ids`` are the windows THIS process is holding right now.
        ``recorded_ids`` is the durable live-state file (may include ids a prior
        process opened then died on — i.e. stale entries the UI can reconcile).
        """
        open_ids = sorted(self._contexts.keys())
        return {
            "open_ids": open_ids,
            "recorded_ids": profiles_live.read_open_ids(),
        }


# Module-level singleton (same pattern as runner.manager).
manager = KeepOpenManager()
