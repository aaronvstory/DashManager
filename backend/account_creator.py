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

import json
import shutil
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
    from playwright.async_api import async_playwright

    from backend.browser.selectors import CHROMIUM_ARGS, UA
    from backend.browser.signup import fill_signup_form, submit_and_verify

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

        async def poll_otp() -> str:
            res = await daisy.fetch_otp(token, api_url, mirror_hosts)
            return res.get("code") or ""

        # ── Drive the browser in a TEMP profile ──────────────────────────────
        # The customer row doesn't exist yet, so sign up in a temp persistent
        # profile; it's moved to data/profiles/{cid}/ once the row is created.
        import tempfile

        temp_profile = Path(tempfile.mkdtemp(prefix="dm_signup_"))
        async with async_playwright() as p:
            outcome = "failed"
            storage_backup = ""
            ctx = None
            try:
                ctx = await p.chromium.launch_persistent_context(
                    str(temp_profile),
                    headless=headless,
                    args=CHROMIUM_ARGS, user_agent=UA,
                    viewport={"width": 1400, "height": 900})
                page = ctx.pages[0] if ctx.pages else await ctx.new_page()
                await fill_signup_form(page, identity, emit=_emit)
                _emit("signup_submitting", {})
                outcome = await submit_and_verify(
                    page, poll_otp,
                    address={"full_address": identity.get("full_address")},
                    emit=_emit)
                _emit("signup_outcome", {"outcome": outcome})

                if outcome == "created":
                    config.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
                    storage_backup = str(config.SESSIONS_DIR
                                         / "pending_signup_storage.json")
                    await ctx.storage_state(path=storage_backup)
            finally:
                if ctx is not None:
                    await ctx.close()

        if outcome != "created":
            shutil.rmtree(temp_profile, ignore_errors=True)
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

        # Adopt the signup profile as the customer's persistent profile.
        # Everything below is wrapped so the temp dir is cleaned on ANY failure
        # (e.g. db.update_customer raising) — a successful move makes the
        # final rmtree a no-op since temp_profile no longer exists.
        from backend.browser.driver import profile_dir

        try:
            dest = profile_dir(cid)
            shutil.rmtree(dest, ignore_errors=True)
            shutil.move(str(temp_profile), str(dest))
            final_storage = config.SESSIONS_DIR / f"{cid}_storage.json"
            if storage_backup and Path(storage_backup).exists():
                Path(storage_backup).replace(final_storage)
            await db.update_customer(cid,
                                     storage_state_path=str(final_storage),
                                     session_status="active")
        finally:
            shutil.rmtree(temp_profile, ignore_errors=True)

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
