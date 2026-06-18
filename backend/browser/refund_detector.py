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

# Top-of-receipt refund banner, e.g. "We've issued $201.59 refund + free Express
# delivery upgrade. Refund will process to your original payment method within
# 5-7 business days." This is valid refund proof on its own (the charge comes
# back by the original authorization dropping off — a breakdown `Refund -$X`
# line may never appear). The apostrophe is often a curly ' on the live page, so
# allow any single char between We and ve. Verified live 2026-06-18.
_ISSUED_BANNER_RE = re.compile(
    rf"we.?ve\s+issued\s+\$\s*({_AMOUNT})\s+refund", re.IGNORECASE)
# A card/Payment block on the receipt ("Payment  Visa…0000  $X" + "Change payment
# method"). Its presence WITH no refund proof = risk of a missing refund.
_CARD_BLOCK_RE = re.compile(
    r"change payment method|\bpayment\b[\s\S]{0,40}\bvisa\b|"
    r"\bvisa\b[\s\S]{0,20}\d{4}", re.IGNORECASE)
# Money issued to DoorDash credits rather than the original card — must be
# converted (chat) within ~3 days. "credits"/"credit balance"/"DoorDash credit".
_CREDITS_RE = re.compile(
    r"door ?dash credit|credit balance|issued .{0,20}credit|in credits?\b",
    re.IGNORECASE)


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
    For ``Refund`` this is safe: a real receipt breakdown carries exactly ONE
    ``Refund -$X`` line (verified live, see HARVEST_NOTES), so "last wins" only
    ever discards a stray earlier mention. In the hypothetical of two genuine
    refund lines summing to the total, last-wins reads the smaller and yields
    ``partial`` — which routes to a chat + human re-check (never a false
    ``refunded``), so the zero-tolerance bar holds even there.
    """
    text = page_text or ""
    lines = [ln.strip() for ln in text.splitlines()]

    total_label: str = cfg.get("total_label", "Total")
    refund_label: str = cfg.get("refund_label", "Refund")
    cancelled_texts: list[str] = cfg.get("cancelled_texts", [])
    pending_texts: list[str] = cfg.get("pending_texts", _DEFAULT_PENDING_TEXTS)
    remake_texts: list[str] = cfg.get("remake_texts", [])

    text_cf = text.casefold()
    cancelled_seen = any(t.casefold() in text_cf for t in cancelled_texts if t)
    pending_seen = any(t.casefold() in text_cf for t in pending_texts if t)
    remake_seen = any(t.casefold() in text_cf for t in remake_texts if t)

    # Refinement signals (2026-06-18).
    banner_m = _ISSUED_BANNER_RE.search(text)
    issued_banner = _to_float(banner_m.group(1)) if banner_m else None
    # Banner only counts as proof if it also says the money goes to the original
    # payment method (credits banners would say "credit" — handled separately).
    banner_to_card = bool(issued_banner) and "original payment method" in text_cf
    card_block_seen = bool(_CARD_BLOCK_RE.search(text))
    credits_seen = bool(_CREDITS_RE.search(text))

    totals = _label_amounts(lines, total_label)
    refunds = _label_amounts(lines, refund_label)

    total = totals[-1] if totals else None
    refund = abs(refunds[-1]) if refunds else None

    def _result(status: RefundStatus, **kw) -> RefundResult:
        return RefundResult(
            status=status, total_amount=total, refund_amount=refund,
            cancelled_text_seen=cancelled_seen, remake_seen=remake_seen,
            issued_banner_amount=issued_banner, card_block_seen=card_block_seen,
            credits_seen=credits_seen, **kw)

    # A real Refund line wins outright (strongest proof).
    if refund and total is not None:
        return _result(RefundStatus.refunded if refund >= total
                       else RefundStatus.partial)

    # An "We've issued $X refund ... original payment method" banner is valid
    # proof even when the breakdown Total/Refund didn't parse (banner-only
    # receipts). Use the banner amount as the refund amount.
    if banner_to_card:
        refund = issued_banner
        return _result(RefundStatus.refunded)

    if total is None:
        # No Total parsed AND no banner proof. If a card block is present this is
        # a live charge with no refund proof -> pursue (not silently unknown).
        if card_block_seen:
            return _result(RefundStatus.not_refunded)
        return _result(RefundStatus.unknown)

    # Total known, no refund line, no card-to-original banner.
    # Credits: money issued but to credits, not card -> needs conversion (chat).
    if credits_seen and not pending_seen:
        return _result(RefundStatus.not_refunded)  # routes to chat (convert)
    if pending_seen:
        return _result(RefundStatus.pending_claim)
    if remake_seen:
        return _result(RefundStatus.remake)
    # Cancelled order, no refund line: a card block present = clear risk of a
    # missing refund; absence of a card block + no banner is still not proven.
    return _result(RefundStatus.not_refunded)
