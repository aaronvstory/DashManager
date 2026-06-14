"""Pure tests for the self-claim result parser (no browser)."""
from __future__ import annotations

from backend.browser.claim import (
    claim_succeeded,
    decide_original_selected,
    is_remake_offer_page,
)
from backend.config import DEFAULT_SETTINGS

CFG = DEFAULT_SETTINGS["refund_signal"]

# Post-confirm receipt: a real Refund line + the original-payment banner.
CLAIMED_TO_ORIGINAL = (
    "We've issued a $112.24 refund to your original payment method\n"
    "Subtotal\n$95.00\nService Fee\n$7.64\nTotal\n$112.24\n"
    "Refund\n-$112.24\nPaid with Visa"
)

# Refund line present but no original-payment banner visible.
REFUNDED_NO_BANNER = (
    "Subtotal\n$95.00\nTotal\n$112.24\nRefund\n-$112.24\nPaid with Visa"
)

# Still on the claim screen — confirm didn't take.
STILL_PENDING = (
    "Choose your refund method\n$112.24 credits\n"
    "$112.24 to your original payment method\nSubtotal\n$95.00\nTotal\n$112.24"
)


def test_claim_success_to_original():
    r = claim_succeeded(CLAIMED_TO_ORIGINAL, CFG)
    assert r.outcome == "success"
    assert r.amount == 112.24
    assert r.to_original_payment is True
    assert r.confirmed is True


def test_claim_refund_line_without_banner_is_unconfirmed():
    # ZERO TOLERANCE: a Refund line WITHOUT a positive original-payment banner
    # is NOT success — we can't prove it went to the card (could be partial,
    # credits worded differently, etc.). It must surface as `unconfirmed` for a
    # human to verify, never silently pass as a won refund.
    r = claim_succeeded(REFUNDED_NO_BANNER, CFG)
    assert r.outcome == "unconfirmed"
    assert r.amount == 112.24
    assert r.to_original_payment is False
    assert r.confirmed is False
    assert r.error  # carries the "needs human verify" reason


def test_claim_failed_still_pending():
    # No Refund line yet -> failed (the claim screen still shows credits).
    r = claim_succeeded(STILL_PENDING, CFG)
    assert r.outcome == "failed"
    assert r.confirmed is False


def test_claim_failed_unparseable():
    r = claim_succeeded("Verifying you are human", CFG)
    assert r.outcome == "failed"
    assert r.confirmed is False


def test_claim_refund_to_credits_is_wrong_method():
    # A Refund line that landed as CREDITS (no original-payment banner) must
    # NOT be reported as a win — detect() doesn't veto credits, claim does.
    credits = (
        "We've issued a $112.24 refund to your DoorDash credits balance\n"
        "Subtotal\n$95.00\nTotal\n$112.24\nRefund\n-$112.24"
    )
    r = claim_succeeded(credits, CFG)
    assert r.outcome == "wrong_method"
    assert r.confirmed is False
    assert r.to_original_payment is False


# ── pending_claim TWO variants (live finding, Heidi vs Wendy) ────────────────

def test_remake_offer_variant_detected():
    # Heidi's $107.01: Resolution -> remake offer page (must click Get refund).
    heidi = ("Dairy Queen can remake your order\nReview new order\n"
             "Get refund\nWe're sorry your order had an issue.")
    assert is_remake_offer_page(heidi) is True


def test_direct_variant_not_remake_offer():
    # Wendy's $112.24: Resolution -> straight to credits-vs-card, no remake.
    wendy = ("Choose your refund method\n$112.24 credits\n"
             "$112.24 to your original payment method")
    assert is_remake_offer_page(wendy) is False
    assert is_remake_offer_page("") is False


# ── radio-selection verification (order-independent) ─────────────────────────

def test_original_selected_when_original_checked():
    assert decide_original_selected(
        ["$112.24 to your original payment method"]) is True


def test_credits_selected_aborts():
    assert decide_original_selected(["$112.24 credits"]) is False


def test_original_wins_even_if_credits_also_checked_first():
    # A stray credits-labelled checked element appearing BEFORE the original
    # radio must not abort — original-checked-anywhere wins (the bug gemini
    # caught: an early-credits element returned False prematurely).
    checked = ["some credits toggle", "$112.24 to your original payment method"]
    assert decide_original_selected(checked) is True


def test_empty_checked_is_optimistic():
    # Nothing reported checked -> optimistic True (the receipt is the guard).
    assert decide_original_selected([]) is True
