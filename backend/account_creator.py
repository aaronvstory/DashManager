"""Create a brand-new DoorDash account end to end.

Ties together:
  1. CustomerDaisy (via DaisyBridge subprocess): generate identity + address
     in a chosen location/radius, create a Mail.tm inbox, rent an api.cc number.
  2. Signup browser driver: fill the DoorDash signup form, submit, poll the
     api.cc OTP live and enter it.
  3. On success the browser is logged in → capture storage_state + cookies.
  4. Save to BOTH databases: CustomerDaisy's (so it appears there) and
     DashManager's customers table (so it joins the bucket/run pipeline).

Progress streams over the EventBus. One creation at a time is enforced by the
route layer.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend import config, db
from backend.daisy.bridge import DaisyBridge
from backend.events import bus


def _emit(type: str, data: dict | None = None) -> None:
    bus.publish(type, data or {})


async def create_account(*, location_origin: str | None,
                         radius_miles: float, bucket_date: str | None,
                         daisy_root: str | None = None,
                         headless: bool | None = None,
                         batch_id: str | None = None,
                         batch_label: str | None = None,
                         reuse_number: dict[str, Any] | None = None,
                         ) -> dict[str, Any]:
    """Run the full create-account flow. Returns a summary dict.

    `headless` overrides the browser setting for this one run (None = use the
    setting). `batch_id`/`batch_label`, when given, are stamped onto the
    CustomerDaisy record so several accounts created in one run cluster as a
    single batch in CustomerDaisy's "recent batch OTPs" screen (the per-rental
    `ordernum` is unique, so without a shared id they'd appear as N separate
    entries).

    `reuse_number`, when given, is an already-bought number dict — the rent step
    is SKIPPED and this number is used. ⚠️ USE WITH CARE: if the number's prior
    signup SUBMITTED and failed, DoorDash blocklists it (HTTP 403 forever), so
    reusing a failed-signup number just re-triggers the failure. Only pass a
    number whose signup never submitted. The skill rents fresh by default for
    this reason; this param is an escape hatch, not the norm.

    Raises on fatal failure (caller emits account_failed); partial progress is
    reported via events.
    """
    bucket = bucket_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    browser_cfg = await db.get_setting("browser")
    headless = (headless if headless is not None
                else bool(browser_cfg.get("headless", False)))

    async with DaisyBridge(root=daisy_root) as daisy:
        bal = await daisy.balance()
        _emit("account_balance", {"balance": bal})
        if bal < 0.05:
            raise RuntimeError(f"api.cc balance too low (${bal:.2f})")

        _emit("identity_generating", {"origin": location_origin,
                                      "radius_miles": radius_miles})
        identity = await daisy.generate_identity(location_origin, radius_miles)
        _emit("identity_generated", {
            "first_name": identity.get("first_name"),
            "last_name": identity.get("last_name"),
            "email": identity.get("email"),
            "city": identity.get("city"), "state": identity.get("state"),
            "full_address": identity.get("full_address")})

        if reuse_number and reuse_number.get("number_token"):
            number = dict(reuse_number)
            identity.update(number)  # reuse an already-bought number, no rent
            _emit("number_reused",
                  {"phone_number": number.get("phone_number")})
        else:
            _emit("number_renting", {})
            number = await daisy.rent_number()
            identity.update(number)  # phone_number, number_token, api_url, ...
            _emit("number_rented", {"phone_number": number.get("phone_number"),
                                    "price": number.get("price")})

        token = number["number_token"]
        api_url = number.get("api_url", "")
        mirror_hosts = number.get("mirror_hosts", [])

        # ── Drive signup via the WINNING config: SeleniumBase UC + OS-level
        #    PyAutoGUI input (os_input=True), home IP. This is the ONLY path that
        #    beats DoorDash's PerimeterX gate — synthetic Playwright/CDP input
        #    gets the "something went wrong" reject (see memory
        #    doordash-signup-bot-detection). signup_via_cdp is SYNC and handles
        #    OTP entry+submit, delivery address, the DashPass skip, and the
        #    /consumer/edit_profile confirmation internally; it returns the
        #    Playwright-compatible storage_state (cookies) the session machinery
        #    uses, so no persistent-profile move is needed.
        #    ⚠️ os_input drives the REAL shared cursor — runs must be hands-off.
        from backend.browser.cdp_signup import signup_via_cdp

        # get_running_loop (not get_event_loop): we're inside the running async
        # context, and get_event_loop is deprecated / can return a stale loop.
        loop = asyncio.get_running_loop()

        def _poll_otp_sync() -> str:
            # signup_via_cdp runs in a worker thread; marshal the async bridge
            # fetch back onto this event loop. Bounded wait (the bridge's own
            # fetch_otp timeout is ~45s) so a hung subprocess pipe can't pin a
            # thread-pool slot forever and starve other to_thread work.
            fut = asyncio.run_coroutine_threadsafe(
                daisy.fetch_otp(token, api_url, mirror_hosts), loop)
            try:
                return (fut.result(timeout=60.0).get("code") or "")
            except Exception:
                return ""

        shot_dir = str(config.SCREENSHOTS_DIR / "signup"
                       / datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")) \
            if hasattr(config, "SCREENSHOTS_DIR") else None
        result = await asyncio.to_thread(
            signup_via_cdp, identity,
            poll_otp=_poll_otp_sync, proxy=None, headless=headless,
            ios_mobile=False, os_input=True, pre_submit_dwell_s=1.5,
            emit=_emit, otp_total_wait_s=240.0, screenshot_dir=shot_dir)
        outcome = result.get("outcome", "failed")
        _emit("signup_outcome", {"outcome": outcome,
                                 "profile_confirmed":
                                 result.get("profile_confirmed", {})})

        storage_backup = ""
        if outcome == "created" and result.get("storage_state"):
            config.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
            # Per-call unique filename — a shared pending_signup_storage.json
            # would let two concurrent create_account runs overwrite each other's
            # session before the move, assigning one customer's cookies to
            # another (the old temp-profile path had this uniqueness via mkdtemp).
            uid = f"{token[:8]}_{datetime.now(timezone.utc).strftime('%H%M%S%f')}"
            storage_backup = str(config.SESSIONS_DIR
                                 / f"pending_signup_{uid}.json")
            with open(storage_backup, "w", encoding="utf-8") as f:
                json.dump(result["storage_state"], f)

        if outcome != "created":
            try:  # number/identity still useful — record the attempt
                await daisy.save_customer(_daisy_record(
                    identity, verified=False,
                    batch_id=batch_id, batch_label=batch_label))
            except Exception:
                pass
            raise RuntimeError(f"account not created (outcome={outcome})")

        # ── Persist to BOTH databases ────────────────────────────────────────
        daisy_id = ""
        try:
            daisy_id = await daisy.save_customer(
                _daisy_record(identity, verified=True,
                              batch_id=batch_id, batch_label=batch_label))
        except Exception as exc:
            _emit("log", {"level": "warn",
                          "message": f"CustomerDaisy save failed: {exc}"})

        cid = await db.create_customer(
            bucket,
            first_name=identity.get("first_name", ""),
            last_name=identity.get("last_name", ""),
            email=identity.get("email", ""),
            phone=identity.get("phone_number", ""),
            password=identity.get("password", ""),
            number_token=identity.get("number_token", ""),
            api_url=identity.get("api_url", ""),
            mirror_hosts=json.dumps(identity.get("mirror_hosts", [])),
            notes=_notes(identity, daisy_id))

        # Persist the captured session. signup_via_cdp returns cookies only (no
        # persistent profile to adopt), so we just move the pending storage JSON
        # into the customer's session path — the login/scrape machinery rehydrates
        # a Playwright context from storage_state on next use.
        final_storage = config.SESSIONS_DIR / f"{cid}_storage.json"
        if storage_backup and Path(storage_backup).exists():
            Path(storage_backup).replace(final_storage)
        await db.update_customer(cid,
                                 storage_state_path=str(final_storage),
                                 session_status="active")

        summary = {"customer_id": cid, "daisy_id": daisy_id,
                   "name": f"{identity.get('first_name','')} "
                           f"{identity.get('last_name','')}".strip(),
                   "email": identity.get("email"),
                   "phone": identity.get("phone_number"),
                   "bucket_date": bucket}
        _emit("account_created", summary)
        return summary


# rent_number returns these keys; CustomerDaisy's save_customer only folds the
# `apicc_`-prefixed names into customer metadata. Without this mapping the
# number token/url/ordernum silently drop on save — so a failed signup leaves an
# ORPHAN (a paid number with no recoverable handle). Mapping them makes orphans
# reusable: orphan_numbers() can find them and create_account(reuse_number=...)
# can finish signup on an already-bought number instead of renting a new one.
_NUMBER_FIELD_MAP = {
    "number_token": "apicc_number_token",
    "api_url": "apicc_api_url",
    "mirror_hosts": "apicc_mirror_hosts",
    "ordernum": "apicc_ordernum",
    "end_time": "apicc_end_time",
}


def _daisy_record(identity: dict[str, Any], *, verified: bool,
                  batch_id: str | None = None,
                  batch_label: str | None = None) -> dict[str, Any]:
    """Shape an identity into CustomerDaisy's save_customer payload.

    Maps the rented-number fields to the `apicc_*` keys CustomerDaisy persists
    into metadata (so the number survives even on a failed signup — recoverable
    as an orphan). When `batch_id` is given it's written to `apicc_batch_id`/
    `apicc_batch_label` so a run groups under one batch in CustomerDaisy's
    "recent batch OTPs" screen.
    """
    rec = dict(identity)
    rec["verification_completed"] = verified
    for src, dst in _NUMBER_FIELD_MAP.items():
        if identity.get(src) is not None and dst not in rec:
            rec[dst] = identity[src]
    if batch_id:
        rec["apicc_batch_id"] = batch_id
        rec["apicc_batch_label"] = batch_label or batch_id
    return rec


def _extract_orphan(rec: dict[str, Any]) -> dict[str, Any] | None:
    """Pure: turn a CustomerDaisy record into a reusable number dict, or None.

    A reusable orphan = a record whose api.cc number token is present but whose
    signup never completed (verification_completed falsy). The number lives in
    metadata under the `apicc_*` keys (see _NUMBER_FIELD_MAP); pull them back
    into the rent_number-shaped dict create_account(reuse_number=...) expects.
    """
    if rec.get("verification_completed"):
        return None
    meta = rec.get("metadata") or {}
    token = meta.get("apicc_number_token") or rec.get("number_token")
    if not token:
        return None
    return {
        "phone_number": (rec.get("primary_phone") or rec.get("phone")
                         or meta.get("phone_number") or ""),
        "number_token": token,
        "api_url": meta.get("apicc_api_url") or rec.get("api_url") or "",
        "mirror_hosts": meta.get("apicc_mirror_hosts") or [],
        "ordernum": meta.get("apicc_ordernum") or "",
        "created_at": rec.get("created_at") or "",
        "_daisy_name": f"{rec.get('first_name','')} "
                       f"{rec.get('last_name','')}".strip(),
    }


def _within_hours(created_at: str, now: datetime, max_age_hours: float) -> bool:
    """True if an ISO `created_at` is within max_age_hours of `now`.

    An unparseable/empty timestamp is treated as TOO OLD (False) — we never
    reuse a number we can't prove is recent (it could be an expired old one).
    """
    if not created_at:
        return False
    try:
        ts = datetime.fromisoformat(str(created_at))
    except (ValueError, TypeError):
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (now - ts).total_seconds() <= max_age_hours * 3600


async def orphan_numbers(*, daisy_root: str | None = None,
                         limit: int = 30,
                         max_age_hours: float = 24.0,
                         ) -> list[dict[str, Any]]:
    """Find RECENTLY-bought api.cc numbers whose signup never completed.

    ⚠️ REUSE IS USUALLY UNSAFE — verified live 2026-06-12. If a signup got far
    enough to SUBMIT and then failed, DoorDash blocklists that phone number and
    returns HTTP 403 on every later signup with it ("Something went wrong,
    please refresh and retry"). So a number from a *failed signup* is BURNED —
    reusing it just re-triggers the 403 forever. Only a number bought but whose
    signup NEVER submitted (e.g. a pure pre-submit VPN block) is safely
    reusable, and that's hard to tell apart after the fact.

    Because of this, the skill no longer reuses by default — it rents a fresh
    number per account. This function remains for the rare known-safe case and
    for diagnostics. `max_age_hours` still excludes old/expired records.
    Dedups against DashManager customers already holding that token.
    """
    now = datetime.now(timezone.utc)
    used_tokens = {c.get("number_token") for c in await db.list_customers()
                   if c.get("number_token")}
    out: list[dict[str, Any]] = []
    async with DaisyBridge(root=daisy_root) as daisy:
        recents = await daisy.list_recent_customers(limit)
    for rec in recents:
        orphan = _extract_orphan(rec)
        if (orphan and orphan["number_token"] not in used_tokens
                and _within_hours(orphan["created_at"], now, max_age_hours)):
            out.append(orphan)
    return out


async def adopt_from_daisy(name: str, bucket_date: str | None = None, *,
                           daisy_root: str | None = None,
                           limit: int = 40) -> dict[str, Any]:
    """Create a DashManager customer row from a CustomerDaisy record by name.

    For the manual-create workflow: the user signs an account up in a REAL
    browser (which passes DoorDash's signup bot check that blocks automation),
    and this pulls that account's identity + api.cc token from CustomerDaisy
    into DashManager so the normal login/refund pipeline can take over. After
    this, call relogin_customer(customer_id) to log in (login has NO bot gate)
    + capture the session via the api.cc OTP.

    `name` is matched case-insensitively against "First Last". Returns the new
    customer row dict (incl. its DashManager `id`). Raises if not found or if a
    DashManager customer with that email already exists.
    """
    bucket = bucket_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    target = name.strip().lower()
    async with DaisyBridge(root=daisy_root) as daisy:
        recents = await daisy.list_recent_customers(limit)
    rec = next(
        (c for c in recents
         if f"{c.get('first_name','')} {c.get('last_name','')}".strip().lower()
         == target), None)
    if rec is None:
        raise ValueError(f"no CustomerDaisy record named {name!r} "
                         f"(checked {len(recents)} recent)")

    email = rec.get("email", "")
    existing = await db.list_customers()
    dup = next((c for c in existing if c.get("email") == email and email), None)
    if dup:
        return dup  # already adopted — idempotent

    hosts = rec.get("mirror_hosts") or []
    cid = await db.create_customer(
        bucket,
        first_name=rec.get("first_name", ""),
        last_name=rec.get("last_name", ""),
        email=email,
        phone=rec.get("phone", ""),
        password=rec.get("password", ""),
        number_token=rec.get("number_token", ""),
        api_url=rec.get("api_url", ""),
        mirror_hosts=json.dumps(hosts),
        notes=_notes({"full_address": rec.get("full_address", "")},
                     str(rec.get("customer_id", ""))) + " · adopted (manual)")
    row = await db.get_customer(cid)
    return row or {"id": cid}


def _notes(identity: dict[str, Any], daisy_id: str) -> str:
    bits = ["created via signup"]
    if identity.get("full_address"):
        bits.append(identity["full_address"])
    if daisy_id:
        bits.append(f"daisy:{daisy_id}")
    return " · ".join(bits)
