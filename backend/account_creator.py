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
                         daisy_root: str | None = None) -> dict[str, Any]:
    """Run the full create-account flow. Returns a summary dict.

    Raises on fatal failure (caller emits account_failed); partial progress is
    reported via events so the UI shows where it stopped.
    """
    from playwright.async_api import async_playwright

    from backend.browser.driver import launch_browser
    from backend.browser.selectors import UA
    from backend.browser.signup import fill_signup_form, submit_and_verify

    bucket = bucket_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    browser_cfg = await db.get_setting("browser")

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

        # ── Drive the browser ────────────────────────────────────────────────
        async with async_playwright() as p:
            browser = await launch_browser(
                p, bool(browser_cfg.get("headless", False)))
            outcome = "failed"
            storage_path = cookies_path = ""
            try:
                ctx = await browser.new_context(
                    viewport={"width": 1400, "height": 900}, user_agent=UA)
                page = await ctx.new_page()
                await fill_signup_form(page, identity, emit=_emit)
                _emit("signup_submitting", {})
                outcome = await submit_and_verify(
                    page, poll_otp,
                    address={"full_address": identity.get("full_address")},
                    emit=_emit)
                _emit("signup_outcome", {"outcome": outcome})

                if outcome == "created":
                    config.SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
                    storage_path = str(config.SESSIONS_DIR
                                       / "pending_signup_storage.json")
                    cookies_path = str(config.SESSIONS_DIR
                                       / "pending_signup_cookies.json")
                    await ctx.storage_state(path=storage_path)
                    Path(cookies_path).write_text(
                        json.dumps(await ctx.cookies(), indent=2), "utf-8")
            finally:
                await browser.close()

        if outcome != "created":
            # Number/identity are still useful; record the attempt in Daisy DB.
            try:
                await daisy.save_customer(_daisy_record(identity, verified=False))
            except Exception:
                pass
            raise RuntimeError(f"account not created (outcome={outcome})")

        # ── Persist to BOTH databases ────────────────────────────────────────
        daisy_id = ""
        try:
            daisy_id = await daisy.save_customer(
                _daisy_record(identity, verified=True))
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
        # Rename pending session files to the customer-scoped names.
        final_storage = config.SESSIONS_DIR / f"{cid}_storage.json"
        final_cookies = config.SESSIONS_DIR / f"{cid}_cookies.json"
        Path(storage_path).replace(final_storage)
        Path(cookies_path).replace(final_cookies)
        await db.update_customer(cid, storage_state_path=str(final_storage),
                                 cookies_path=str(final_cookies))

        summary = {"customer_id": cid, "daisy_id": daisy_id,
                   "name": f"{identity.get('first_name','')} "
                           f"{identity.get('last_name','')}".strip(),
                   "email": identity.get("email"),
                   "phone": identity.get("phone_number"),
                   "bucket_date": bucket}
        _emit("account_created", summary)
        return summary


def _daisy_record(identity: dict[str, Any], *, verified: bool) -> dict[str, Any]:
    """Shape an identity into CustomerDaisy's save_customer payload."""
    rec = dict(identity)
    rec["verification_completed"] = verified
    return rec


def _notes(identity: dict[str, Any], daisy_id: str) -> str:
    bits = ["created via signup"]
    if identity.get("full_address"):
        bits.append(identity["full_address"])
    if daisy_id:
        bits.append(f"daisy:{daisy_id}")
    return " · ".join(bits)
