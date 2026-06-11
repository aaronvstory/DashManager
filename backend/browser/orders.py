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
from backend.browser.selectors import (
    ACTIVE_ORDER_CARD_SELECTORS,
    CANCELLED_BADGE_TEXTS,
    IN_PROGRESS_STATUS_TEXTS,
    LOGIN_URL_MARKERS,
    ORDER_CARD_SELECTORS,
    ORDER_LINK_SELECTOR,
    ORDERS_EMPTY_TEXT,
    ORDERS_IN_PROGRESS_HEADER,
    ORDERS_URL,
    SCROLL_MAX_ITERS,
    SCROLL_STABLE_ITERS,
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
    """Pure: the matched in-progress status phrase from a card's text, or ''."""
    lo = (text or "").lower()
    for s in IN_PROGRESS_STATUS_TEXTS:
        if s in lo:
            # Return the human form from the original text where possible.
            idx = lo.find(s)
            return (text[idx:idx + len(s)]).strip() or s
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
    cancelled = any(b in lowered for b in CANCELLED_BADGE_TEXTS)

    description = ""
    if price_line_idx is not None and price_line_idx + 1 < len(lines):
        candidate = lines[price_line_idx + 1]
        # A cancelled badge directly after the price line is not a description.
        if not any(b in candidate.lower() for b in CANCELLED_BADGE_TEXTS):
            description = candidate

    return {
        "store_name": store_name,
        "description": description,
        "items_count": items_count,
        "price": price,
        "cancelled": cancelled,
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

    Active (in-progress) orders DO carry a /orders/<uuid> link (verified live
    2026-06-12), so the same card extraction covers both — each is classified
    by its status text into in_progress / cancelled / completed.
    """
    await page.goto(ORDERS_URL, wait_until="domcontentloaded")
    await asyncio.sleep(3)  # redirects to login land after dcl (harvest)
    if any(marker in page.url for marker in LOGIN_URL_MARKERS):
        raise SessionExpiredError(f"redirected to {page.url}")
    await handle_cloudflare(page)

    body = await page.evaluate("() => document.body ? document.body.innerText : ''")
    page_state = classify_orders_page(body)
    if page_state == "none":
        if emit:
            emit("log", {"message": "no orders (No Previous Deliveries)"})
        return OrdersScrapeResult(state="none")

    chosen: str | None = None
    prev = stable = 0
    for _ in range(SCROLL_MAX_ITERS):
        if any(marker in page.url for marker in LOGIN_URL_MARKERS):
            raise SessionExpiredError(f"redirected to {page.url} during scroll")
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(0.55)
        await page.keyboard.press("End")
        await asyncio.sleep(0.75)
        if chosen is None:
            chosen = await _pick_card_selector(page)
        count = await page.locator(chosen).count() if chosen else 0
        if count == prev and count > 0:
            stable += 1
            if stable >= SCROLL_STABLE_ITERS:
                break
        else:
            stable = 0
        prev = count

    if chosen is None:
        if emit:
            emit("log", {"message":
                         "page shows orders but no card selector matched — "
                         "selector drift?"})
        return OrdersScrapeResult(state=page_state)
    if emit:
        emit("log", {"message": f"order card selector: {chosen}"})

    raw: list[dict[str, str]] = await page.evaluate(
        _EXTRACT_CARDS_JS, {"card": chosen, "link": ORDER_LINK_SELECTOR})

    orders: list[ScrapedOrder] = []
    seen: set[str] = set()
    for item in raw:
        href = item.get("href") or ""
        uuid = extract_order_uuid(href)
        if uuid is None or uuid in seen:
            continue
        seen.add(uuid)
        text = item.get("text") or ""
        parsed = parse_card_text(text)
        status_text = in_progress_status(text)
        if parsed["cancelled"]:
            lifecycle = OrderStatus.cancelled
        elif status_text:
            lifecycle = OrderStatus.in_progress
        else:
            lifecycle = OrderStatus.completed
        orders.append(ScrapedOrder(
            order_uuid=uuid, receipt_url=href,
            store_name=parsed["store_name"],
            description=parsed["description"],
            items_count=parsed["items_count"], price=parsed["price"],
            order_status=lifecycle, status_text=status_text,
            dasher_name=parsed.get("dasher_name", "")))

    n_prog = sum(1 for o in orders if o.order_status == OrderStatus.in_progress)
    n_done = sum(1 for o in orders if o.order_status == OrderStatus.completed)
    if emit:
        emit("log", {"message":
                     f"scraped {len(orders)} orders "
                     f"({n_prog} in progress, {n_done} completed)"})
    return OrdersScrapeResult(
        state="in_progress" if n_prog else "has_completed",
        orders=orders, in_progress_count=n_prog, completed_count=n_done)


async def scrape_orders(page: Page,
                        emit: EmitFn | None = None) -> list[ScrapedOrder]:
    """Back-compat list wrapper around scrape_orders_full."""
    return (await scrape_orders_full(page, emit)).orders


async def open_receipt(page: Page, receipt_url: str) -> str:
    """Open one order's receipt page and return its full body innerText."""
    await page.goto(receipt_url, wait_until="domcontentloaded")
    await handle_cloudflare(page)
    await asyncio.sleep(1.5)
    return await page.evaluate(
        "() => document.body ? document.body.innerText : ''")


async def _pick_card_selector(page: Page) -> str | None:
    for sel in ORDER_CARD_SELECTORS:
        if await page.locator(sel).count() > 0:
            return sel
    return None
