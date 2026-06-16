"""Daily working-report generator — a self-contained, hand-designed HTML file.

One report per day at ``data/reports/YYYY-MM-DD.html`` plus an ``index.html``
landing page linking every day. It is the shared spine between the
``/dash-create`` and ``/dash-refunds`` skills: prep writes the customer roster
(full account info), refunds writes order outcomes + chat transcripts onto the
same file. Re-runnable and idempotent — it rebuilds the whole day from the DB.

Design intent (no template-slop): a calm warm near-black/parchment surface, a
single DoorDash-red accent, real type hierarchy (sans + mono for data), status
as quiet pills, transcripts as honest chat bubbles. Each customer is a
collapsible card showing every operational detail (phone/email/address/password/
api.cc ID/session/dates) with copy-to-clipboard chips and reveal toggles for the
sensitive fields. Small self-contained inline JS — no CDN, opens from disk
offline, prints clean.
"""
from __future__ import annotations

import html
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend import config, db

# ── public surface ───────────────────────────────────────────────────────────


async def build_daily_report(report_date: str | None = None,
                             out_dir: Path | None = None) -> Path:
    """Rebuild the day's report (and refresh the index) from the DB.

    `report_date` is a 'YYYY-MM-DD' bucket; defaults to today (UTC). Returns the
    written daily-report path. Also (re)writes index.html listing all reports.
    """
    report_date = report_date or _today()
    out_dir = out_dir or (config.DATA_DIR / "reports")
    out_dir.mkdir(parents=True, exist_ok=True)

    model = await _collect(report_date)
    out_path = out_dir / f"{report_date}.html"
    out_path.write_text(render_report(model), encoding="utf-8")

    # refresh the index across all known buckets
    try:
        (out_dir / "index.html").write_text(
            render_index(await _index_model(out_dir)), encoding="utf-8")
    except Exception:
        pass  # index is a convenience; never fail the daily build over it
    return out_path


async def _collect(report_date: str) -> dict[str, Any]:
    """Pull every customer in the bucket plus their orders, claims, chats."""
    customers = [c for c in await db.list_customers()
                 if c.get("bucket_date") == report_date]
    customers.sort(key=lambda c: c.get("id", 0))

    rows: list[dict[str, Any]] = []
    for idx, c in enumerate(customers, 1):
        orders = await db.list_orders(c["id"])
        enriched_orders = []
        for o in orders:
            claims = await db.list_claims_for_order(o["id"])
            chats = await db.list_chats_for_order(o["id"])
            chat_views = []
            for ch in chats:
                msgs = await db.list_chat_messages(ch["id"])
                chat_views.append({**ch, "messages": msgs})
            enriched_orders.append(
                {**o, "claims": claims, "chats": chat_views})
        shots = []
        try:
            shots = await db.list_screenshots_for_customer(c["id"])
        except Exception:
            pass  # screenshots table may not exist on old DBs
        rows.append({**c, "orders": enriched_orders,
                     "screenshots": shots,
                     "_seq": idx,
                     "_copy_id": _short_id(c, report_date, idx)})

    return {
        "date": report_date,
        "generated_at": _now_human(),
        "customers": rows,
        "summary": _summarize(rows),
    }


async def _index_model(out_dir: Path) -> dict[str, Any]:
    """Collect a per-bucket summary for the index, newest first."""
    by_bucket: dict[str, list[dict[str, Any]]] = {}
    for c in await db.list_customers():
        by_bucket.setdefault(c.get("bucket_date", ""), []).append(c)

    # only list buckets that actually have a rendered report file
    entries = []
    for f in sorted(out_dir.glob("*.html"), reverse=True):
        bucket = f.stem
        if bucket == "index":
            continue
        custs = by_bucket.get(bucket, [])
        active = sum(1 for c in custs if c.get("session_status") == "active")
        entries.append({
            "bucket": bucket,
            "pretty": _pretty_date(bucket),
            "file": f.name,
            "customers": len(custs),
            "active": active,
        })
    return {"entries": entries, "generated_at": _now_human()}


def _summarize(rows: list[dict[str, Any]]) -> dict[str, int]:
    s = {"customers": len(rows), "orders": 0, "refunded": 0,
         "pursuing": 0, "unconfirmed": 0, "unchecked": 0, "no_orders": 0,
         "needs_you": 0, "active": 0}
    for c in rows:
        if c.get("session_status") == "active":
            s["active"] += 1
        orders = c["orders"]
        if not orders:
            s["no_orders"] += 1
        for o in orders:
            s["orders"] += 1
            st = o.get("refund_status", "unchecked")
            if st == "refunded":
                s["refunded"] += 1
            elif st == "unconfirmed":
                # Tracked separately AND as pursuing — it is NOT done.
                s["unconfirmed"] += 1
                s["pursuing"] += 1
            elif st in ("not_refunded", "partial", "pending_claim", "remake",
                        "unknown"):
                # `unknown` = a receipt we READ but couldn't parse — it needs a
                # human (see _order_needs_you), so it belongs in pursuing, NOT
                # lumped with the transient `unchecked`.
                s["pursuing"] += 1
            else:
                # `unchecked` (not yet scraped) or any other unrecognized status.
                # Count it so the breakdown is EXHAUSTIVE — refunded + pursuing +
                # unchecked == orders — and no order is silently uncounted on a
                # money-tracking board.
                s["unchecked"] += 1
            if _order_needs_you(o):
                s["needs_you"] += 1
    return s


def _order_needs_you(o: dict[str, Any]) -> bool:
    st = o.get("refund_status", "unchecked")
    if st == "refunded":
        return False  # only a receipt-proven refund is truly done
    # ZERO-TOLERANCE: `unconfirmed` ALWAYS needs a human — an agent promise or a
    # claim we couldn't prove to the card is not money in hand. A successful
    # chat now lands the order in `unconfirmed` (not `refunded`), so it still
    # surfaces here until a detect re-check proves the Refund -$X line.
    if st == "unconfirmed":
        return True
    if st in ("not_refunded", "partial", "pending_claim", "remake", "unknown"):
        return True
    return False


# ── rendering: daily report ──────────────────────────────────────────────────


def render_report(model: dict[str, Any]) -> str:
    """Pure: model dict -> full HTML document string (no I/O, unit-testable)."""
    date = esc(model["date"])
    pretty_date = _pretty_date(model["date"])
    s = model["summary"]
    cards = _summary_cards(s)
    customers_html = "\n".join(_customer_section(c)
                               for c in model["customers"])
    if not model["customers"]:
        customers_html = _empty_state()

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DashManager · {date}</title>
{_STYLE}
</head>
<body>
<div class="shell">
  <header class="masthead">
    <div class="brand">
      <a class="brand-link" href="index.html" title="All reports">
        <span class="brand-dot"></span>
        <span class="brand-name">DashManager</span>
      </a>
      <a class="index-link" href="index.html">All reports →</a>
    </div>
    <div class="masthead-meta">
      <div class="eyebrow">Daily refund worklog</div>
      <h1 class="title">{pretty_date}</h1>
      <div class="subtle">Generated {esc(model['generated_at'])}</div>
    </div>
  </header>

  <section class="cards">
    {cards}
  </section>

  <div class="toolbar">
    <button class="tbtn" onclick="dmAll(true)">Expand all</button>
    <button class="tbtn" onclick="dmAll(false)">Collapse all</button>
  </div>

  <main class="roster">
    {customers_html}
  </main>

  <footer class="footer">
    <span>DashManager — local refund audit · all amounts pursued to the
    original payment method, never store credit.</span>
  </footer>
</div>
<div class="toast" id="dmToast">Copied</div>
{_SCRIPT}
</body>
</html>"""


def _summary_cards(s: dict[str, int]) -> str:
    items = [
        ("Customers", s["customers"], "neutral"),
        ("Sessions live", s.get("active", 0), "good"),
        ("Orders", s["orders"], "neutral"),
        ("Refunded", s["refunded"], "good"),
        ("Pursuing", s["pursuing"], "warn"),
        ("Needs you", s["needs_you"], "alert" if s["needs_you"] else "muted"),
    ]
    # Only surface Unchecked when there ARE any — it means orders weren't
    # verified this run (a partial/interrupted scrape), which shouldn't read as
    # "all clear". Hidden on a clean board to avoid noise.
    if s.get("unchecked", 0):
        items.append(("Unchecked", s["unchecked"], "warn"))
    out = []
    for label, value, tone in items:
        out.append(
            f'<div class="card card--{tone}">'
            f'<div class="card-value">{value}</div>'
            f'<div class="card-label">{esc(label)}</div></div>')
    return "\n".join(out)


def _customer_section(c: dict[str, Any]) -> str:
    name = esc(_full_name(c)) or "Unnamed customer"
    initials = esc(_initials(c))
    session = _session_pill(c.get("session_status", ""))
    seq = c.get("_seq", "")
    orders = c["orders"]

    # account detail grid (the operational info)
    details = _account_details(c)
    proof = _proof_row(c.get("screenshots", []))

    if not orders:
        body = ('<div class="no-orders">'
                '<span class="no-orders-mark">∅</span>'
                '<div><div class="no-orders-title">No orders to audit</div>'
                '<div class="subtle">No DoorDash order history scraped yet '
                '(run a refund check to populate).</div></div></div>')
    else:
        body = _breakdown_table(orders) + "\n".join(
            _order_block(o) for o in orders)

    # quick summary line shown on the collapsed header
    quick = _customer_quick(c)

    return f"""<details class="customer" open>
  <summary class="customer-head">
    <span class="seq">{esc(str(seq))}</span>
    <span class="avatar">{initials}</span>
    <span class="customer-id">
      <span class="customer-name">{name} {session}</span>
      <span class="customer-quick">{quick}</span>
    </span>
    <span class="chevron">▾</span>
  </summary>
  <div class="customer-inner">
    <div class="account-grid">
      {details}
    </div>
    {proof}
    <div class="customer-body">
      {body}
    </div>
  </div>
</details>"""


def _customer_quick(c: dict[str, Any]) -> str:
    """One-line summary shown on the collapsed customer header."""
    orders = c["orders"]
    if not orders:
        return '<span class="q-mut">no orders yet</span>'
    refunded = sum(1 for o in orders if o.get("refund_status") == "refunded")
    pursue = sum(1 for o in orders
                 if o.get("refund_status") in
                 ("not_refunded", "partial", "pending_claim", "remake"))
    bits = [f'{len(orders)} order' + ("s" if len(orders) != 1 else "")]
    if refunded:
        bits.append(f'<span class="q-good">{refunded} refunded</span>')
    if pursue:
        bits.append(f'<span class="q-warn">{pursue} pursuing</span>')
    return " · ".join(bits)


def _account_details(c: dict[str, Any]) -> str:
    """The operational info grid: contact, address, credentials, ids, session."""
    rows = []

    def field(label: str, value: str, *, copy: bool = False,
              mono: bool = True, secret: bool = False) -> str:
        v = value or "—"
        cls = "av mono" if mono else "av"
        if v == "—":
            return (f'<div class="af"><div class="ak">{esc(label)}</div>'
                    f'<div class="{cls} dim">—</div></div>')
        safe = esc(v)
        attr = f' data-copy="{safe}"' if copy else ""
        inner = safe
        if secret:
            inner = (f'<span class="secret" data-val="{safe}">••••••••</span>'
                     f'<button class="reveal" onclick="dmReveal(this)" '
                     f'title="Reveal">show</button>')
        copybtn = (f'<button class="copy" onclick="dmCopy(this)"{attr} '
                   f'title="Copy">⧉</button>') if copy else ""
        return (f'<div class="af"><div class="ak">{esc(label)}</div>'
                f'<div class="{cls}">{inner}{copybtn}</div></div>')

    copy_id = c.get("_copy_id", "")
    token = c.get("number_token") or ""
    token_short = (token[:12] + "…") if len(token) > 12 else token

    rows.append(field("Copy ID", copy_id, copy=True))
    rows.append(field("Phone", c.get("phone") or "", copy=True))
    rows.append(field("Email", c.get("email") or "", copy=True))
    rows.append(field("Address", _address(c), copy=True, mono=False))
    rows.append(field("Password", c.get("password") or "", copy=True,
                      secret=True))
    rows.append(field("api.cc token", token_short, copy=False, secret=bool(token)))
    rows.append(_session_field(c))
    rows.append(field("Added", _date_only(c.get("created_at"))))
    return "\n".join(rows)


def _proof_rel(path: str) -> str:
    """Relative href from the report (data/reports/) to a screenshot.

    Screenshots live at data/screenshots/<bucket>/...; the report sits at
    data/reports/<bucket>.html. So the link is ../screenshots/<bucket>/file.
    Falls back to the raw path if it isn't under data/screenshots.
    """
    p = str(path or "").replace("\\", "/")
    marker = "/screenshots/"
    i = p.find(marker)
    if i != -1:
        return "../screenshots/" + p[i + len(marker):]
    return p


def _proof_row(shots: list[dict[str, Any]]) -> str:
    """A 'Proof' strip of clickable screenshot thumbnails for one customer."""
    if not shots:
        return ('<div class="proof"><div class="proof-key">Proof</div>'
                '<div class="proof-none">no screenshots yet '
                '(captured on the next refund check)</div></div>')
    thumbs = []
    for s in shots:
        href = esc(_proof_rel(s.get("path", "")))
        label = esc(s.get("label") or s.get("kind") or "shot")
        kind = esc(s.get("kind") or "")
        thumbs.append(
            f'<a class="thumb thumb--{kind}" href="{href}" target="_blank" '
            f'title="{label}">'
            f'<img loading="lazy" src="{href}" alt="{label}">'
            f'<span class="thumb-cap">{label}</span></a>')
    return (f'<div class="proof"><div class="proof-key">Proof '
            f'<span class="proof-n">{len(shots)}</span></div>'
            f'<div class="proof-strip">{"".join(thumbs)}</div></div>')


def _session_field(c: dict[str, Any]) -> str:
    st = c.get("session_status", "")
    pill = _session_pill(st) or '<span class="dim">—</span>'
    return (f'<div class="af"><div class="ak">Session</div>'
            f'<div class="av">{pill}</div></div>')


def resolution_method(o: dict[str, Any]) -> tuple[str, str]:
    """Pure: HOW was this order resolved? Returns (label, confirmation).

    Derived from the order's claims + chats (no extra DB column needed):
      - a confirmed claim          → "Self-claim"
      - a successful chat that reached an agent → "Agent chat" (covers the
        credits→card conversion and ordinary refund pursuit)
      - a successful chat, no agent (bot self-serve) → "Self-serve chat"
      - refunded with neither       → "Already refunded"
      - still pursuing              → "Pending"
    The confirmation string surfaces the proof (claim amount / the agent's
    confirming line) so the table shows WHY we trust it.
    """
    status = o.get("refund_status", "unchecked")
    claims = o.get("claims") or []
    chats = o.get("chats") or []
    won_claim = next((c for c in claims if c.get("confirmed")), None)
    won_chat = next((ch for ch in chats if ch.get("outcome") == "success"),
                    None)

    # Last inbound agent line (the promise / confirmation), if any chat won.
    chat_conf = ""
    if won_chat is not None:
        for m in reversed(won_chat.get("messages", [])):
            if m.get("direction") == "in":
                chat_conf = (m.get("content") or "")[:140]
                break

    # ZERO-TOLERANCE display rule: an affirmative "this got refunded via X"
    # label is only honest once the receipt PROVED it (status == "refunded").
    # An `unconfirmed` order (agent promised, or a claim we couldn't verify to
    # the card) must NOT read as resolved — it shows "Pending" with the promise
    # as context, matching the ⚠ Unconfirmed status badge beside it.
    if status == "unconfirmed":
        if won_chat is not None:
            return ("Pending — agent promised", chat_conf
                    or "awaiting receipt proof")
        if won_claim is not None:
            return ("Pending — claim unverified",
                    "claim ran; card destination not yet proven on receipt")
        return ("Pending — unconfirmed", "awaiting receipt proof")

    if status == "refunded":
        if won_chat is not None:
            if won_chat.get("agent_reached"):
                lo = chat_conf.lower()
                if "credit" in lo or "exchanged" in lo:
                    return ("Credits→card (agent chat)", chat_conf)
                return ("Agent chat", chat_conf)
            return ("Self-serve chat", chat_conf)
        if won_claim is not None:
            amt = won_claim.get("amount")
            dest = ("to original card" if won_claim.get("to_original_payment")
                    else "refunded")
            return ("Self-claim", f"{_money(amt)} {dest}")
        return ("Already refunded", "receipt shows refund to original card")

    if status in ("not_refunded", "partial", "pending_claim", "remake"):
        return ("Pending", "not yet resolved")
    return ("—", "")


def _breakdown_table(orders: list[dict[str, Any]]) -> str:
    """A per-customer order breakdown: row per order + totals footer."""
    rows = []
    total_refunded = 0.0
    n_ref = 0
    for i, o in enumerate(orders, 1):
        store = esc(o.get("store_name") or "Order")
        amt = o.get("price")
        amt_s = _money(amt)
        date = esc(_date_only(o.get("last_checked_at")) or "—")
        st = o.get("refund_status", "unchecked")
        refunded = st == "refunded"
        if refunded and o.get("refund_amount") is not None:
            total_refunded += float(o["refund_amount"])
            n_ref += 1
        elif refunded and amt is not None:
            total_refunded += float(amt)
            n_ref += 1
        check = ('<span class="bk-yes">✓ refunded</span>' if refunded
                 else _refund_pill(st))
        method, conf = resolution_method(o)
        conf_html = (f'<span class="bk-conf" title="{esc(conf)}">{esc(conf)}'
                     f'</span>') if conf else "—"
        rows.append(
            f'<tr><td class="bk-n">{i}</td><td>{store}</td>'
            f'<td class="bk-amt mono">{amt_s}</td>'
            f'<td class="bk-date mono">{date}</td>'
            f'<td>{check}</td><td class="bk-method">{esc(method)}</td>'
            f'<td class="bk-confcell">{conf_html}</td></tr>')
    foot = (f'<tr class="bk-foot"><td></td><td>{len(orders)} order'
            f'{"s" if len(orders) != 1 else ""}</td>'
            f'<td class="bk-amt mono">—</td><td></td>'
            f'<td class="mono">{n_ref}/{len(orders)}</td>'
            f'<td colspan="2" class="bk-total mono">'
            f'{_money(total_refunded)} refunded to card</td></tr>')
    return (
        '<table class="breakdown"><thead><tr>'
        '<th>#</th><th>Store</th><th>Amount</th><th>Checked</th>'
        '<th>Refunded</th><th>Method</th><th>Confirmation</th>'
        f'</tr></thead><tbody>{"".join(rows)}{foot}</tbody></table>')


def _order_block(o: dict[str, Any]) -> str:
    store = esc(o.get("store_name") or "Order")
    desc = esc(o.get("description") or "")
    price = _money(o.get("price"))
    pill = _refund_pill(o.get("refund_status", "unchecked"))
    refunded_amt = o.get("refund_amount")
    total = o.get("total_amount")
    money_line = ""
    if refunded_amt is not None and total is not None:
        money_line = (f'<span class="amt-detail">refund '
                      f'{_money(refunded_amt)} of {_money(total)}</span>')

    claims_html = "".join(_claim_row(cl) for cl in o.get("claims", []))
    chats_html = "".join(_chat_block(ch) for ch in o.get("chats", []))
    trail = ""
    if claims_html or chats_html:
        trail = f'<div class="order-trail">{claims_html}{chats_html}</div>'

    meta = " · ".join(x for x in [desc, money_line] if x)
    meta_html = f'<div class="order-meta">{meta}</div>' if meta else ""

    return f"""<section class="order">
  <div class="order-head">
    <div class="order-store">
      <span class="order-name">{store}</span>
      {meta_html}
    </div>
    <div class="order-right">
      <span class="order-price">{price}</span>
      {pill}
    </div>
  </div>
  {trail}
</section>"""


def _claim_row(cl: dict[str, Any]) -> str:
    confirmed = bool(cl.get("confirmed"))
    to_orig = bool(cl.get("to_original_payment"))
    amt = _money(cl.get("amount"))
    if confirmed and to_orig:
        tone, text = "good", f"Self-claim confirmed · {amt} to original card"
    elif confirmed:
        tone, text = "warn", f"Self-claim confirmed · {amt} (verify destination)"
    else:
        outcome = esc(cl.get("outcome") or "attempted")
        tone, text = "muted", f"Self-claim {outcome} · {amt}"
    err = cl.get("error")
    err_html = f'<div class="trail-err">{esc(err)}</div>' if err else ""
    return (f'<div class="trail-item trail-item--{tone}">'
            f'<span class="trail-glyph">⟳</span>'
            f'<div class="trail-text">{text}{err_html}</div></div>')


def _chat_block(ch: dict[str, Any]) -> str:
    outcome = ch.get("outcome") or "open"
    agent = bool(ch.get("agent_reached"))
    attempt = ch.get("attempt_no", 1)
    tone = {"success": "good", "failed": "alert", "blocked": "alert",
            "manual_flag": "warn", "review_blocked": "warn"}.get(
                outcome, "muted")
    head = (f'attempt {attempt} · '
            f'{"reached a human" if agent else "no human reached"} · '
            f'{esc(outcome)}')

    bubbles = []
    for m in ch.get("messages", []):
        d = m.get("direction", "system")
        content = esc(m.get("content") or "")
        if d == "out":
            cls, who = "bubble bubble--out", "us"
        elif d == "in":
            cls, who = "bubble bubble--in", "support"
        else:
            cls, who = "bubble bubble--sys", "sys"
        bubbles.append(
            f'<div class="{cls}"><span class="bubble-who">{who}</span>'
            f'<span class="bubble-text">{content}</span></div>')
    transcript = ("".join(bubbles)
                  or '<div class="bubble bubble--sys">'
                     '<span class="bubble-text">No messages captured.</span>'
                     '</div>')

    return f"""<details class="chat chat--{tone}">
  <summary class="chat-head"><span class="chat-tag">transcript</span>{head}
    <span class="chevron sm">▾</span></summary>
  <div class="chat-thread">{transcript}</div>
</details>"""


def _empty_state() -> str:
    return ('<div class="page-empty">'
            '<div class="page-empty-mark">◷</div>'
            '<div class="page-empty-title">Nothing on the board yet</div>'
            '<div class="subtle">Create customers or run a refund check to '
            'populate today\'s worklog.</div></div>')


# ── rendering: index ─────────────────────────────────────────────────────────


def render_index(model: dict[str, Any]) -> str:
    """Pure: index model -> the all-reports landing page."""
    entries = model["entries"]
    if entries:
        cards = "\n".join(
            f'<a class="day" href="{esc(e["file"])}">'
            f'<div class="day-date">{esc(e["pretty"])}</div>'
            f'<div class="day-bucket mono">{esc(e["bucket"])}</div>'
            f'<div class="day-meta">'
            f'<span class="day-count">{e["customers"]} customer'
            f'{"s" if e["customers"] != 1 else ""}</span>'
            f'<span class="day-active">{e["active"]} live</span></div>'
            f'</a>' for e in entries)
    else:
        cards = ('<div class="page-empty"><div class="page-empty-mark">◷</div>'
                 '<div class="page-empty-title">No reports yet</div></div>')

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DashManager · Reports</title>
{_STYLE}
</head>
<body>
<div class="shell">
  <header class="masthead">
    <div class="brand">
      <span class="brand-dot"></span>
      <span class="brand-name">DashManager</span>
    </div>
    <div class="masthead-meta">
      <div class="eyebrow">Refund worklog</div>
      <h1 class="title">All reports</h1>
      <div class="subtle">Updated {esc(model['generated_at'])}</div>
    </div>
  </header>
  <main class="days">
    {cards}
  </main>
  <footer class="footer"><span>DashManager — local refund audit.</span></footer>
</div>
</body>
</html>"""


# ── small pure helpers ───────────────────────────────────────────────────────


def _refund_pill(status: str) -> str:
    spec = {
        "refunded": ("good", "Refunded"),
        "not_refunded": ("alert", "Not refunded"),
        "partial": ("warn", "Partial"),
        "pending_claim": ("warn", "Claimable"),
        "remake": ("warn", "Remake"),
        "unconfirmed": ("alert", "⚠ Unconfirmed"),
        "unknown": ("muted", "Unknown"),
        "unchecked": ("muted", "Unchecked"),
    }
    tone, label = spec.get(status, ("muted", status or "Unchecked"))
    return f'<span class="pill pill--{tone}">{esc(label)}</span>'


def _session_pill(status: str) -> str:
    if not status:
        return ""
    spec = {"active": ("good", "session ok"),
            "expired": ("warn", "session expired"),
            "invalid": ("alert", "session invalid")}
    tone, label = spec.get(status, ("muted", status))
    return f'<span class="pill pill--{tone} pill--soft">{esc(label)}</span>'


def _money(v: Any) -> str:
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return "—"


def _full_name(c: dict[str, Any]) -> str:
    return f"{c.get('first_name', '')} {c.get('last_name', '')}".strip()


def _initials(c: dict[str, Any]) -> str:
    f = (c.get("first_name") or "").strip()
    l = (c.get("last_name") or "").strip()
    out = (f[:1] + l[:1]).upper()
    return out or "•"


def _short_id(c: dict[str, Any], bucket: str, seq: int) -> str:
    """The compact copy-paste id like CustomerDaisy's '06-13 1 Kelly'."""
    mmdd = bucket[5:] if len(bucket) >= 10 else bucket
    return f"{mmdd} {seq} {c.get('first_name','')}".strip()


def _address(c: dict[str, Any]) -> str:
    """Pull a human address out of the free-text notes field if present."""
    notes = (c.get("notes") or "").strip()
    if not notes:
        return "—"
    parts = [p.strip() for p in notes.split("·")]
    for p in parts:
        low = p.lower()
        if p and not low.startswith(("created via", "daisy:", "no-orders",
                                     "imported from customerdaisy", "imported",
                                     "adopted")):
            return p
    # the address often follows "imported from CustomerDaisy"
    for p in parts:
        if any(ch.isdigit() for ch in p) and "," in p:
            return p
    return parts[-1] if parts else "—"


def _date_only(v: Any) -> str:
    s = str(v or "")
    return s[:10] if len(s) >= 10 else (s or "—")


def esc(v: Any) -> str:
    return html.escape("" if v is None else str(v))


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _now_human() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _pretty_date(iso: str) -> str:
    try:
        d = datetime.strptime(iso, "%Y-%m-%d")
        return d.strftime("%A, %B ") + str(d.day) + d.strftime(", %Y")
    except ValueError:
        return iso


# ── inline script (self-contained, offline) ─────────────────────────────────

_SCRIPT = """<script>
function dmToast(msg){var t=document.getElementById('dmToast');if(!t)return;
  t.textContent=msg||'Copied';t.classList.add('show');
  clearTimeout(window._dmt);window._dmt=setTimeout(function(){
    t.classList.remove('show');},1200);}
function dmCopy(btn){var v=btn.getAttribute('data-copy')||
  (btn.parentNode&&btn.parentNode.textContent||'').trim();
  navigator.clipboard&&navigator.clipboard.writeText(v).then(
    function(){dmToast('Copied: '+v.slice(0,40));},
    function(){dmToast('Copy failed');});}
function dmReveal(btn){var s=btn.parentNode.querySelector('.secret');
  if(!s)return;var v=s.getAttribute('data-val')||'';
  if(s.dataset.shown==='1'){s.textContent='\\u2022\\u2022\\u2022\\u2022\\u2022\\u2022\\u2022\\u2022';
    s.dataset.shown='0';btn.textContent='show';}
  else{s.textContent=v;s.dataset.shown='1';btn.textContent='hide';}}
function dmAll(open){document.querySelectorAll('details.customer').forEach(
  function(d){d.open=open;});}
</script>"""


# ── the stylesheet ───────────────────────────────────────────────────────────

_STYLE = """<style>
  :root {
    --bg: #0f0e0c; --surface: #1c1a17; --surface-2: #232019; --line: #2e2a24;
    --ink: #f4efe7; --ink-soft: #a8a097; --ink-mute: #6f685f;
    --red: #ef2b1f; --red-soft: #ff5a4d;
    --good: #4ec98a; --warn: #e9b04b; --alert: #ff6b5e;
    --radius: 16px;
    --mono: ui-monospace, "SF Mono", "JetBrains Mono", "Cascadia Code", monospace;
    --sans: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }
  @media (prefers-color-scheme: light) {
    :root { --bg: #f5f2ec; --surface: #fffdf9; --surface-2: #f0ece3;
      --line: #e3ddd1; --ink: #1c1916; --ink-soft: #5f574d; --ink-mute: #978d80; }
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--ink);
    font-family: var(--sans); font-feature-settings: "ss01","cv05","tnum";
    line-height: 1.5; letter-spacing: -0.01em;
    background-image:
      radial-gradient(120% 90% at 100% 0%, rgba(239,43,31,0.10), transparent 55%),
      radial-gradient(90% 70% at 0% 0%, rgba(239,43,31,0.05), transparent 50%); }
  a { color: inherit; text-decoration: none; }
  .shell { max-width: 980px; margin: 0 auto; padding: 52px 28px 80px; }

  /* masthead */
  .masthead { display: flex; flex-direction: column; gap: 26px; margin-bottom: 36px; }
  .brand { display: flex; align-items: center; justify-content: space-between; }
  .brand-link { display: inline-flex; align-items: center; gap: 9px; }
  .brand-dot { width: 9px; height: 9px; border-radius: 50%; background: var(--red);
    box-shadow: 0 0 0 4px rgba(239,43,31,0.18); }
  .brand-name { font-weight: 600; font-size: 0.82rem; letter-spacing: 0.16em;
    text-transform: uppercase; color: var(--ink-soft); }
  .index-link { font-size: 0.78rem; color: var(--ink-mute); }
  .index-link:hover { color: var(--red-soft); }
  .eyebrow { font-size: 0.74rem; letter-spacing: 0.18em; text-transform: uppercase;
    color: var(--red-soft); font-weight: 600; margin-bottom: 10px; }
  .title { margin: 0; font-size: clamp(1.9rem, 4vw, 2.9rem); font-weight: 680;
    letter-spacing: -0.03em; line-height: 1.02; }
  .subtle { color: var(--ink-mute); font-size: 0.86rem; }
  .masthead-meta .subtle { margin-top: 8px; }

  /* summary cards */
  .cards { display: grid; grid-template-columns: repeat(6, 1fr); gap: 11px;
    margin-bottom: 28px; }
  .card { background: var(--surface); border: 1px solid var(--line);
    border-radius: var(--radius); padding: 16px 14px; position: relative;
    overflow: hidden; }
  .card::after { content: ""; position: absolute; inset: 0 0 auto 0; height: 2px;
    background: var(--line); }
  .card--good::after { background: var(--good); }
  .card--warn::after { background: var(--warn); }
  .card--alert::after { background: var(--alert); }
  .card--muted::after { background: transparent; }
  .card-value { font-family: var(--mono); font-size: 1.7rem; font-weight: 600;
    letter-spacing: -0.04em; line-height: 1; }
  .card-label { margin-top: 7px; font-size: 0.68rem; letter-spacing: 0.09em;
    text-transform: uppercase; color: var(--ink-mute); }
  @media (max-width: 820px) { .cards { grid-template-columns: repeat(3, 1fr); } }
  @media (max-width: 520px) { .cards { grid-template-columns: repeat(2, 1fr); } }

  /* toolbar */
  .toolbar { display: flex; gap: 8px; margin-bottom: 16px; }
  .tbtn { background: var(--surface); border: 1px solid var(--line);
    color: var(--ink-soft); font-size: 0.74rem; padding: 6px 12px;
    border-radius: 8px; cursor: pointer; font-family: var(--sans); }
  .tbtn:hover { color: var(--ink); border-color: var(--ink-mute); }

  /* customer (collapsible) */
  .roster { display: flex; flex-direction: column; gap: 16px; }
  .customer { background: linear-gradient(180deg, var(--surface), var(--bg));
    border: 1px solid var(--line); border-radius: 20px; overflow: hidden; }
  .customer[open] { border-color: #3a352d; }
  .customer-head { display: flex; align-items: center; gap: 14px;
    padding: 18px 22px; cursor: pointer; list-style: none; user-select: none; }
  .customer-head::-webkit-details-marker { display: none; }
  .seq { font-family: var(--mono); font-size: 0.78rem; color: var(--ink-mute);
    width: 18px; text-align: right; flex: 0 0 auto; }
  .avatar { flex: 0 0 auto; width: 42px; height: 42px; border-radius: 13px;
    display: grid; place-items: center; font-weight: 650; font-size: 0.9rem;
    color: var(--ink); background: var(--surface-2); border: 1px solid var(--line); }
  .customer-id { flex: 1 1 auto; min-width: 0; display: flex;
    flex-direction: column; gap: 3px; }
  .customer-name { font-size: 1.08rem; font-weight: 640; letter-spacing: -0.02em;
    display: flex; align-items: center; gap: 9px; flex-wrap: wrap; }
  .customer-quick { font-size: 0.82rem; color: var(--ink-mute); }
  .q-good { color: var(--good); } .q-warn { color: var(--warn); }
  .q-mut { color: var(--ink-mute); }
  .chevron { color: var(--ink-mute); font-size: 0.85rem; transition: transform .2s;
    flex: 0 0 auto; }
  .customer[open] > .customer-head .chevron { transform: rotate(180deg); }
  .chevron.sm { margin-left: auto; }
  .customer-inner { padding: 0 22px 20px; }

  /* account detail grid */
  .account-grid { display: grid; grid-template-columns: repeat(2, 1fr);
    gap: 1px; background: var(--line); border: 1px solid var(--line);
    border-radius: 14px; overflow: hidden; margin-bottom: 18px; }
  @media (max-width: 640px) { .account-grid { grid-template-columns: 1fr; } }
  .af { background: var(--surface); padding: 11px 14px; min-width: 0; }
  .ak { font-size: 0.64rem; letter-spacing: 0.1em; text-transform: uppercase;
    color: var(--ink-mute); margin-bottom: 4px; }
  .av { font-size: 0.9rem; display: flex; align-items: center; gap: 8px;
    word-break: break-word; }
  .av.mono { font-family: var(--mono); letter-spacing: -0.02em; font-size: 0.85rem; }
  .av.dim, .dim { color: var(--ink-mute); }
  .copy, .reveal { background: transparent; border: 1px solid var(--line);
    color: var(--ink-mute); border-radius: 6px; cursor: pointer; flex: 0 0 auto;
    font-family: var(--sans); }
  .copy { padding: 1px 6px; font-size: 0.82rem; }
  .reveal { padding: 1px 7px; font-size: 0.66rem; text-transform: uppercase;
    letter-spacing: 0.06em; }
  .copy:hover, .reveal:hover { color: var(--ink); border-color: var(--red-soft); }
  .secret { font-family: var(--mono); letter-spacing: 0.1em; }

  /* proof strip */
  .proof { margin-bottom: 18px; }
  .proof-key { font-size: 0.64rem; letter-spacing: 0.1em; text-transform: uppercase;
    color: var(--ink-mute); margin-bottom: 8px; display: flex; align-items: center;
    gap: 7px; }
  .proof-n { background: var(--surface-2); border: 1px solid var(--line);
    border-radius: 999px; padding: 0 7px; font-size: 0.62rem; color: var(--ink-soft); }
  .proof-none { font-size: 0.82rem; color: var(--ink-mute); }
  .proof-strip { display: flex; gap: 10px; flex-wrap: wrap; }
  .thumb { display: block; width: 150px; border: 1px solid var(--line);
    border-radius: 10px; overflow: hidden; background: var(--surface-2);
    transition: border-color .15s, transform .15s; }
  .thumb:hover { border-color: var(--red-soft); transform: translateY(-2px); }
  .thumb img { width: 100%; height: 92px; object-fit: cover; object-position: top;
    display: block; }
  .thumb-cap { display: block; padding: 5px 8px; font-size: 0.68rem;
    color: var(--ink-soft); white-space: nowrap; overflow: hidden;
    text-overflow: ellipsis; }
  .thumb--receipt { border-color: rgba(78,201,138,0.25); }

  /* breakdown table */
  .breakdown { width: 100%; border-collapse: collapse; margin-bottom: 18px;
    font-size: 0.84rem; border: 1px solid var(--line); border-radius: 12px;
    overflow: hidden; }
  .breakdown thead th { text-align: left; font-size: 0.62rem;
    letter-spacing: 0.09em; text-transform: uppercase; color: var(--ink-mute);
    padding: 9px 12px; background: var(--surface-2);
    border-bottom: 1px solid var(--line); white-space: nowrap; }
  .breakdown td { padding: 9px 12px; border-bottom: 1px solid var(--line);
    vertical-align: top; }
  .breakdown tbody tr:last-child td { border-bottom: none; }
  .bk-n { color: var(--ink-mute); font-family: var(--mono); width: 24px; }
  .bk-amt { font-weight: 600; white-space: nowrap; }
  .bk-date { color: var(--ink-mute); white-space: nowrap; }
  .bk-yes { color: var(--good); font-weight: 600; white-space: nowrap; }
  .bk-method { color: var(--ink-soft); white-space: nowrap; }
  .bk-confcell { color: var(--ink-mute); font-size: 0.78rem; max-width: 240px; }
  .bk-conf { display: inline-block; max-width: 240px; overflow: hidden;
    text-overflow: ellipsis; white-space: nowrap; vertical-align: bottom; }
  .bk-foot td { border-top: 1px solid var(--line); font-weight: 600;
    background: var(--surface-2); }
  .bk-total { color: var(--good); text-align: right; }

  .customer-body { display: flex; flex-direction: column; }

  /* order */
  .order { padding: 15px 0; border-bottom: 1px solid var(--line); }
  .order:last-child { border-bottom: none; }
  .order:first-child { padding-top: 0; }
  .order-head { display: flex; justify-content: space-between; gap: 16px;
    align-items: flex-start; }
  .order-name { font-weight: 600; font-size: 1rem; }
  .order-meta { color: var(--ink-mute); font-size: 0.82rem; margin-top: 3px; }
  .amt-detail { font-family: var(--mono); }
  .order-right { display: flex; align-items: center; gap: 12px; flex: 0 0 auto; }
  .order-price { font-family: var(--mono); font-weight: 600; font-size: 0.98rem;
    letter-spacing: -0.03em; }

  /* pills */
  .pill { display: inline-flex; align-items: center; gap: 6px; font-size: 0.72rem;
    font-weight: 600; letter-spacing: 0.02em; padding: 4px 11px; border-radius: 999px;
    border: 1px solid transparent; white-space: nowrap; }
  .pill::before { content: ""; width: 6px; height: 6px; border-radius: 50%;
    background: currentColor; opacity: 0.9; }
  .pill--good { color: var(--good); background: rgba(78,201,138,0.10);
    border-color: rgba(78,201,138,0.25); }
  .pill--warn { color: var(--warn); background: rgba(233,176,75,0.10);
    border-color: rgba(233,176,75,0.25); }
  .pill--alert { color: var(--alert); background: rgba(255,107,94,0.10);
    border-color: rgba(255,107,94,0.25); }
  .pill--muted { color: var(--ink-mute); background: rgba(120,110,100,0.10);
    border-color: var(--line); }
  .pill--soft { font-weight: 500; font-size: 0.64rem; padding: 2px 9px; }
  .pill--soft::before { display: none; }

  /* claim + chat trail */
  .order-trail { margin-top: 13px; display: flex; flex-direction: column; gap: 10px; }
  .trail-item { display: flex; gap: 11px; align-items: flex-start; padding: 11px 14px;
    border-radius: 12px; background: var(--surface-2); border: 1px solid var(--line);
    font-size: 0.86rem; }
  .trail-glyph { font-family: var(--mono); opacity: 0.7; }
  .trail-item--good { border-color: rgba(78,201,138,0.30); }
  .trail-item--good .trail-glyph { color: var(--good); }
  .trail-item--warn .trail-glyph { color: var(--warn); }
  .trail-err { color: var(--alert); font-size: 0.8rem; margin-top: 4px; }

  .chat { border: 1px solid var(--line); border-radius: 14px; overflow: hidden;
    background: var(--surface-2); }
  .chat-head { padding: 10px 14px; font-size: 0.76rem; color: var(--ink-soft);
    border-bottom: 1px solid transparent; display: flex; align-items: center;
    gap: 10px; cursor: pointer; list-style: none; }
  .chat-head::-webkit-details-marker { display: none; }
  .chat[open] .chat-head { border-bottom-color: var(--line); }
  .chat-tag { font-size: 0.62rem; letter-spacing: 0.12em; text-transform: uppercase;
    color: var(--ink-mute); border: 1px solid var(--line); padding: 2px 7px;
    border-radius: 6px; }
  .chat--good .chat-head { color: var(--good); }
  .chat--alert .chat-head { color: var(--alert); }
  .chat--warn .chat-head { color: var(--warn); }
  .chat-thread { padding: 14px; display: flex; flex-direction: column; gap: 8px; }
  .bubble { max-width: 78%; padding: 9px 13px; border-radius: 14px; font-size: 0.88rem;
    display: flex; flex-direction: column; gap: 3px; line-height: 1.42; }
  .bubble-who { font-size: 0.6rem; letter-spacing: 0.1em; text-transform: uppercase;
    opacity: 0.6; }
  .bubble--out { align-self: flex-end; background: var(--red); color: #fff;
    border-bottom-right-radius: 4px; }
  .bubble--out .bubble-who { color: rgba(255,255,255,0.75); }
  .bubble--in { align-self: flex-start; background: var(--surface);
    border: 1px solid var(--line); border-bottom-left-radius: 4px; }
  .bubble--sys { align-self: center; background: transparent; color: var(--ink-mute);
    font-size: 0.78rem; max-width: 100%; text-align: center; }

  /* empty states */
  .no-orders { display: flex; gap: 14px; align-items: center; padding: 6px 0; }
  .no-orders-mark { font-size: 1.4rem; color: var(--ink-mute); width: 40px;
    height: 40px; display: grid; place-items: center; border: 1px dashed var(--line);
    border-radius: 12px; flex: 0 0 auto; }
  .no-orders-title { font-weight: 600; }
  .page-empty { text-align: center; padding: 80px 20px; }
  .page-empty-mark { font-size: 2.4rem; color: var(--ink-mute); }
  .page-empty-title { font-size: 1.2rem; font-weight: 640; margin-top: 12px; }

  /* index */
  .days { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px,1fr));
    gap: 14px; }
  .day { background: linear-gradient(180deg, var(--surface), var(--bg));
    border: 1px solid var(--line); border-radius: 18px; padding: 20px;
    transition: border-color .15s, transform .15s; display: block; }
  .day:hover { border-color: var(--red-soft); transform: translateY(-2px); }
  .day-date { font-weight: 640; font-size: 1.02rem; letter-spacing: -0.02em; }
  .day-bucket { color: var(--ink-mute); font-size: 0.78rem; margin-top: 3px; }
  .day-meta { display: flex; gap: 12px; margin-top: 14px; font-size: 0.8rem; }
  .day-count { color: var(--ink-soft); }
  .day-active { color: var(--good); }

  .footer { margin-top: 44px; padding-top: 20px; border-top: 1px solid var(--line);
    color: var(--ink-mute); font-size: 0.78rem; text-align: center; }

  /* toast */
  .toast { position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%)
      translateY(20px); background: var(--ink); color: var(--bg); font-size: 0.82rem;
    font-weight: 600; padding: 9px 16px; border-radius: 10px; opacity: 0;
    pointer-events: none; transition: opacity .2s, transform .2s; max-width: 80vw;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .toast.show { opacity: 1; transform: translateX(-50%) translateY(0); }
  .mono { font-family: var(--mono); }

  @media print {
    body { background: #fff; color: #111; }
    .customer, .card, .chat, .day { break-inside: avoid; }
    .toolbar, .copy, .reveal, .toast, .index-link { display: none; }
    details { open: true; }
  }
</style>"""
