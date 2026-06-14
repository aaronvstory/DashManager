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
    from collections import defaultdict
    from backend.report import resolution_method

    # Bucket-scoped batch reads — a handful of JOINed queries, grouped in
    # memory, instead of a query per customer/order/chat (no N+1).
    customers = await db.list_customers_for_bucket(report_date)
    all_orders = await db.list_orders_for_bucket(report_date)
    all_claims = await db.list_claims_for_bucket(report_date)
    all_chats = await db.list_chats_for_bucket(report_date)
    all_msgs = await db.list_chat_messages_for_bucket(report_date)
    all_shots = await db.list_screenshots_for_bucket(report_date)

    claims_by_order: dict[int, list] = defaultdict(list)
    for cl in all_claims:
        claims_by_order[cl["order_id"]].append(dict(cl))
    msgs_by_chat: dict[int, list] = defaultdict(list)
    for m in all_msgs:
        msgs_by_chat[m["chat_id"]].append(dict(m))
    chats_by_order: dict[int, list] = defaultdict(list)
    for ch in all_chats:
        ch = dict(ch)
        ch["messages"] = msgs_by_chat.get(ch["id"], [])
        chats_by_order[ch["order_id"]].append(ch)
    shots_by_order: dict[int, list] = defaultdict(list)
    shots_by_cust: dict[int, list] = defaultdict(list)  # non-order (orders-page)
    for s in all_shots:
        url = _shot_url(s["path"])
        if not url:
            continue
        item = {"url": url, "label": s.get("label", ""), "kind": s.get("kind", "")}
        if s.get("order_id"):
            shots_by_order[s["order_id"]].append(item)
        else:
            shots_by_cust[s["customer_id"]].append(item)
    orders_by_cust: dict[int, list] = defaultdict(list)
    for o in all_orders:
        orders_by_cust[o["customer_id"]].append(dict(o))

    rows = []
    summary = {"customers": len(customers), "orders": 0, "refunded": 0,
               "pursuing": 0, "unconfirmed": 0, "needs_you": 0, "no_orders": 0,
               "total_refunded": 0.0}
    for c in customers:
        c = dict(c)
        # Strip every credential/secret the report view never needs.
        # number_token is the api.cc SIM token (a credential) — STRIP it too.
        for k in ("password", "storage_state_path", "cookies_path",
                  "api_url", "mirror_hosts", "number_token"):
            c.pop(k, None)
        orders = []
        for o in orders_by_cust.get(c["id"], []):
            o["claims"] = claims_by_order.get(o["id"], [])
            o["chats"] = chats_by_order.get(o["id"], [])
            label, confirmation = resolution_method(o)
            o["resolution"] = {"label": label, "confirmation": confirmation}
            o["screenshots"] = shots_by_order.get(o["id"], [])
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
            # needs_you mirrors the sidebar's definition exactly (pursuing set),
            # so the native view and the report list never disagree. `unchecked`
            # (not yet processed) is NOT "needs you" — it's just unscraped.
            if st in _PURSUING:
                summary["needs_you"] += 1
            orders.append(o)
        if not orders:
            summary["no_orders"] += 1
        c["screenshots"] = shots_by_cust.get(c["id"], [])
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
