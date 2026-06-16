"""Zero-tolerance verification gates — lock in 'never hallucinate a refund'.

These tests pin the asymmetric-caution contract: anything short of POSITIVE
proof of a refund to the original card must NOT be reported as done. A real
refund mislabelled `unconfirmed` only costs a re-check; a non-card refund
mislabelled `refunded` costs real money — so the gates always fail toward
caution. If a future change loosens this, one of these tests breaks.
"""
from __future__ import annotations

from backend.browser.chat_strategy import has_card_confirmation
from backend.browser.claim import claim_succeeded
from backend.config import DEFAULT_SETTINGS
from backend.models import RefundStatus as S
from backend.runner import reconcile_detect_status as R

CFG = DEFAULT_SETTINGS["refund_signal"]


# ── claim gate: only refund-line + card-banner is success ────────────────────

def test_claim_only_card_banner_is_success():
    card = ("We've issued a $50.00 refund to your original payment method\n"
            "Total\n$50.00\nRefund\n-$50.00")
    assert claim_succeeded(card, CFG).outcome == "success"


def test_claim_refund_line_no_banner_never_success():
    # The exact former-hallucination: refund line, no card proof → unconfirmed.
    no_banner = "Total\n$50.00\nRefund\n-$50.00"
    r = claim_succeeded(no_banner, CFG)
    assert r.outcome == "unconfirmed"
    assert r.confirmed is False


# ── reconcile_detect_status: no silent regression, only refunded promotes ────

def test_refunded_reading_always_wins():
    assert R("unconfirmed", S.refunded) == S.refunded
    assert R("pending_claim", S.refunded) == S.refunded
    assert R("not_refunded", S.refunded) == S.refunded


def test_inconclusive_never_downgrades():
    assert R("refunded", S.unknown) == S.refunded
    assert R("unconfirmed", S.unknown) == S.unconfirmed
    assert R("pending_claim", S.unchecked) == S.pending_claim


def test_unconfirmed_only_cleared_by_refunded():
    # An agent promised it (unconfirmed); a later 'not_refunded'/'partial' read
    # does NOT clear it — a human still reconciles promise vs. what posted.
    assert R("unconfirmed", S.not_refunded) == S.unconfirmed
    assert R("unconfirmed", S.partial) == S.unconfirmed


def test_normal_progression_allowed():
    assert R("not_refunded", S.pending_claim) == S.pending_claim
    assert R(None, S.not_refunded) == S.not_refunded
    assert R("unchecked", S.not_refunded) == S.not_refunded


def test_weak_read_does_not_clobber_stronger():
    assert R("pending_claim", S.not_refunded) == S.pending_claim


def test_refunded_is_never_lost_by_any_later_read():
    # THE money-protection invariant: a receipt-proven `refunded` order must
    # survive EVERY later detect reading. A transient/weak re-read (or even a
    # contradictory not_refunded) must never silently un-refund money.
    for later in (S.not_refunded, S.partial, S.pending_claim, S.unconfirmed,
                  S.remake, S.unknown, S.unchecked):
        assert R("refunded", later) == S.refunded, later


def test_garbage_current_status_defaults_to_unchecked():
    # An unparseable / empty / missing stored status must not crash; it's
    # treated as `unchecked` (rank 0) so a real fresh WEAK read wins cleanly.
    # (Use weak `detected` values so the current-parsing fallback is actually
    # exercised — a `refunded` detected would early-return before parsing.)
    assert R("not_a_real_status", S.not_refunded) == S.not_refunded
    assert R("", S.pending_claim) == S.pending_claim
    assert R(None, S.not_refunded) == S.not_refunded


def test_unconfirmed_outranks_pending_claim_on_weak_read():
    # unconfirmed (rank 3) stored must not be clobbered by a weaker pending_claim
    # (rank 2) read — only a `refunded` read clears unconfirmed (tested above).
    assert R("unconfirmed", S.pending_claim) == S.unconfirmed


# ── chat gate: agent confirmation must name the amount AND the card ──────────

PHRASES = ["refund", "refunded", "processed", "issued"]


def test_card_confirmation_requires_amount_and_card():
    text = "Your refund of $106.81 has been issued to your original payment method"
    assert has_card_confirmation(text, 106.81, PHRASES) is True


def test_card_confirmation_rejects_missing_amount():
    text = "Your refund has been issued to your original card"
    assert has_card_confirmation(text, 106.81, PHRASES) is False


def test_card_confirmation_rejects_missing_card_destination():
    text = "Your refund of $106.81 has been processed"
    assert has_card_confirmation(text, 106.81, PHRASES) is False


def test_card_confirmation_rejects_credits():
    text = "Your $106.81 has been issued to your original payment method as credits"
    assert has_card_confirmation(text, 106.81, PHRASES) is False


def test_card_confirmation_rejects_wrong_amount():
    # Agent confirms a DIFFERENT amount than this order's — not a confirmation.
    text = "Your refund of $50.00 has been issued to your original card"
    assert has_card_confirmation(text, 106.81, PHRASES) is False


def test_card_confirmation_none_price_cannot_confirm():
    text = "Your refund has been issued to your original payment method"
    assert has_card_confirmation(text, None, PHRASES) is False


def test_card_confirmation_blank_phrase_does_not_falsely_pass():
    # A misconfigured blank phrase must NOT make every reply "confirmed"
    # (`"" in text` is always True). Reply has amount+card but no real phrase.
    text = "$106.81 to your original payment method"
    assert has_card_confirmation(text, 106.81, ["", "  "]) is False
