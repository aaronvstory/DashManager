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


# ── detect_customer_via_cdp write decisions (mock login + DB) ──────────────────
async def test_cdp_detect_promotes_only_proven_and_records_partial(monkeypatch):
    """The CDP detect path must: promote a proven full refund -> refunded;
    record a partial refund's amount but keep it `unconfirmed` (not dropped,
    not promoted); leave not_refunded untouched. Mirrors the legacy gate.
    """
    from backend import relogin

    ORDERS = [
        {"id": 1, "order_uuid": "u-full", "refund_status": "unconfirmed",
         "receipt_url": "https://x/orders/u-full", "price": 100.0},
        {"id": 2, "order_uuid": "u-partial", "refund_status": "unconfirmed",
         "receipt_url": "https://x/orders/u-partial", "price": 80.0},
        {"id": 3, "order_uuid": "u-none", "refund_status": "not_refunded",
         "receipt_url": "https://x/orders/u-none", "price": 60.0},
    ]
    by_uuid = {
        "u-full": dict(status="refunded", total=100.0, refund=100.0),
        "u-partial": dict(status="partial", total=80.0, refund=30.0),
        "u-none": dict(status="not_refunded", total=60.0, refund=None),
    }
    writes: list[tuple] = []

    async def fake_get_customer(_cid):
        return {"id": 44, "email": "x@y.z", "number_token": "tok",
                "api_url": "", "mirror_hosts": "[]", "password": "pw"}

    async def fake_list_orders(_cid):
        return list(ORDERS)

    async def fake_get_setting(key):
        return {} if key in ("refund_signal", "daisy") else {}

    async def fake_resolve_pw(_c):
        return "pw"

    async def fake_update(order_id, status, total, amount):
        writes.append((order_id, status, total, amount))

    # phone_login_via_cdp runs the after_login callback and returns its rows.
    def fake_login(email, *, poll_otp, after_login=None, **kw):
        rows = []
        for o in ORDERS:
            sig = by_uuid[o["order_uuid"]]
            rows.append({"id": o["id"], "uuid": o["order_uuid"],
                         "status": sig["status"], "total": sig["total"],
                         "refund": sig["refund"], "card_block": False,
                         "credits": False, "readable": True})
        return {"outcome": "logged_in", "storage_state": None,
                "after_login": rows}

    class _FakeBridge:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    monkeypatch.setattr(relogin.db, "get_customer", fake_get_customer)
    monkeypatch.setattr(relogin.db, "list_orders", fake_list_orders)
    monkeypatch.setattr(relogin.db, "get_setting", fake_get_setting)
    monkeypatch.setattr(relogin.db, "update_order_refund", fake_update)
    monkeypatch.setattr(relogin, "_resolve_password", fake_resolve_pw)
    monkeypatch.setattr(relogin, "DaisyBridge", _FakeBridge)
    monkeypatch.setattr(
        "backend.browser.cdp_login.phone_login_via_cdp", fake_login)

    res = await relogin.detect_customer_via_cdp(44, headless=True)

    assert res["promoted"] == 1  # only the full refund
    w = {row[0]: row for row in writes}
    assert w[1] == (1, "refunded", 100.0, 100.0)        # full -> refunded
    assert w[2] == (2, "unconfirmed", 80.0, 30.0)       # partial -> recorded amt
    assert 3 not in w                                   # not_refunded untouched
