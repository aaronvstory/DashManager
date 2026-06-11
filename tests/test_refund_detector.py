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
