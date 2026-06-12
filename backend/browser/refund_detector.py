"""Pure refund detector: parses a receipt page's body innerText.

The single most important correctness logic in the app. Operates only on text
plus the ``refund_signal`` settings dict — no browser, no I/O — so it is fully
unit-testable against fixture pages.

Real-world receipt layout (live page, 2026-06): the cost breakdown renders as
label/value pairs that in innerText appear as alternating lines::

    Subtotal
    $50.95
    ...
    Total
    $112.34
    Refund
    -$112.34

Same-line forms ("Total $112.34", "Refund -$112.34") are also tolerated.
"""
from __future__ import annotations

import re

from backend.models import RefundResult, RefundStatus

# Dollar value: optional sign on either side of the $, optional thousands
# commas, exactly two decimals. The sign sits outside the captured digits so
# parsed amounts are always positive (callers care about magnitude only).
_AMOUNT = r"(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}"
_MONEY_VALUE_RE = re.compile(rf"^[-+]?\s*\$\s*-?({_AMOUNT})$")


def _to_float(num: str) -> float:
    return float(num.replace(",", ""))


def _label_amounts(lines: list[str], label: str) -> list[float]:
    """Amounts for every breakdown line whose label matches exactly.

    Matches are case-insensitive and label-exact: either the line is the bare
    label (value on the next non-empty line) or the line is label + a lone
    dollar value. Prose that merely contains the label ("Total savings ...")
    never matches because the remainder must be exactly a money token.
    """
    label_cf = label.strip().casefold()
    amounts: list[float] = []
    for i, line in enumerate(lines):
        bare = line.rstrip(":").strip()
        if bare.casefold() == label_cf:
            for nxt in lines[i + 1:]:
                if not nxt:
                    continue
                m = _MONEY_VALUE_RE.match(nxt)
                if m:
                    amounts.append(_to_float(m.group(1)))
                break
            continue
        if bare.casefold().startswith(label_cf):
            rest = bare[len(label_cf):].lstrip(" :\t")
            m = _MONEY_VALUE_RE.match(rest)
            if m:
                amounts.append(_to_float(m.group(1)))
    return amounts


# A self-service "Choose your refund method" / Resolution screen — the user
# claims the refund by selecting ORIGINAL PAYMENT METHOD and confirming.
# Verified live 2026-06-12. No agent chat needed for these.
_DEFAULT_PENDING_TEXTS = [
    "pending refund",
    "pending resolution",
    "choose your refund method",
    "to original payment method",  # only appears on the claim screen
]


def detect(page_text: str, cfg: dict) -> RefundResult:
    """Classify a receipt page's refund state from its body innerText.

    cfg is the ``refund_signal`` settings dict (total_label, refund_label,
    cancelled_texts, pending_texts). Rules, in order:

    - No parseable Total (junk page, Cloudflare interstitial, empty) ->
      ``unknown`` — never silently pass.
    - Refund line present: amount >= total -> ``refunded``; 0 < amount < total
      -> ``partial`` (a real refund always wins, even if a stale claim
      banner lingers).
    - A self-service claim screen ("Pending Refund" / "Choose your refund
      method") -> ``pending_claim`` — claim it (original payment method),
      don't chat.
    - Otherwise (cancelled, no refund, no claim) -> ``not_refunded`` — chat.
      Cancellation prose alone proves NOTHING; it only sets
      ``cancelled_text_seen``.

    If a label appears multiple times the LAST occurrence wins — pages can
    mention totals elsewhere (marketing copy, summaries) before the breakdown.
    """
    text = page_text or ""
    lines = [ln.strip() for ln in text.splitlines()]

    total_label: str = cfg.get("total_label", "Total")
    refund_label: str = cfg.get("refund_label", "Refund")
    cancelled_texts: list[str] = cfg.get("cancelled_texts", [])
    pending_texts: list[str] = cfg.get("pending_texts", _DEFAULT_PENDING_TEXTS)

    text_cf = text.casefold()
    cancelled_seen = any(t.casefold() in text_cf for t in cancelled_texts if t)
    pending_seen = any(t.casefold() in text_cf for t in pending_texts if t)

    totals = _label_amounts(lines, total_label)
    refunds = _label_amounts(lines, refund_label)

    total = totals[-1] if totals else None
    refund = abs(refunds[-1]) if refunds else None

    if total is None:
        return RefundResult(
            status=RefundStatus.unknown, total_amount=None,
            refund_amount=refund, cancelled_text_seen=cancelled_seen)

    # A real refund line wins outright.
    if refund:  # non-zero Refund line — money moved
        status = (RefundStatus.refunded if refund >= total
                  else RefundStatus.partial)
        return RefundResult(
            status=status, total_amount=total, refund_amount=refund,
            cancelled_text_seen=cancelled_seen)

    # No refund yet — is it self-claimable, or does it need a chat?
    if pending_seen:
        return RefundResult(
            status=RefundStatus.pending_claim, total_amount=total,
            refund_amount=refund, cancelled_text_seen=cancelled_seen)
    return RefundResult(
        status=RefundStatus.not_refunded, total_amount=total,
        refund_amount=refund, cancelled_text_seen=cancelled_seen)
