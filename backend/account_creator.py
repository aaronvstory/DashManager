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
                         batch_label: str | None = None) -> dict[str, Any]:
    """Run the full create-account flow. Returns a summary dict.

    `headless` overrides the browser setting for this one run (None = use the
    setting). `batch_id`/`batch_label`, when given, are stamped onto the
    CustomerDaisy record so several accounts created in one run cluster as a
    single batch in CustomerDaisy's "recent batch OTPs" screen (the per-rental
    `ordernum` is unique, so without a shared id they'd appear as N separate
    entries). Raises on fatal failure (caller emits account_failed); partial
    progress is reported via events.
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


def _daisy_record(identity: dict[str, Any], *, verified: bool,
                  batch_id: str | None = None,
                  batch_label: str | None = None) -> dict[str, Any]:
    """Shape an identity into CustomerDaisy's save_customer payload.

    When `batch_id` is given it's written to the `apicc_batch_id`/
    `apicc_batch_label` fields CustomerDaisy folds into customer metadata, so a
    run of several accounts groups under one batch in its "recent batch OTPs"
    screen.
    """
    rec = dict(identity)
    rec["verification_completed"] = verified
    if batch_id:
        rec["apicc_batch_id"] = batch_id
        rec["apicc_batch_label"] = batch_label or batch_id
    return rec


def _notes(identity: dict[str, Any], daisy_id: str) -> str:
    bits = ["created via signup"]
    if identity.get("full_address"):
        bits.append(identity["full_address"])
    if daisy_id:
        bits.append(f"daisy:{daisy_id}")
    return " · ".join(bits)
