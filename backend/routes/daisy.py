"""CustomerDaisy web section — view/edit/delete/export CustomerDaisy's own DB.

DashManager and CustomerDaisy keep SEPARATE customer databases. The sync model
(Slice 2 of the CustomerDaisy port):

  * **CustomerDaisy's DB is the source of truth for IDENTITY + the api.cc number**
    (name, email, password, address, phone, number_token). These routes
    read/write it via the bridge (`backend/daisy/bridge.py`, Slice 1 surface).
  * **DashManager's `customers` table owns the per-run state** — bucket_date,
    session_status, storage_state, orders, refunds. It LINKS to a CustomerDaisy
    record by email (and number_token); it never duplicates identity ownership.

So this section lets the user manage the upstream pool (the accounts they
created in CustomerDaisy) from the web app, while the existing
`/api/customers/daisy/import` + `adopt_from_daisy` pull chosen ones into a
DashManager bucket for the refund pipeline. Each row is annotated with whether
it's already adopted into DashManager (matched by email) so the UI can show it.

All bridge calls go through CustomerDaisy's subprocess worker; creds/PII never
leave the local machine, and the export strips passwords (Slice 1).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from backend import db

router = APIRouter()


async def _bridge():
    """Construct a DaisyBridge from the saved daisy settings (root + python)."""
    from backend.daisy.bridge import DaisyBridge

    cfg = await db.get_setting("daisy")
    return DaisyBridge(root=cfg.get("root"), python=cfg.get("python") or None)


async def _dashmanager_emails() -> set[str]:
    """Emails already adopted into DashManager (the sync link key)."""
    return {(c.get("email") or "").lower()
            for c in await db.list_customers() if c.get("email")}


# The worker's _normalize_row is intentionally COMPLETE (internal callers like
# re-adoption need the full row, incl. the plaintext password). The HTTP layer
# must NOT leak that password to the browser — strip it here before returning.
# (Export already strips it worker-side; this covers list/get/update.)
_PUBLIC_EXCLUDE = {"password"}


def _safe(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k not in _PUBLIC_EXCLUDE}


@router.get("")
async def list_daisy_customers(limit: int = 200) -> dict[str, Any]:
    """CustomerDaisy's customers (newest first), each tagged `in_dashmanager`.

    `in_dashmanager` = this CustomerDaisy record's email already has a
    DashManager customer row (so the UI can show adopted vs not).
    """
    bridge = await _bridge()
    async with bridge as d:
        customers = await d.list_customers(limit)
        count = await d.customer_count()
    dm_emails = await _dashmanager_emails()
    for c in customers:
        c["in_dashmanager"] = (c.get("email") or "").lower() in dm_emails
    return {"customers": [_safe(c) for c in customers], "count": count}


@router.get("/{customer_id}")
async def get_daisy_customer(customer_id: str) -> dict[str, Any]:
    bridge = await _bridge()
    async with bridge as d:
        cust = await d.get_customer(customer_id)
    if cust is None:
        raise HTTPException(status_code=404, detail="customer not found")
    dm_emails = await _dashmanager_emails()
    cust["in_dashmanager"] = (cust.get("email") or "").lower() in dm_emails
    return _safe(cust)


class DaisyPatch(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    full_name: str | None = None
    email: str | None = None
    password: str | None = None
    full_address: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    phone: str | None = None  # maps to primary_phone in the worker


@router.patch("/{customer_id}")
async def update_daisy_customer(customer_id: str, body: DaisyPatch
                                ) -> dict[str, Any]:
    """Update whitelisted identity/address fields on a CustomerDaisy record."""
    fields = body.model_dump(exclude_unset=True, exclude_none=True)
    bridge = await _bridge()
    async with bridge as d:
        row = await d.update_customer(customer_id, fields)
    if row is None:
        raise HTTPException(status_code=404, detail="customer not found")
    return _safe(row)


@router.delete("/{customer_id}")
async def delete_daisy_customer(customer_id: str) -> dict[str, Any]:
    """Delete a CustomerDaisy record. Does NOT touch any DashManager row.

    (DashManager customers are managed via /api/customers; deleting upstream
    only removes it from the CustomerDaisy pool.)
    """
    bridge = await _bridge()
    async with bridge as d:
        ok = await d.delete_customer(customer_id)
    if not ok:
        raise HTTPException(status_code=404, detail="customer not found")
    return {"ok": True}


@router.get("/export/{fmt}")
async def export_daisy_customers(fmt: str, limit: int = 1000) -> Response:
    """Export the CustomerDaisy pool as a downloadable csv|json|txt file.

    Passwords are stripped from the export (the worker enforces this for json;
    csv/txt never include it). Returns the file as an attachment.
    """
    if fmt not in ("csv", "json", "txt"):
        raise HTTPException(status_code=400, detail="format must be csv|json|txt")
    bridge = await _bridge()
    async with bridge as d:
        out = await d.export(fmt, limit)
    media = {"csv": "text/csv", "json": "application/json",
             "txt": "text/plain"}[fmt]
    return Response(
        content=out.get("text", ""),
        media_type=media,
        headers={"Content-Disposition":
                 f'attachment; filename="customerdaisy.{fmt}"'})
