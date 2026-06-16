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

import pytest

from backend.browser.refund_detector import RefundResult
from backend.models import RefundStatus
from backend import refund_run
from backend.refund_run import _scope_customers, _scope_dict, resolution_write


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


# ── scope resolution: WHICH customers a refund command touches (real money,
#    so the precedence + filtering must be pinned). ──

def test_scope_dict_prefers_ids_over_bucket():
    # --ids wins over --bucket (a caller targeting specific customers must not
    # accidentally fan out to the whole bucket).
    assert _scope_dict("2026-06-16", [17, 20]) == {"customer_ids": [17, 20]}


def test_scope_dict_bucket_when_no_ids():
    assert _scope_dict("2026-06-16", None) == {"bucket_date": "2026-06-16"}


def test_scope_dict_both_none_is_bucket_none():
    # Both absent -> {"bucket_date": None}. This is SAFE: RunManager resolves it
    # with a truthiness check (scope.get("bucket_date") is falsy) -> selects
    # NOBODY, not "all bucketless customers". main() also guards this upstream.
    assert _scope_dict(None, None) == {"bucket_date": None}


_CUSTS = [
    {"id": 17, "bucket_date": "2026-06-16"},
    {"id": 20, "bucket_date": "2026-06-16"},
    {"id": 99, "bucket_date": "2026-06-15"},
]


@pytest.fixture
def patched_customers(monkeypatch):
    """Point refund_run's db.list_customers at the fixed _CUSTS pool."""
    async def fake_list():
        return list(_CUSTS)
    monkeypatch.setattr(refund_run.db, "list_customers", fake_list)


async def test_scope_customers_filters_by_ids(patched_customers):
    out = await _scope_customers(None, [17, 99])
    assert {c["id"] for c in out} == {17, 99}


async def test_scope_customers_ids_win_over_bucket(patched_customers):
    # given BOTH, ids win (consistent with _scope_dict) — a stray bucket arg
    # must not widen an id-scoped run.
    out = await _scope_customers("2026-06-15", [17])
    assert [c["id"] for c in out] == [17]      # NOT the 2026-06-15 customer 99


async def test_scope_customers_filters_by_bucket(patched_customers):
    out = await _scope_customers("2026-06-16", None)
    assert {c["id"] for c in out} == {17, 20}


async def test_scope_customers_both_none_is_empty(patched_customers):
    assert await _scope_customers(None, None) == []
