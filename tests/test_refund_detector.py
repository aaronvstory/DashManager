"""Fixture-driven tests for the pure refund detector."""
import pytest

from backend.browser.refund_detector import detect
from backend.config import DEFAULT_SETTINGS
from backend.models import RefundStatus

CFG = DEFAULT_SETTINGS["refund_signal"]

# Verbatim breakdown shape from a live receipt page (2026-06).
REAL_BREAKDOWN_REFUNDED = (
    "Subtotal\n$50.95\nDelivery Fee\n$0.00\nService Fee\n$7.64\n"
    "Estimated Tax\n$3.95\nDasher Tip\n$49.80\nTotal\n$112.34\n"
    "Refund\n-$112.34"
)

RECEIPT_PAGE_REFUNDED = (
    "DoorDash\nYour receipt\nTaco Bell\nOrder #abc123\n"
    "3 items\n" + REAL_BREAKDOWN_REFUNDED + "\nPaid with Visa\nGet help"
)

CANCELLED_NO_REFUND = (
    "DoorDash\nYour order was canceled, we'll help you find a replacement\n"
    "Taco Bell\nSubtotal\n$50.95\nDelivery Fee\n$0.00\nService Fee\n$7.64\n"
    "Estimated Tax\n$3.95\nDasher Tip\n$49.80\nTotal\n$112.34\nGet help"
)

PARTIAL_REFUND = (
    "Subtotal\n$50.95\nService Fee\n$7.64\nEstimated Tax\n$3.95\n"
    "Dasher Tip\n$49.80\nTotal\n$112.34\nRefund\n-$50.00"
)

COMMA_AMOUNTS = (
    "Subtotal\n$1,150.00\nService Fee\n$84.56\n"
    "Total\n$1,234.56\nRefund\n-$1,234.56"
)

SAME_LINE_VARIANT = (
    "Subtotal $50.95\nService Fee $7.64\nEstimated Tax $3.95\n"
    "Dasher Tip $49.80\nTotal $112.34\nRefund -$112.34"
)

CLOUDFLARE_JUNK = (
    "www.doordash.com\nVerifying you are human. This may take a few "
    "seconds.\nwww.doordash.com needs to review the security of your "
    "connection before proceeding."
)

REFUND_WITHOUT_TOTAL = "Some broken page\nRefund\n-$112.34\nGet help"

# Marketing copy mentions a parseable "Total" before the real breakdown:
# the LAST occurrence must win.
MARKETING_TOTAL_FIRST = (
    "DashPass members saved big this month\nTotal\n$5.00\n"
    "in fees waived on this order!\n"
    "Subtotal\n$50.95\nService Fee\n$7.64\nTotal\n$112.34\nGet help"
)

NOT_REFUNDED_PLAIN = (
    "Subtotal\n$50.95\nService Fee\n$7.64\nEstimated Tax\n$3.95\n"
    "Total\n$62.54\nPaid with Visa"
)


def test_real_breakdown_full_refund():
    r = detect(RECEIPT_PAGE_REFUNDED, CFG)
    assert r.status == RefundStatus.refunded
    assert r.total_amount == pytest.approx(112.34)
    assert r.refund_amount == pytest.approx(112.34)
    assert r.cancelled_text_seen is False


def test_cancelled_text_alone_is_not_refunded():
    # Cancellation prose proves nothing about money — still not_refunded.
    r = detect(CANCELLED_NO_REFUND, CFG)
    assert r.status == RefundStatus.not_refunded
    assert r.total_amount == pytest.approx(112.34)
    assert r.refund_amount is None
    assert r.cancelled_text_seen is True


def test_partial_refund():
    r = detect(PARTIAL_REFUND, CFG)
    assert r.status == RefundStatus.partial
    assert r.total_amount == pytest.approx(112.34)
    assert r.refund_amount == pytest.approx(50.00)


def test_comma_formatted_amounts():
    r = detect(COMMA_AMOUNTS, CFG)
    assert r.status == RefundStatus.refunded
    assert r.total_amount == pytest.approx(1234.56)
    assert r.refund_amount == pytest.approx(1234.56)


def test_same_line_label_value_variant():
    r = detect(SAME_LINE_VARIANT, CFG)
    assert r.status == RefundStatus.refunded
    assert r.total_amount == pytest.approx(112.34)
    assert r.refund_amount == pytest.approx(112.34)


def test_cloudflare_interstitial_is_unknown():
    r = detect(CLOUDFLARE_JUNK, CFG)
    assert r.status == RefundStatus.unknown
    assert r.total_amount is None
    assert r.cancelled_text_seen is False


def test_empty_page_is_unknown():
    r = detect("", CFG)
    assert r.status == RefundStatus.unknown
    assert r.total_amount is None
    assert r.refund_amount is None


def test_refund_without_total_is_unknown():
    # Never silently pass: a Refund with no parseable Total is unparseable.
    r = detect(REFUND_WITHOUT_TOTAL, CFG)
    assert r.status == RefundStatus.unknown
    assert r.total_amount is None
    assert r.refund_amount == pytest.approx(112.34)


def test_marketing_total_before_breakdown_uses_last():
    r = detect(MARKETING_TOTAL_FIRST, CFG)
    assert r.status == RefundStatus.not_refunded
    assert r.total_amount == pytest.approx(112.34)


def test_total_without_refund_or_cancel_text():
    r = detect(NOT_REFUNDED_PLAIN, CFG)
    assert r.status == RefundStatus.not_refunded
    assert r.total_amount == pytest.approx(62.54)
    assert r.refund_amount is None
    assert r.cancelled_text_seen is False


def test_subtotal_is_not_mistaken_for_total():
    text = "Subtotal\n$50.95\nTotal\n$62.54"
    r = detect(text, CFG)
    assert r.total_amount == pytest.approx(62.54)


def test_label_match_tolerates_case_and_colon():
    text = "subtotal\n$50.95\nTOTAL:\n$62.54\nRefund: -$62.54"
    r = detect(text, CFG)
    assert r.status == RefundStatus.refunded
    assert r.total_amount == pytest.approx(62.54)
    assert r.refund_amount == pytest.approx(62.54)


def test_prose_containing_label_word_does_not_match():
    text = "Total savings of $5.00 with DashPass\nTotal\n$62.54"
    r = detect(text, CFG)
    assert r.status == RefundStatus.not_refunded
    assert r.total_amount == pytest.approx(62.54)


def test_zero_dollar_refund_is_not_refunded():
    text = "Total\n$62.54\nRefund\n$0.00"
    r = detect(text, CFG)
    assert r.status == RefundStatus.not_refunded
    assert r.refund_amount == pytest.approx(0.0)


CFG_FULL = {
    "total_label": "Total", "refund_label": "Refund",
    "cancelled_texts": ["order was canceled", "order cancelled"],
    "pending_texts": ["pending refund", "choose your refund method",
                      "to original payment method"],
}


def test_pending_claim_choose_refund_method():
    txt = ("Your order was canceled, we'll help you find a replacement\n"
           "Choose your refund method\n"
           "$112.44 credits\n$112.44 to original payment method\n"
           "Subtotal\n$50.95\nTotal\n$112.44")
    r = detect(txt, CFG_FULL)
    assert r.status == RefundStatus.pending_claim
    assert r.total_amount == 112.44


def test_pending_refund_badge():
    txt = "Order Cancelled\nPending Refund\nSubtotal\n$50.95\nTotal\n$106.81"
    assert detect(txt, CFG_FULL).status == RefundStatus.pending_claim


def test_real_refund_beats_pending_banner():
    # A real Refund line wins even if a stale claim banner lingers.
    txt = ("Choose your refund method\nTotal\n$112.44\nRefund\n-$112.44")
    assert detect(txt, CFG_FULL).status == RefundStatus.refunded


def test_canceled_no_refund_no_pending_is_chat():
    txt = ("Your order was canceled, we'll help you find a replacement\n"
           "Subtotal\n$50.95\nTotal\n$106.71\nPayment\nVisa")
    assert detect(txt, CFG_FULL).status == RefundStatus.not_refunded


def test_remake_no_refund_is_remake_status():
    # A remade order with no refund line -> remake (routed to chat, flagged).
    txt = ("We remade your order\nSubtotal\n$50.95\nTotal\n$62.54\n"
           "Paid with Visa")
    r = detect(txt, CFG)
    assert r.status == RefundStatus.remake
    assert r.remake_seen is True


def test_remake_with_refund_line_is_refunded():
    # A real Refund line still wins over the remake flag — but remake_seen
    # is preserved for the audit.
    txt = ("We remade your order\nSubtotal\n$50.95\nTotal\n$62.54\n"
           "Refund\n-$62.54")
    r = detect(txt, CFG)
    assert r.status == RefundStatus.refunded
    assert r.remake_seen is True


def test_remake_flag_off_by_default():
    r = detect(NOT_REFUNDED_PLAIN, CFG)
    assert r.status == RefundStatus.not_refunded
    assert r.remake_seen is False


def test_remake_pending_claim_still_claimable():
    # A remade order that ALSO offers a self-claim screen -> pending_claim
    # (self-claim beats chatting); remake_seen still recorded.
    txt = ("We remade your order\nChoose your refund method\n"
           "Subtotal\n$50.95\nTotal\n$62.54")
    r = detect(txt, CFG)
    assert r.status == RefundStatus.pending_claim
    assert r.remake_seen is True


# ── Safety-critical edges: these pin behavior the money classification relies
#    on, so a future _label_amounts refactor can't silently regress it. ──

def test_multiple_refund_lines_last_wins():
    # A stray "Refund" amount in summary/marketing copy BEFORE the real
    # breakdown must not win — the LAST Refund occurrence does (same rule as
    # Total). Here a $5.00 mention precedes the real -$112.34 refund line.
    txt = ("Refund\n-$5.00\nin a promo blurb\n"
           "Subtotal\n$50.95\nTotal\n$112.34\nRefund\n-$112.34")
    r = detect(txt, CFG)
    assert r.status == RefundStatus.refunded
    assert r.refund_amount == pytest.approx(112.34)   # last wins, NOT $5.00
    assert r.total_amount == pytest.approx(112.34)


def test_refund_prose_is_not_captured_as_amount():
    # "Refunded to your Visa..." is prose, not a Refund breakdown line — it must
    # NOT be read as a refund amount (the label-exact / money-token rule). A
    # false positive here would mislabel money as refunded.
    txt = "Total\n$62.54\nRefunded to your Visa card ending 1234\nGet help"
    r = detect(txt, CFG)
    assert r.status == RefundStatus.not_refunded
    assert r.refund_amount is None


def test_refund_exceeding_total_is_refunded():
    # Refund can edge slightly OVER total (e.g. a tip adjustment): refund >=
    # total -> refunded, never misclassified as partial.
    txt = "Total\n$112.34\nRefund\n-$112.35"
    r = detect(txt, CFG)
    assert r.status == RefundStatus.refunded
    assert r.refund_amount == pytest.approx(112.35)
    assert r.total_amount == pytest.approx(112.34)


def test_refund_label_value_on_later_line_after_blank():
    # The bare-label path skips blank lines to the value (real innerText often
    # has empty lines between a label and its amount).
    txt = "Subtotal\n$50.95\nTotal\n\n$112.34\nRefund\n\n-$112.34"
    r = detect(txt, CFG)
    assert r.status == RefundStatus.refunded
    assert r.total_amount == pytest.approx(112.34)
    assert r.refund_amount == pytest.approx(112.34)
