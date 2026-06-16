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

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from backend import db
from backend.daisy.bridge import DaisyError

if TYPE_CHECKING:
    from backend.daisy.bridge import DaisyBridge

router = APIRouter()


def _address_http_error(exc: Exception) -> HTTPException:
    """Map a worker address error (surfaced as a DaisyError string) to HTTP.

    The worker raises IndexError ("out of range") for a bad index and ValueError
    ("needs a non-empty full_address") for bad input; both arrive here as the
    DaisyError message. 404 for out-of-range, 400 otherwise.
    """
    msg = str(exc)
    status = 404 if "out of range" in msg else 400
    # Worker-authored message (addresses are the user's own, not creds).
    return HTTPException(status_code=status, detail=msg.split(": ")[-1])


async def _bridge() -> DaisyBridge:
    """Construct a DaisyBridge from the saved daisy settings (root + python)."""
    from backend.daisy.bridge import DaisyBridge

    cfg = await db.get_setting("daisy")
    if not isinstance(cfg, dict):  # defensive — the default is always a dict
        cfg = {}
    return DaisyBridge(root=cfg.get("root"), python=cfg.get("python") or None)


async def _dashmanager_emails() -> set[str]:
    """Emails already adopted into DashManager (the sync link key).

    Queries only the email column (not full customer rows — the table carries
    large text fields we don't need just to build a lookup set).
    """
    rows = await db.query("SELECT email FROM customers WHERE email != ''")
    return {(r.get("email") or "").lower() for r in rows if r.get("email")}


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


# NOTE: this static-segment route is declared BEFORE `/{customer_id}` so it is
# matched as a literal, not captured as a customer id.
@router.get("/addresses")
async def list_anchor_addresses() -> dict[str, Any]:
    """The user's anchor-address pool (CustomerDaisy's my_addresses.json).

    For the create-account dialog: lets a batch be anchored to one of the
    user's own saved addresses instead of only the predefined locations.
    Empty list when no my_addresses.json is configured.
    """
    bridge = await _bridge()
    async with bridge as d:
        addresses = await d.list_addresses()
    return {"addresses": addresses}


@router.post("/addresses")
async def add_anchor_address(body: AnchorAddress) -> dict[str, Any]:
    """Append an address to the anchor pool; returns the new full list."""
    bridge = await _bridge()
    async with bridge as d:
        addresses = await d.add_address(body.model_dump())
    return {"addresses": addresses}


@router.patch("/addresses/{index}")
async def update_anchor_address(index: int, body: AnchorAddress
                                ) -> dict[str, Any]:
    """Replace the anchor address at ``index`` (0-based). 404 if out of range."""
    bridge = await _bridge()
    try:
        async with bridge as d:
            addresses = await d.update_address(index, body.model_dump())
    except DaisyError as exc:
        raise _address_http_error(exc) from exc
    return {"addresses": addresses}


@router.delete("/addresses/{index}")
async def delete_anchor_address(index: int) -> dict[str, Any]:
    """Remove the anchor address at ``index`` (0-based). 404 if out of range."""
    bridge = await _bridge()
    try:
        async with bridge as d:
            addresses = await d.delete_address(index)
    except DaisyError as exc:
        raise _address_http_error(exc) from exc
    return {"addresses": addresses}


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


class AnchorAddress(BaseModel):
    # An anchor-pool entry. full_address is the only required field; the worker
    # also validates it's non-empty. extra=forbid so a typo'd key fails loud.
    model_config = {"extra": "forbid"}
    full_address: str
    name: str = ""
    city: str = ""
    state: str = ""


class DaisyPatch(BaseModel):
    # Reject unknown keys so a client can't think an unsupported field was
    # applied (the worker also whitelists columns, but fail loud at the edge).
    model_config = {"extra": "forbid"}

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
