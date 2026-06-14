"""Pure tests for the refund_run twin-matcher (no browser, no DB).

The synthetic-UUID gotcha: a card-based pending_claim is stored as
`pendingclaim:store:N` with no receipt URL. After claiming, only a FRESH scrape
reveals the real `/orders/<uuid>`. `_match_twin` pairs the stored row to its
fresh-scrape twin so we can reopen the real receipt and verify — by exact UUID
when real, else by store + price within tolerance.
"""
from __future__ import annotations

from dataclasses import dataclass

from backend.refund_run import _match_twin


@dataclass
class FakeOrder:
    order_uuid: str
    price: float | None
    store_name: str
    receipt_url: str = "https://www.doordash.com/orders/x"


def test_match_by_real_uuid():
    fresh = [FakeOrder("abc-123", 50.0, "Dairy Queen")]
    dbo = {"order_uuid": "abc-123", "price": 50.0, "store_name": "Dairy Queen"}
    assert _match_twin(dbo, fresh) is fresh[0]


def test_synthetic_uuid_matches_by_store_and_price():
    fresh = [FakeOrder("real-uuid-999", 119.41, "Dairy Queen")]
    dbo = {"order_uuid": "pendingclaim:Dairy Queen:1", "price": 119.41,
           "store_name": "Dairy Queen"}
    assert _match_twin(dbo, fresh) is fresh[0]


def test_price_within_tolerance():
    fresh = [FakeOrder("real-1", 119.40, "Dairy Queen")]
    dbo = {"order_uuid": "pendingclaim:Dairy Queen:1", "price": 119.41,
           "store_name": "Dairy Queen"}
    assert _match_twin(dbo, fresh) is fresh[0]


def test_no_match_when_price_too_far():
    fresh = [FakeOrder("real-1", 200.0, "Dairy Queen")]
    dbo = {"order_uuid": "pendingclaim:Dairy Queen:1", "price": 119.41,
           "store_name": "Dairy Queen"}
    assert _match_twin(dbo, fresh) is None


def test_no_match_when_store_differs():
    fresh = [FakeOrder("real-1", 119.41, "Wendy's")]
    dbo = {"order_uuid": "pendingclaim:Dairy Queen:1", "price": 119.41,
           "store_name": "Dairy Queen"}
    assert _match_twin(dbo, fresh) is None


def test_picks_correct_twin_among_several():
    fresh = [
        FakeOrder("u-a", 119.31, "Dairy Queen"),
        FakeOrder("u-b", 119.41, "Dairy Queen"),
    ]
    dbo = {"order_uuid": "pendingclaim:Dairy Queen:2", "price": 119.41,
           "store_name": "Dairy Queen"}
    assert _match_twin(dbo, fresh) is fresh[1]
