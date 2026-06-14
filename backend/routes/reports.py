"""Daily-report endpoints: list the generated reports and (re)build one.

The report HTML files themselves (with embedded chat transcripts, refund
method, and proof thumbnails) are served as static files — see the
``/reports`` + ``/screenshots`` mounts in ``backend.main``. This router only
exposes the *index* (which dates exist + a quick summary) so the React app can
render a browsable "Reports" page, plus a build trigger.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException

from backend import config, db, report

router = APIRouter()

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Refund states that still need attention (everything except a receipt-proven
# `refunded`). `unconfirmed` is counted both here (pursuing) and on its own.
_PURSUING = ("not_refunded", "partial", "pending_claim", "remake", "unconfirmed")


@router.get("")
async def list_reports() -> dict:
    """Every daily report on disk, newest first, with a small summary.

    The summary (customer/order/refund counts) is rebuilt from the DB so it is
    always live, even if the on-disk HTML is stale.
    """
    out_dir = config.REPORTS_DIR
    dates = sorted(
        (p.stem for p in out_dir.glob("*.html") if _DATE_RE.match(p.stem)),
        reverse=True,
    )
    if not dates:
        return {"reports": []}

    # Two flat aggregate queries for ALL buckets (not N+1 per date): one for the
    # customer count per bucket, one for order counts per (bucket, refund_status).
    # Computed once and sliced per date below. `needs_you` == pursuing here
    # (every non-`refunded` order needs attention), so it's derived, not queried.
    cust_rows = await db.query(
        "SELECT bucket_date AS d, COUNT(*) AS n "
        "FROM customers GROUP BY bucket_date")
    cust_by_date = {r["d"]: r["n"] for r in cust_rows}
    order_rows = await db.query(
        "SELECT c.bucket_date AS d, o.refund_status AS st, COUNT(*) AS n "
        "FROM orders o JOIN customers c ON o.customer_id = c.id "
        "GROUP BY c.bucket_date, o.refund_status")
    # date -> {refund_status -> count}
    by_date: dict[str, dict[str, int]] = {}
    for r in order_rows:
        by_date.setdefault(r["d"], {})[r["st"]] = r["n"]

    reports = []
    for d in dates:
        counts = by_date.get(d, {})
        orders = sum(counts.values())
        refunded = counts.get("refunded", 0)
        unconfirmed = counts.get("unconfirmed", 0)
        pursuing = sum(counts.get(st, 0) for st in _PURSUING)
        reports.append(
            {
                "date": d,
                "url": f"/report-files/{d}.html",
                "customers": cust_by_date.get(d, 0),
                "orders": orders,
                "refunded": refunded,
                "pursuing": pursuing,
                "unconfirmed": unconfirmed,
                "needs_you": pursuing,
            }
        )
    return {"reports": reports}


def _shot_url(path: str) -> str:
    """Map a stored screenshot path to its served URL (/screenshots/<…>)."""
    p = (path or "").replace("\\", "/")
    marker = "/screenshots/"
    i = p.find(marker)
    return ("/screenshots/" + p[i + len(marker):]) if i >= 0 else ""


@router.get("/{report_date}/data")
async def report_data(report_date: str) -> dict:
    """Full native-report model for a day: per-customer orders enriched with
    resolution, chats (+messages), claims, and proof screenshot URLs — so the
    React app can render the report natively (full width, hover-zoom proofs)
    instead of cramming the standalone HTML into an iframe.
    """
    if not _DATE_RE.match(report_date):
        raise HTTPException(400, "report_date must be YYYY-MM-DD")
    from backend.report import resolution_method

    customers = [c for c in await db.list_customers()
                 if c.get("bucket_date") == report_date]
    customers.sort(key=lambda c: c.get("id", 0))

    rows = []
    summary = {"customers": len(customers), "orders": 0, "refunded": 0,
               "pursuing": 0, "unconfirmed": 0, "needs_you": 0, "no_orders": 0,
               "total_refunded": 0.0}
    for c in customers:
        c = dict(c)
        # strip secrets the report view never needs
        for k in ("password", "storage_state_path", "cookies_path",
                  "api_url", "mirror_hosts"):
            c.pop(k, None)
        orders = []
        cust_shots = await db.list_screenshots_for_customer(c["id"])
        for o in await db.list_orders(c["id"]):
            o = dict(o)
            o["claims"] = await db.list_claims_for_order(o["id"])
            chats = []
            for ch in await db.list_chats_for_order(o["id"]):
                ch = dict(ch)
                ch["messages"] = await db.list_chat_messages(ch["id"])
                chats.append(ch)
            o["chats"] = chats
            label, confirmation = resolution_method(o)
            o["resolution"] = {"label": label, "confirmation": confirmation}
            o["screenshots"] = [
                {"url": _shot_url(s["path"]), "label": s.get("label", ""),
                 "kind": s.get("kind", "")}
                for s in cust_shots if s.get("order_id") == o["id"]
                and _shot_url(s["path"])
            ]
            st = o.get("refund_status", "unchecked")
            summary["orders"] += 1
            if st == "refunded":
                summary["refunded"] += 1
                if o.get("refund_amount"):
                    summary["total_refunded"] += float(o["refund_amount"])
            elif st == "unconfirmed":
                summary["unconfirmed"] += 1
                summary["pursuing"] += 1
            elif st in _PURSUING:
                summary["pursuing"] += 1
            if st != "refunded":
                summary["needs_you"] += 1
            orders.append(o)
        if not orders:
            summary["no_orders"] += 1
        # customer-level (non-order) proof shots, e.g. the orders-page capture
        c["screenshots"] = [
            {"url": _shot_url(s["path"]), "label": s.get("label", ""),
             "kind": s.get("kind", "")}
            for s in cust_shots if not s.get("order_id") and _shot_url(s["path"])
        ]
        c["orders"] = orders
        rows.append(c)

    return {"date": report_date, "customers": rows, "summary": summary,
            "html_url": f"/report-files/{report_date}.html"}


@router.post("/{report_date}/rebuild")
async def rebuild_report(report_date: str) -> dict:
    """Re-render a day's report from the current DB state."""
    if not _DATE_RE.match(report_date):
        raise HTTPException(400, "report_date must be YYYY-MM-DD")
    path = await report.build_daily_report(report_date)
    return {"date": report_date, "url": f"/report-files/{path.name}"}
