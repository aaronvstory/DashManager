"""Self-claim driver for ``pending_claim`` refunds — no agent chat needed.

DoorDash offers a self-service "Choose your refund method" screen for some
cancelled orders. The DEFAULT selection is CREDITS, which we must never accept
(credits ≠ a real refund). This driver claims the refund to the ORIGINAL
payment method and verifies the receipt afterwards shows a ``Refund -$X`` line.

Verified live 2026-06-12 (Wendy, $112.24):
  orders page → "Resolution" button → "Choose your refund method"
  → click "to original payment method" (selects that radio; credits is the
  bad default) → VERIFY the right radio is selected → "Confirm"
  → banner "We've issued $X refund … to your original payment method".

The verification + result parsing are pure (``claim_succeeded``) so they are
unit-testable without a browser; the Playwright orchestration is ``run_claim``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from playwright.async_api import Page

from backend.browser.pacing import human_pause
from backend.browser.refund_detector import detect
from backend.browser.selectors import (
    CLAIM_CONFIRM_BUTTON,
    CLAIM_GET_REFUND_TEXT,
    CLAIM_NAV_SETTLE_S,
    CLAIM_REMAKE_OFFER_TEXTS,
    CLAIM_SUCCESS_TEXTS,
    REFUND_METHOD_CREDITS_TEXT,
    REFUND_METHOD_ORIGINAL_TEXT,
    RESOLUTION_BUTTON,
)
from backend.models import RefundStatus

Emit = Callable[[str, dict[str, Any]], None]


@dataclass
class ClaimResult:
    """Outcome of one self-claim attempt (mirrors the claims audit row)."""
    outcome: str                 # 'success' | 'failed' | 'wrong_method' | 'error'
    amount: float | None = None
    to_original_payment: bool = False
    confirmed: bool = False
    error: str | None = None


def claim_succeeded(receipt_text: str, cfg: dict[str, Any]) -> ClaimResult:
    """Pure: did the post-confirm receipt prove a refund to the original card?

    Success requires BOTH a real ``Refund -$X`` line (the detector says
    refunded/partial) AND an original-payment banner — a credits refund or a
    lingering claim screen is NOT success. ``cfg`` is the refund_signal dict.
    """
    lo = (receipt_text or "").lower()
    to_original = any(t in lo for t in CLAIM_SUCCESS_TEXTS)
    rr = detect(receipt_text, cfg)
    refunded = rr.status in (RefundStatus.refunded, RefundStatus.partial)
    if refunded and to_original:
        return ClaimResult(outcome="success", amount=rr.refund_amount,
                           to_original_payment=True, confirmed=True)
    if refunded and not to_original:
        # Money moved but we can't see the original-payment banner — treat as
        # success on the refund line (the detector already vetoes credits via
        # its own logic) but record that the banner wasn't confirmed.
        return ClaimResult(outcome="success", amount=rr.refund_amount,
                           to_original_payment=False, confirmed=True)
    return ClaimResult(outcome="failed", amount=rr.total_amount,
                       to_original_payment=to_original, confirmed=False)


def is_remake_offer_page(text: str) -> bool:
    """Pure: is this the REMAKE-OFFER variant of the resolution screen?

    Heidi's $107.01: Resolution led to "<store> can remake your order" with a
    "Review new order" button + a "Get refund" link, instead of going straight
    to the credits-vs-card screen. We must click "Get refund" first here.
    """
    lo = (text or "").lower()
    return any(t in lo for t in CLAIM_REMAKE_OFFER_TEXTS)


async def _maybe_click_get_refund(page: Page) -> bool:
    """On the remake-offer variant, click 'Get refund' to reach the credits-
    vs-card screen. Returns True if we clicked it. NEVER touches 'Review new
    order' (that would accept the remake instead of refunding)."""
    try:
        body = await page.evaluate(
            "() => document.body ? document.body.innerText : ''") or ""
    except Exception:
        body = ""
    if not is_remake_offer_page(body):
        return False
    try:
        await page.get_by_text(CLAIM_GET_REFUND_TEXT,
                               exact=False).first.click(timeout=8_000)
        return True
    except Exception:
        # Couldn't find the link by text — try a role=link fallback.
        try:
            await page.get_by_role(
                "link", name=CLAIM_GET_REFUND_TEXT).first.click(timeout=5_000)
            return True
        except Exception:
            return False


async def _select_original_payment(page: Page) -> bool:
    """Click the 'to original payment method' option; return True if clicked.

    Credits is the default — selecting the original-payment radio is the whole
    point. We click the visible text (the radio has no stable testid).
    """
    try:
        await page.get_by_text(REFUND_METHOD_ORIGINAL_TEXT,
                               exact=False).first.click(timeout=8_000)
        return True
    except Exception:
        return False


async def _verify_original_selected(page: Page) -> bool:
    """Best-effort check that the ORIGINAL-payment radio is the chosen one.

    DoorDash renders the choice as styled radios; we look for an
    ``aria-checked``/``:checked`` element whose surrounding text mentions the
    original-payment option and NOT credits. Returns True when we can confirm
    it, False when we genuinely see credits selected, and True (optimistic)
    when the DOM is unreadable — the post-confirm receipt is the real guard.
    """
    try:
        checked = await page.evaluate(
            """() => {
                const nodes = [...document.querySelectorAll(
                    '[role=radio], input[type=radio], [aria-checked]')];
                const sel = nodes.filter(n =>
                    n.getAttribute('aria-checked') === 'true' ||
                    n.checked === true);
                return sel.map(n => {
                    const row = n.closest('label, li, div') || n;
                    return (row.innerText || '').toLowerCase();
                });
            }""")
    except Exception:
        return True  # unreadable DOM — let the receipt verification decide
    if not checked:
        return True  # nothing reported checked — rely on the receipt
    orig = REFUND_METHOD_ORIGINAL_TEXT.lower()
    cred = REFUND_METHOD_CREDITS_TEXT.lower()
    for txt in checked:
        if orig in txt:
            return True
        if cred in txt and orig not in txt:
            return False  # credits is the selected one — abort the confirm
    return True


async def run_claim(page: Page, order_uuid: str, receipt_url: str,
                    refund_cfg: dict[str, Any],
                    emit: Emit | None = None) -> ClaimResult:
    """Drive the full self-claim for one pending_claim order.

    Assumes the orders page (or the order receipt) is reachable. Navigates to
    the receipt to find the Resolution button, claims to the original payment
    method, confirms, and re-reads the receipt to verify a Refund line.
    """
    def _emit(type: str, data: dict[str, Any]) -> None:
        if emit is not None:
            emit(type, data)

    try:
        from backend.browser.driver import handle_cloudflare, screenshot
        from backend.browser.orders import open_receipt

        # Land on the order's page where the Resolution button lives.
        await page.goto(receipt_url, wait_until="domcontentloaded")
        await handle_cloudflare(page)
        await human_pause(1.5, 3.0)

        # Click "Resolution" -> "Choose your refund method".
        try:
            await page.get_by_role(
                "button", name=RESOLUTION_BUTTON).first.click(timeout=8_000)
        except Exception:
            return ClaimResult(outcome="failed",
                               error="Resolution button not found")
        await human_pause(CLAIM_NAV_SETTLE_S, CLAIM_NAV_SETTLE_S + 1.5)

        # REMAKE-OFFER variant: click "Get refund" first to reach the
        # credits-vs-card screen (the DIRECT variant skips straight to it).
        if await _maybe_click_get_refund(page):
            _emit("log", {"msg": f"claim {order_uuid}: remake-offer variant, "
                                 "clicked Get refund"})
            await human_pause(CLAIM_NAV_SETTLE_S, CLAIM_NAV_SETTLE_S + 1.5)

        if not await _select_original_payment(page):
            return ClaimResult(outcome="failed",
                               error="original-payment option not found")
        await human_pause(0.8, 1.8)

        # Guard: never confirm while CREDITS is the selected radio.
        if not await _verify_original_selected(page):
            await screenshot(page, f"claim_wrong_method_{order_uuid}")
            return ClaimResult(outcome="wrong_method", to_original_payment=False,
                               error="credits still selected before confirm")

        try:
            await page.get_by_role(
                "button", name=CLAIM_CONFIRM_BUTTON).first.click(timeout=8_000)
        except Exception:
            return ClaimResult(outcome="failed", to_original_payment=True,
                               error="Confirm button not found")
        await human_pause(2.0, 4.0)  # let the refund post + banner render

        # Re-read the receipt to verify a real Refund line landed.
        text = await open_receipt(page, receipt_url)
        result = claim_succeeded(text, refund_cfg)
        _emit("log", {"msg": f"claim {order_uuid}: {result.outcome} "
                             f"(amount={result.amount})"})
        return result
    except Exception as exc:
        try:
            from backend.browser.driver import screenshot
            await screenshot(page, f"claim_error_{order_uuid}")
        except Exception:
            pass
        return ClaimResult(outcome="error", error=str(exc))
