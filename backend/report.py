"""Daily working-report generator — a self-contained, hand-designed HTML file.

One report per day at ``data/reports/YYYY-MM-DD.html``. It is the shared spine
between the ``/dash-create`` and ``/dash-refunds`` skills: create writes the
customer roster, refunds writes order outcomes + chat transcripts onto the same
file. Re-runnable and idempotent — it always rebuilds the whole day from the DB,
so either skill (or a fresh session) can regenerate the current state at will.

Design intent (no template-slop): a calm near-black/parchment surface, a single
DoorDash-red accent, real type hierarchy (display + grotesk + mono for data),
status as quiet pills, transcripts as honest chat bubbles. Zero JS, zero CDN —
opens straight from disk, prints clean, survives offline.
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
    """Rebuild the whole day's report from the DB and write it to disk.

    `report_date` is a 'YYYY-MM-DD' bucket; defaults to today (UTC). Customers
    are selected by their `bucket_date`. Returns the written file path.
    """
    report_date = report_date or _today()
    out_dir = out_dir or (config.DATA_DIR / "reports")
    out_dir.mkdir(parents=True, exist_ok=True)

    model = await _collect(report_date)
    html_doc = render_report(model)
    out_path = out_dir / f"{report_date}.html"
    out_path.write_text(html_doc, encoding="utf-8")
    return out_path


async def _collect(report_date: str) -> dict[str, Any]:
    """Pull every customer in the bucket plus their orders, claims, chats."""
    customers = [c for c in await db.list_customers()
                 if c.get("bucket_date") == report_date]

    rows: list[dict[str, Any]] = []
    for c in customers:
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
        rows.append({**c, "orders": enriched_orders})

    return {
        "date": report_date,
        "generated_at": _now_human(),
        "customers": rows,
        "summary": _summarize(rows),
    }


def _summarize(rows: list[dict[str, Any]]) -> dict[str, int]:
    s = {"customers": len(rows), "orders": 0, "refunded": 0,
         "pursuing": 0, "no_orders": 0, "needs_you": 0}
    for c in rows:
        orders = c["orders"]
        if not orders:
            s["no_orders"] += 1
        for o in orders:
            s["orders"] += 1
            st = o.get("refund_status", "unchecked")
            if st == "refunded":
                s["refunded"] += 1
            elif st in ("not_refunded", "partial", "pending_claim", "remake"):
                s["pursuing"] += 1
            # a chat that ended needing a human, or an unresolved pursuit
            if _order_needs_you(o):
                s["needs_you"] += 1
    return s


def _order_needs_you(o: dict[str, Any]) -> bool:
    """True when an order is unresolved in a way the user should glance at."""
    st = o.get("refund_status", "unchecked")
    if st == "refunded":
        return False
    if st in ("not_refunded", "partial", "pending_claim", "remake", "unknown"):
        # if the latest chat won, it's effectively resolved
        for ch in o.get("chats", []):
            if ch.get("outcome") == "success":
                return False
        return True
    return False


# ── rendering ────────────────────────────────────────────────────────────────


def render_report(model: dict[str, Any]) -> str:
    """Pure: model dict -> full HTML document string (no I/O, unit-testable)."""
    date = esc(model["date"])
    pretty_date = _pretty_date(model["date"])
    summary = model["summary"]
    cards = _summary_cards(summary)
    customers_html = "\n".join(_customer_section(c) for c in model["customers"])
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
      <span class="brand-dot"></span>
      <span class="brand-name">DashManager</span>
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

  <main class="roster">
    {customers_html}
  </main>

  <footer class="footer">
    <span>DashManager — local refund audit · all amounts pursued to the original payment method, never store credit.</span>
  </footer>
</div>
</body>
</html>"""


def _summary_cards(s: dict[str, int]) -> str:
    items = [
        ("Customers", s["customers"], "neutral"),
        ("Orders", s["orders"], "neutral"),
        ("Refunded", s["refunded"], "good"),
        ("Pursuing", s["pursuing"], "warn"),
        ("Needs you", s["needs_you"], "alert" if s["needs_you"] else "muted"),
    ]
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
    phone = esc(c.get("phone") or "—")
    email = esc(c.get("email") or "—")
    address = esc(_address(c))
    session = _session_pill(c.get("session_status", ""))
    orders = c["orders"]

    if not orders:
        body = ('<div class="no-orders">'
                '<span class="no-orders-mark">∅</span>'
                '<div><div class="no-orders-title">No orders to audit</div>'
                '<div class="subtle">Account has no DoorDash order history yet.</div>'
                '</div></div>')
    else:
        body = "\n".join(_order_block(o) for o in orders)

    return f"""<article class="customer">
  <div class="customer-head">
    <div class="avatar">{initials}</div>
    <div class="customer-id">
      <div class="customer-name">{name} {session}</div>
      <div class="customer-contact">
        <span class="contact"><span class="contact-key">tel</span>{phone}</span>
        <span class="contact"><span class="contact-key">email</span>{email}</span>
      </div>
      <div class="customer-addr">{address}</div>
    </div>
  </div>
  <div class="customer-body">
    {body}
  </div>
</article>"""


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
            "manual_flag": "warn", "review_blocked": "warn"}.get(outcome, "muted")
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
                     '<span class="bubble-text">No messages captured.</span></div>')

    return f"""<div class="chat chat--{tone}">
  <div class="chat-head"><span class="chat-tag">transcript</span>{head}</div>
  <div class="chat-thread">{transcript}</div>
</div>"""


def _empty_state() -> str:
    return ('<div class="page-empty">'
            '<div class="page-empty-mark">◷</div>'
            '<div class="page-empty-title">Nothing on the board yet</div>'
            '<div class="subtle">Create customers or run a refund check to '
            'populate today\'s worklog.</div></div>')


# ── small pure helpers ───────────────────────────────────────────────────────


def _refund_pill(status: str) -> str:
    spec = {
        "refunded": ("good", "Refunded"),
        "not_refunded": ("alert", "Not refunded"),
        "partial": ("warn", "Partial"),
        "pending_claim": ("warn", "Claimable"),
        "remake": ("warn", "Remake"),
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


def _address(c: dict[str, Any]) -> str:
    """Pull a human address out of the free-text notes field if present.

    create_account stores notes like "created via signup · <full address> ·
    daisy:<id>". The middle segment is the address; fall back to the raw notes.
    """
    notes = (c.get("notes") or "").strip()
    if not notes:
        return "—"
    parts = [p.strip() for p in notes.split("·")]
    for p in parts:
        if p and not p.lower().startswith(("created via", "daisy:",
                                           "no-orders", "imported")):
            return p
    return parts[-1] if parts else "—"


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


# ── the stylesheet (one place; restrained, modern, print-clean) ──────────────

_STYLE = """<style>
  :root {
    --bg: #0f0e0c;          /* warm near-black */
    --panel: #17161300;     /* layered over bg via gradient */
    --surface: #1c1a17;
    --surface-2: #232019;
    --line: #2e2a24;
    --ink: #f4efe7;         /* parchment text */
    --ink-soft: #a8a097;
    --ink-mute: #6f685f;
    --red: #ef2b1f;         /* DoorDash-ish accent, used sparingly */
    --red-soft: #ff5a4d;
    --good: #4ec98a;
    --warn: #e9b04b;
    --alert: #ff6b5e;
    --radius: 16px;
    --mono: ui-monospace, "SF Mono", "JetBrains Mono", "Cascadia Code", monospace;
    --sans: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }
  @media (prefers-color-scheme: light) {
    :root {
      --bg: #f5f2ec; --surface: #fffdf9; --surface-2: #f0ece3;
      --line: #e3ddd1; --ink: #1c1916; --ink-soft: #5f574d; --ink-mute: #978d80;
    }
  }
  * { box-sizing: border-box; }
  html { -webkit-text-size-adjust: 100%; }
  body {
    margin: 0; background: var(--bg); color: var(--ink);
    font-family: var(--sans);
    font-feature-settings: "ss01","cv05","tnum";
    line-height: 1.5; letter-spacing: -0.01em;
    background-image:
      radial-gradient(120% 90% at 100% 0%, rgba(239,43,31,0.10), transparent 55%),
      radial-gradient(90% 70% at 0% 0%, rgba(239,43,31,0.05), transparent 50%);
  }
  .shell { max-width: 940px; margin: 0 auto; padding: 56px 28px 80px; }

  /* masthead */
  .masthead { display: flex; flex-direction: column; gap: 28px; margin-bottom: 40px; }
  .brand { display: inline-flex; align-items: center; gap: 9px; }
  .brand-dot { width: 9px; height: 9px; border-radius: 50%;
    background: var(--red); box-shadow: 0 0 0 4px rgba(239,43,31,0.18); }
  .brand-name { font-weight: 600; font-size: 0.82rem; letter-spacing: 0.16em;
    text-transform: uppercase; color: var(--ink-soft); }
  .eyebrow { font-size: 0.74rem; letter-spacing: 0.18em; text-transform: uppercase;
    color: var(--red-soft); font-weight: 600; margin-bottom: 10px; }
  .title { margin: 0; font-size: clamp(1.9rem, 4vw, 2.9rem); font-weight: 680;
    letter-spacing: -0.03em; line-height: 1.02; }
  .subtle { color: var(--ink-mute); font-size: 0.86rem; }
  .masthead-meta .subtle { margin-top: 8px; }

  /* summary cards */
  .cards { display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px;
    margin-bottom: 44px; }
  .card { background: var(--surface); border: 1px solid var(--line);
    border-radius: var(--radius); padding: 18px 16px; position: relative;
    overflow: hidden; }
  .card::after { content: ""; position: absolute; inset: 0 0 auto 0; height: 2px;
    background: var(--line); }
  .card--good::after { background: var(--good); }
  .card--warn::after { background: var(--warn); }
  .card--alert::after { background: var(--alert); }
  .card--muted::after { background: transparent; }
  .card-value { font-family: var(--mono); font-size: 1.8rem; font-weight: 600;
    letter-spacing: -0.04em; line-height: 1; }
  .card-label { margin-top: 8px; font-size: 0.72rem; letter-spacing: 0.1em;
    text-transform: uppercase; color: var(--ink-mute); }
  @media (max-width: 720px) { .cards { grid-template-columns: repeat(2, 1fr); } }

  /* customer */
  .roster { display: flex; flex-direction: column; gap: 22px; }
  .customer { background: linear-gradient(180deg, var(--surface), var(--bg));
    border: 1px solid var(--line); border-radius: 22px; overflow: hidden; }
  .customer-head { display: flex; gap: 16px; padding: 22px 24px;
    border-bottom: 1px solid var(--line); align-items: center; }
  .avatar { flex: 0 0 auto; width: 46px; height: 46px; border-radius: 14px;
    display: grid; place-items: center; font-weight: 650; font-size: 0.95rem;
    letter-spacing: 0.02em; color: var(--ink);
    background: var(--surface-2); border: 1px solid var(--line); }
  .customer-name { font-size: 1.12rem; font-weight: 640; letter-spacing: -0.02em;
    display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .customer-contact { margin-top: 5px; display: flex; gap: 16px; flex-wrap: wrap; }
  .contact { font-size: 0.85rem; color: var(--ink-soft);
    font-family: var(--mono); letter-spacing: -0.02em; }
  .contact-key { color: var(--ink-mute); margin-right: 7px; font-size: 0.7rem;
    text-transform: uppercase; letter-spacing: 0.08em; }
  .customer-addr { margin-top: 4px; font-size: 0.82rem; color: var(--ink-mute); }
  .customer-body { padding: 8px 24px 22px; display: flex; flex-direction: column; }

  /* order */
  .order { padding: 16px 0; border-bottom: 1px solid var(--line); }
  .order:last-child { border-bottom: none; }
  .order-head { display: flex; justify-content: space-between; gap: 16px;
    align-items: flex-start; }
  .order-name { font-weight: 600; font-size: 1rem; }
  .order-meta { color: var(--ink-mute); font-size: 0.82rem; margin-top: 3px; }
  .amt-detail { font-family: var(--mono); }
  .order-right { display: flex; align-items: center; gap: 12px; flex: 0 0 auto; }
  .order-price { font-family: var(--mono); font-weight: 600; font-size: 0.98rem;
    letter-spacing: -0.03em; }

  /* pills */
  .pill { display: inline-flex; align-items: center; gap: 6px;
    font-size: 0.72rem; font-weight: 600; letter-spacing: 0.02em;
    padding: 4px 11px; border-radius: 999px; border: 1px solid transparent;
    white-space: nowrap; }
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
  .pill--soft { font-weight: 500; font-size: 0.66rem; padding: 2px 9px; }
  .pill--soft::before { display: none; }

  /* claim + chat trail */
  .order-trail { margin-top: 14px; display: flex; flex-direction: column;
    gap: 10px; }
  .trail-item { display: flex; gap: 11px; align-items: flex-start;
    padding: 11px 14px; border-radius: 12px; background: var(--surface-2);
    border: 1px solid var(--line); font-size: 0.86rem; }
  .trail-glyph { font-family: var(--mono); opacity: 0.7; }
  .trail-item--good { border-color: rgba(78,201,138,0.30); }
  .trail-item--good .trail-glyph { color: var(--good); }
  .trail-item--warn .trail-glyph { color: var(--warn); }
  .trail-err { color: var(--alert); font-size: 0.8rem; margin-top: 4px; }

  .chat { border: 1px solid var(--line); border-radius: 14px; overflow: hidden;
    background: var(--surface-2); }
  .chat-head { padding: 10px 14px; font-size: 0.76rem; color: var(--ink-soft);
    border-bottom: 1px solid var(--line); display: flex; align-items: center;
    gap: 10px; }
  .chat-tag { font-size: 0.64rem; letter-spacing: 0.12em; text-transform: uppercase;
    color: var(--ink-mute); border: 1px solid var(--line); padding: 2px 7px;
    border-radius: 6px; }
  .chat--good .chat-head { color: var(--good); }
  .chat--alert .chat-head { color: var(--alert); }
  .chat--warn .chat-head { color: var(--warn); }
  .chat-thread { padding: 14px; display: flex; flex-direction: column; gap: 8px; }
  .bubble { max-width: 78%; padding: 9px 13px; border-radius: 14px;
    font-size: 0.88rem; display: flex; flex-direction: column; gap: 3px;
    line-height: 1.42; }
  .bubble-who { font-size: 0.6rem; letter-spacing: 0.1em; text-transform: uppercase;
    opacity: 0.6; }
  .bubble--out { align-self: flex-end; background: var(--red);
    color: #fff; border-bottom-right-radius: 4px; }
  .bubble--out .bubble-who { color: rgba(255,255,255,0.75); }
  .bubble--in { align-self: flex-start; background: var(--surface);
    border: 1px solid var(--line); border-bottom-left-radius: 4px; }
  .bubble--sys { align-self: center; background: transparent;
    color: var(--ink-mute); font-size: 0.78rem; max-width: 100%;
    text-align: center; }

  /* empty states */
  .no-orders { display: flex; gap: 14px; align-items: center; padding: 14px 0; }
  .no-orders-mark { font-size: 1.4rem; color: var(--ink-mute);
    width: 40px; height: 40px; display: grid; place-items: center;
    border: 1px dashed var(--line); border-radius: 12px; }
  .no-orders-title { font-weight: 600; }
  .page-empty { text-align: center; padding: 80px 20px; }
  .page-empty-mark { font-size: 2.4rem; color: var(--ink-mute); }
  .page-empty-title { font-size: 1.2rem; font-weight: 640; margin-top: 12px; }

  .footer { margin-top: 48px; padding-top: 20px; border-top: 1px solid var(--line);
    color: var(--ink-mute); font-size: 0.78rem; text-align: center; }

  @media print {
    body { background: #fff; color: #111; }
    .customer, .card, .chat { break-inside: avoid; }
  }
</style>"""
