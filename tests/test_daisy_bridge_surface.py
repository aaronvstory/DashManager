"""Slice 1: DaisyBridge surface methods map to the right worker command + args.

Stubs `_call` so no subprocess/CustomerDaisy is needed — verifies each method
sends the expected command name + args and unwraps the result key correctly.
"""
import pytest

from backend.daisy.bridge import DaisyBridge


@pytest.fixture
def bridge(monkeypatch):
    b = DaisyBridge(root="/fake", python="/fake/python")
    calls = []

    async def fake_call(cmd, args=None, timeout=90):
        calls.append((cmd, args or {}))
        # Return whatever shape each method unwraps.
        return {
            "list_customers": {"customers": [{"customer_id": "x"}]},
            "customer_count": {"count": 7},
            "get_customer": {"customer": {"customer_id": "cd-1"}},
            "update_customer": {"customer": {"city": "Sparks"}, "updated": True},
            "delete_customer": {"deleted": True},
            "export": {"format": "csv", "text": "a,b\n1,2\n"},
            "list_addresses": {"addresses": [{"full_address": "1 Main St"}]},
            "add_address": {"addresses": [{"full_address": "1 Main St"},
                                          {"full_address": "2 Oak Ave"}]},
            "update_address": {"addresses": [{"full_address": "1 Main UPDATED"}]},
            "delete_address": {"addresses": []},
            "generate_address": {"address": {"full_address": "9 Oak Ave"}},
        }[cmd]

    monkeypatch.setattr(b, "_call", fake_call)
    b._calls = calls  # type: ignore[attr-defined]
    return b


async def test_list_customers(bridge):
    out = await bridge.list_customers(limit=50)
    assert out == [{"customer_id": "x"}]
    assert bridge._calls[-1] == ("list_customers", {"limit": 50})


async def test_customer_count(bridge):
    assert await bridge.customer_count() == 7
    assert bridge._calls[-1][0] == "customer_count"


async def test_get_customer(bridge):
    assert (await bridge.get_customer("cd-1"))["customer_id"] == "cd-1"
    assert bridge._calls[-1] == ("get_customer", {"customer_id": "cd-1"})


async def test_update_customer(bridge):
    out = await bridge.update_customer("cd-1", {"city": "Sparks"})
    assert out == {"city": "Sparks"}
    assert bridge._calls[-1] == (
        "update_customer", {"customer_id": "cd-1", "fields": {"city": "Sparks"}})


async def test_delete_customer(bridge):
    assert await bridge.delete_customer("cd-2") is True
    assert bridge._calls[-1] == ("delete_customer", {"customer_id": "cd-2"})


async def test_export(bridge):
    out = await bridge.export("csv", limit=100)
    assert out["format"] == "csv"
    assert bridge._calls[-1] == ("export", {"format": "csv", "limit": 100})


async def test_list_addresses(bridge):
    out = await bridge.list_addresses()
    assert out == [{"full_address": "1 Main St"}]
    assert bridge._calls[-1][0] == "list_addresses"


async def test_add_address(bridge):
    out = await bridge.add_address({"full_address": "2 Oak Ave"})
    assert [a["full_address"] for a in out] == ["1 Main St", "2 Oak Ave"]
    assert bridge._calls[-1] == (
        "add_address", {"address": {"full_address": "2 Oak Ave"}})


async def test_update_address(bridge):
    out = await bridge.update_address(0, {"full_address": "1 Main UPDATED"})
    assert out == [{"full_address": "1 Main UPDATED"}]
    assert bridge._calls[-1] == (
        "update_address",
        {"index": 0, "address": {"full_address": "1 Main UPDATED"}})


async def test_delete_address(bridge):
    out = await bridge.delete_address(0)
    assert out == []
    assert bridge._calls[-1] == ("delete_address", {"index": 0})


async def test_generate_address(bridge):
    out = await bridge.generate_address("1 Main St, Reno, NV", radius_miles=3.0)
    assert out == {"full_address": "9 Oak Ave"}
    assert bridge._calls[-1] == (
        "generate_address",
        {"origin_address": "1 Main St, Reno, NV", "radius_miles": 3.0})
