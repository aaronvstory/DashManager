"""Order-history scraping: scroll-until-stable, card parsing, receipt text.

The scroll loop and not-logged-in/Cloudflare gates are ported verbatim from
the proven ddtr app; card text parsing is a pure helper so it stays unit-
testable without a browser.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any, Callable

from playwright.async_api import Page

from backend.browser.driver import SessionExpiredError, handle_cloudflare
from backend.browser.pacing import human_pause
from backend.browser.selectors import (
    CANCELLED_BADGE_TEXTS,
    CANCELLED_STATUS_TEXTS,
    PENDING_CLAIM_BADGE_TEXTS,
    REMAKE_BADGE_TEXTS,
    IN_PROGRESS_SECTION,
    IN_PROGRESS_STATUS_TEXTS,
    LOGIN_URL_MARKERS,
    ORDER_CARD_SELECTORS,
    ORDER_LINK_SELECTOR,
    ORDERS_EMPTY_TEXT,
    ORDERS_IN_PROGRESS_HEADER,
    ORDERS_URL,
    SCROLL_MAX_ITERS,
    SCROLL_STABLE_ITERS,
    STATUS_DISPLAY,
    VIEW_ORDER_BUTTON,
)
from backend.models import OrderStatus, OrdersScrapeResult, ScrapedOrder

EmitFn = Callable[[str, dict[str, Any]], None]

_UUID_RE = re.compile(r"/orders/([0-9a-f-]{36})")
_PRICE_RE = re.compile(r"\$(\d+\.\d{2})")
_ITEMS_RE = re.compile(r"(\d+)\s+items?")

# One evaluate per page: pull every card's innerText + order href together.
_EXTRACT_CARDS_JS = """
(sel) => {
  const cards = [...document.querySelectorAll(sel.card)];
  return cards.map(c => {
    const link = c.matches(sel.link) ? c
               : (c.querySelector(sel.link) || c.closest(sel.link));
    let href = link ? (link.href || link.getAttribute('href') || '') : '';
    if (!href && (c.tagName === 'A' || c.getAttribute('role') === 'link')) {
      href = c.href || c.getAttribute('href') || '';
    }
    return { text: c.innerText || '', href };
  });
}
"""

# In-progress orders: one row per "View Order" button inside the section.
# They carry no /orders/<uuid> link, so each is captured by its row text
# (store + status). Walks up from the button to the row holding the store.
_EXTRACT_IN_PROGRESS_JS = """
(sel) => {
  const sec = document.querySelector(sel.section);
  if (!sec) return [];
  const btns = [...sec.querySelectorAll(sel.btn)];
  return btns.map(b => {
    let el = b;
    for (let i = 0; i < 6 && el.parentElement; i++) {
      el = el.parentElement;
      if (el.innerText && el.innerText.split('\\n').length >= 2) break;
    }
    return { text: el.innerText || '' };
  });
}
"""


def extract_order_uuid(href: str) -> str | None:
    """Pure: order uuid from a /orders/<uuid> href, or None."""
    m = _UUID_RE.search(href or "")
    return m.group(1) if m else None


def classify_orders_page(body_text: str) -> str:
    """Pure: classify the orders page from its body text.

    Returns "none" (no orders ever), "in_progress" (has a live order), or
    "has_completed" (has finished orders). A page can have both an In Progress
    section and completed orders below — "in_progress" wins for the headline
    state but completed cards are still scraped separately.
    """
    lo = (body_text or "").lower()
    if ORDERS_EMPTY_TEXT in lo:
        return "none"
    if (ORDERS_IN_PROGRESS_HEADER in lo
            or any(s in lo for s in IN_PROGRESS_STATUS_TEXTS)):
        return "in_progress"
    return "has_completed"


def in_progress_status(text: str) -> str:
    """Pure: friendly in-progress status label from a card's text, or ''."""
    lo = (text or "").lower()
    # Longest phrases first so "picking up your doubledash order" wins over
    # the substring "picking up your order".
    for s in sorted(IN_PROGRESS_STATUS_TEXTS, key=len, reverse=True):
        if s in lo:
            return STATUS_DISPLAY.get(s, s.title())
    return ""


def parse_card_text(text: str) -> dict[str, Any]:
    """Pure parse of one order card's innerText.

    Live card example (2026-06):
        Dairy Queen
        $112.34 • 5 items • Personal
        Chicken Strip Baskets
        Order Cancelled
    """
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    store_name = lines[0] if lines else ""

    price: float | None = None
    price_line_idx: int | None = None
    for i, ln in enumerate(lines):
        m = _PRICE_RE.search(ln)
        if m:
            price = float(m.group(1))
            price_line_idx = i
            break

    items = _ITEMS_RE.search(text or "")
    items_count = int(items.group(1)) if items else None

    lowered = (text or "").lower()
    # Use the broader status set so a short "Cancelled" badge on the list card
    # still classifies the order as cancelled (not defaulted to completed).
    cancelled = any(b in lowered for b in CANCELLED_STATUS_TEXTS)
    remake = any(b in lowered for b in REMAKE_BADGE_TEXTS)
    # Pending Refund / Pending Resolution card = a claimable refund (self-claim
    # to original card). These cards often lack a /orders/<uuid> link, so the
    # scrape must NOT drop them for missing a UUID.
    pending_claim = any(b in lowered for b in PENDING_CLAIM_BADGE_TEXTS)

    description = ""
    if price_line_idx is not None and price_line_idx + 1 < len(lines):
        candidate = lines[price_line_idx + 1]
        cand_lo = candidate.lower()
        # A cancelled/remake badge directly after the price line is not a
        # description.
        if not any(b in cand_lo for b in CANCELLED_BADGE_TEXTS + REMAKE_BADGE_TEXTS):
            description = candidate

    return {
        "store_name": store_name,
        "description": description,
        "items_count": items_count,
        "price": price,
        "cancelled": cancelled,
        "remake": remake,
        "pending_claim": pending_claim,
        "dasher_name": _extract_dasher(text or ""),
    }


# Dasher name on a live order, e.g. "Erin is heading to you" / "Dasher: Erin".
_DASHER_RES = [
    re.compile(r"([A-Z][a-z]+)\s+is\s+(?:heading|on the way|almost|"
               r"picking up|delivering)", re.I),
    re.compile(r"(?:dasher|driver)[:\s]+([A-Z][a-z]+)", re.I),
    re.compile(r"([A-Z][a-z]+)\s+(?:picked up|has your order)", re.I),
]


def _extract_dasher(text: str) -> str:
    for rx in _DASHER_RES:
        m = rx.search(text or "")
        if m:
            return m.group(1).strip().title()
    return ""


async def scrape_orders_full(
    page: Page, emit: EmitFn | None = None,
) -> OrdersScrapeResult:
    """Scrape /orders and classify state. Raises SessionExpiredError if out.

    Two distinct order shapes (verified live 2026-06-12):
      • COMPLETED orders — under OrdersCompletedSection, carry a
        /orders/<uuid> receipt link → UUID-keyed, refund-checkable.
      • IN-PROGRESS orders — under OrdersInProgressSection, a "View Order"
        button and NO link on the card → captured by row text (store + status
        + dasher), keyed by a synthetic id; not refund-checkable yet.
    """
    await page.goto(ORDERS_URL, wait_until="domcontentloaded")
    await asyncio.sleep(3)  # redirects to login land after dcl (harvest)
    if any(marker in page.url for marker in LOGIN_URL_MARKERS):
        raise SessionExpiredError(f"redirected to {page.url}")
    await handle_cloudflare(page)
    await asyncio.sleep(3)  # the In Progress section renders a beat later

    orders: list[ScrapedOrder] = []

    # ── In-progress orders (no UUID; text-keyed) ──
    ip_raw: list[dict[str, str]] = await page.evaluate(
        _EXTRACT_IN_PROGRESS_JS,
        {"section": IN_PROGRESS_SECTION, "btn": VIEW_ORDER_BUTTON})
    for idx, item in enumerate(ip_raw):
        text = item.get("text") or ""
        parsed = parse_card_text(text)
        status_text = in_progress_status(text) or "In progress"
        store = parsed["store_name"] or "order"
        orders.append(ScrapedOrder(
            order_uuid=f"inprogress:{store}:{idx}",  # synthetic — no real uuid
            receipt_url="",
            store_name=parsed["store_name"],
            description=parsed["description"],
            items_count=parsed["items_count"], price=parsed["price"],
            order_status=OrderStatus.in_progress, status_text=status_text,
            dasher_name=parsed.get("dasher_name", "")))

    # Re-read body AFTER the in-progress section is up, then classify.
    body = await page.evaluate(
        "() => document.body ? document.body.innerText : ''")
    page_state = classify_orders_page(body)
    if page_state == "none" and not orders:
        if emit:
            emit("log", {"message": "no orders (No Previous Deliveries)"})
        return OrdersScrapeResult(state="none")

    # ── Completed orders (UUID-keyed) ──
    # Skip the (up to ~52s) scroll loop entirely when no completed-order cards
    # are present — e.g. an account with ONLY in-progress orders.
    chosen = await _pick_card_selector(page)
    prev = stable = 0
    if chosen is not None:
        for _ in range(SCROLL_MAX_ITERS):
            if any(marker in page.url for marker in LOGIN_URL_MARKERS):
                raise SessionExpiredError(
                    f"redirected to {page.url} during scroll")
            await page.evaluate(
                "window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(0.55)
            await page.keyboard.press("End")
            await asyncio.sleep(0.75)
            count = await page.locator(chosen).count()
            if count == prev and count > 0:
                stable += 1
                if stable >= SCROLL_STABLE_ITERS:
                    break
            else:
                stable = 0
            prev = count

    seen: set[str] = set()
    if chosen is not None:
        if emit:
            emit("log", {"message": f"completed card selector: {chosen}"})
        raw: list[dict[str, str]] = await page.evaluate(
            _EXTRACT_CARDS_JS, {"card": chosen, "link": ORDER_LINK_SELECTOR})
        claim_idx = 0
        for item in raw:
            href = item.get("href") or ""
            uuid = extract_order_uuid(href)
            text = item.get("text") or ""
            parsed = parse_card_text(text)
            if uuid is None:
                # No receipt UUID. A "Pending Refund/Resolution" card is a REAL,
                # self-claimable order (no receipt link until resolved) — keep
                # it with a synthetic id so it isn't silently dropped (this bug
                # lost 2 easy self-serve refunds live 2026-06-13). Anything else
                # uuid-less is an in-progress card, handled separately.
                if not parsed.get("pending_claim"):
                    continue
                claim_idx += 1
                synth = f"pendingclaim:{parsed['store_name']}:{claim_idx}"
                if synth in seen:
                    continue
                seen.add(synth)
                orders.append(ScrapedOrder(
                    order_uuid=synth, receipt_url="",
                    store_name=parsed["store_name"],
                    description=parsed["description"],
                    items_count=parsed["items_count"], price=parsed["price"],
                    order_status=OrderStatus.cancelled,
                    claimable_from_card=True,
                    dasher_name=parsed.get("dasher_name", "")))
                continue
            if uuid in seen:
                continue
            seen.add(uuid)
            lifecycle = (OrderStatus.cancelled if parsed["cancelled"]
                         else OrderStatus.completed)
            orders.append(ScrapedOrder(
                order_uuid=uuid, receipt_url=href,
                store_name=parsed["store_name"],
                description=parsed["description"],
                items_count=parsed["items_count"], price=parsed["price"],
                order_status=lifecycle,
                claimable_from_card=parsed.get("pending_claim", False),
                dasher_name=parsed.get("dasher_name", "")))

    n_prog = sum(1 for o in orders if o.order_status == OrderStatus.in_progress)
    n_done = sum(1 for o in orders if o.order_status == OrderStatus.completed)
    if emit:
        emit("log", {"message":
                     f"scraped {len(orders)} orders "
                     f"({n_prog} in progress, {n_done} completed)"})
    return OrdersScrapeResult(
        state="in_progress" if n_prog else
              ("has_completed" if orders else page_state),
        orders=orders, in_progress_count=n_prog, completed_count=n_done)


async def scrape_orders(page: Page,
                        emit: EmitFn | None = None) -> list[ScrapedOrder]:
    """Back-compat list wrapper around scrape_orders_full."""
    return (await scrape_orders_full(page, emit)).orders


async def open_receipt(page: Page, receipt_url: str) -> str:
    """Open one order's receipt page and return its full body innerText.

    Raises SessionExpiredError if the receipt URL redirects to login — an
    expired session bounces /orders/<uuid> to identity.doordash.com, which
    previously returned the login page's text and looked like an unreadable
    receipt. Surfacing it as SessionExpiredError lets the caller auto-heal
    (re-login + retry) instead of silently mislabelling the order.
    """
    await page.goto(receipt_url, wait_until="domcontentloaded")
    await handle_cloudflare(page)
    # Jittered settle after the load — human-paced, and gives the breakdown a
    # beat to render so the detector reads a complete receipt (not a partial).
    await human_pause(1.5, 3.0)
    if any(marker in page.url for marker in LOGIN_URL_MARKERS):
        raise SessionExpiredError(
            f"receipt {receipt_url} redirected to login ({page.url})")
    return await page.evaluate(
        "() => document.body ? document.body.innerText : ''")


async def _pick_card_selector(page: Page) -> str | None:
    for sel in ORDER_CARD_SELECTORS:
        if await page.locator(sel).count() > 0:
            return sel
    return None
