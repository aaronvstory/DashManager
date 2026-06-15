"""Regression guard: open_receipt must RAISE SessionExpiredError when an order
URL redirects to login — never silently return the login page's text as if it
were a receipt.

This locks in the fix for the Traci-class bug: an expired session bounced
/orders/<uuid> to identity.doordash.com, open_receipt returned the login text,
and the order's refund truth couldn't be read — yet nothing surfaced the expiry.
Now it raises, so the runner can auto-heal (re-login + retry).
"""
from __future__ import annotations

import asyncio

import pytest

from backend.browser.driver import SessionExpiredError
from backend.browser.orders import open_receipt


class _FakePage:
    """Minimal async page stub: pretends a goto landed on `final_url`."""

    def __init__(self, final_url: str, body: str = "Subtotal $1\nTotal $1"):
        self.url = final_url
        self._body = body

    async def goto(self, url, **kwargs):
        return None  # `self.url` is preset to simulate the post-redirect URL

    async def evaluate(self, _script):
        return self._body


def _run(coro):
    return asyncio.run(coro)


def test_open_receipt_raises_on_login_redirect(monkeypatch):
    # Neutralize the human-paced sleep + Cloudflare handler so the test is fast.
    import backend.browser.orders as orders
    monkeypatch.setattr(orders, "handle_cloudflare",
                        lambda page: asyncio.sleep(0))
    monkeypatch.setattr(orders, "human_pause",
                        lambda a, b: asyncio.sleep(0))

    page = _FakePage("https://identity.doordash.com/auth?client_id=...")
    with pytest.raises(SessionExpiredError):
        _run(open_receipt(page, "https://www.doordash.com/orders/abc-123"))


def test_open_receipt_returns_text_on_real_receipt(monkeypatch):
    import backend.browser.orders as orders
    monkeypatch.setattr(orders, "handle_cloudflare",
                        lambda page: asyncio.sleep(0))
    monkeypatch.setattr(orders, "human_pause",
                        lambda a, b: asyncio.sleep(0))

    page = _FakePage("https://www.doordash.com/orders/abc-123",
                     body="Subtotal\n$50\nTotal\n$50\nRefund\n-$50")
    text = _run(open_receipt(page, "https://www.doordash.com/orders/abc-123"))
    assert "Refund" in text
