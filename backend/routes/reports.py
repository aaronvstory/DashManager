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

from backend import config, report

router = APIRouter()

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


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
    reports = []
    for d in dates:
        s = (await report._collect(d)).get("summary", {})
        reports.append(
            {
                "date": d,
                "url": f"/report-files/{d}.html",
                "customers": s.get("customers", 0),
                "orders": s.get("orders", 0),
                "refunded": s.get("refunded", 0),
                "pursuing": s.get("pursuing", 0),
                "unconfirmed": s.get("unconfirmed", 0),
                "needs_you": s.get("needs_you", 0),
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
