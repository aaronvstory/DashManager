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


@router.post("/{report_date}/rebuild")
async def rebuild_report(report_date: str) -> dict:
    """Re-render a day's report from the current DB state."""
    if not _DATE_RE.match(report_date):
        raise HTTPException(400, "report_date must be YYYY-MM-DD")
    path = await report.build_daily_report(report_date)
    return {"date": report_date, "url": f"/report-files/{path.name}"}
