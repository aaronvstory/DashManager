"""Pure tests for refund_run's write-decision (no browser, no DB).

`resolution_write` is the single safety-critical decision in the run: given what
detect() read off a receipt, what status + amount gets persisted? The
zero-tolerance contract:
  - ONLY a full receipt-proven refund writes `refunded` (and counts as promoted).
  - a PARTIAL refund writes `partial` — NEVER `refunded` (the shortfall must
    stay visible so the chat step can pursue it).
  - everything else passes through, none promoted.

There is deliberately NO price-based order matching in refund_run — orders can
share a price or sit cents apart, so reconciliation is UUID-driven only.
"""
from __future__ import annotations

from backend.browser.refund_detector import RefundResult
from backend.models import RefundStatus
from backend.refund_run import resolution_write


def test_full_refund_is_refunded_and_promoted():
    rr = RefundResult(status=RefundStatus.refunded, total_amount=50.0,
                      refund_amount=50.0)
    status, total, amount, promoted = resolution_write(rr, 50.0)
    assert status == "refunded"
    assert amount == 50.0
    assert promoted is True


def test_partial_never_written_as_refunded():
    # THE critical case: a partial refund must NOT be marked refunded — the
    # unrecovered delta would be silently lost.
    rr = RefundResult(status=RefundStatus.partial, total_amount=50.0,
                      refund_amount=8.0)
    status, total, amount, promoted = resolution_write(rr, 50.0)
    assert status == "partial"
    assert amount == 8.0
    assert total == 50.0
    assert promoted is False


def test_not_refunded_passthrough_not_promoted():
    rr = RefundResult(status=RefundStatus.not_refunded, total_amount=50.0,
                      refund_amount=None)
    status, _total, _amount, promoted = resolution_write(rr, 50.0)
    assert status == "not_refunded"
    assert promoted is False


def test_pending_claim_passthrough_not_promoted():
    rr = RefundResult(status=RefundStatus.pending_claim, total_amount=50.0,
                      refund_amount=None)
    status, _t, _a, promoted = resolution_write(rr, 50.0)
    assert status == "pending_claim"
    assert promoted is False


def test_unknown_receipt_not_promoted():
    # An unreadable receipt (unknown) must never count as a refund.
    rr = RefundResult(status=RefundStatus.unknown, total_amount=None,
                      refund_amount=None)
    status, _t, _a, promoted = resolution_write(rr, 50.0)
    assert status == "unknown"
    assert promoted is False


def test_refund_amount_falls_back_to_price_when_missing():
    # Full refund detected but the parsed amount is None -> use the order price,
    # not None (so the DB still records a number for the confirmed refund).
    rr = RefundResult(status=RefundStatus.refunded, total_amount=None,
                      refund_amount=None)
    _status, _total, amount, promoted = resolution_write(rr, 42.5)
    assert amount == 42.5
    assert promoted is True
