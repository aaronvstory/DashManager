"""Slice 2: /api/daisy routes — stubbed bridge, no CustomerDaisy live.

Patches backend.routes.daisy._bridge to return a fake async-context bridge so
the routes are exercised end-to-end (shaping, in_dashmanager tagging, export
headers, 404s) without a subprocess.
"""
from __future__ import annotations

import httpx
import pytest

from backend import db


class _FakeBridge:
    """Async-context stand-in for DaisyBridge with the Slice-1 surface."""

    def __init__(self, rows: list[dict], *, get=None, updated=None,
                 deleted=True, export_text="x"):
        self._rows = rows
        self._get = get
        self._updated = updated
        self._deleted = deleted
        self._export_text = export_text
        self.calls: list[tuple] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def list_customers(self, limit=200):
        self.calls.append(("list_customers", limit))
        return self._rows

    async def customer_count(self):
        return len(self._rows)

    async def analytics(self, limit=100000):
        self.calls.append(("analytics", limit))
        return {"total": 2, "verified": 1, "unverified": 1,
                "by_state": [{"key": "NV", "count": 2}],
                "by_city": [{"key": "Reno", "count": 2}]}

    async def get_customer(self, cid):
        self.calls.append(("get_customer", cid))
        return self._get

    async def update_customer(self, cid, fields):
        self.calls.append(("update_customer", cid, fields))
        return self._updated

    async def delete_customer(self, cid):
        self.calls.append(("delete_customer", cid))
        return self._deleted

    async def export(self, fmt, limit):
        self.calls.append(("export", fmt, limit))
        return {"format": fmt, "text": self._export_text}

    async def list_addresses(self):
        self.calls.append(("list_addresses",))
        return [{"name": "Home", "full_address": "1 Main St, Reno, NV"}]

    async def add_address(self, address):
        # The route now rejects a blank full_address at the Pydantic edge (422),
        # so the bridge only ever sees a clean, non-empty address here.
        self.calls.append(("add_address", address))
        return [{"full_address": "1 Main St, Reno, NV"}, address]

    async def update_address(self, index, address):
        self.calls.append(("update_address", index, address))
        from backend.daisy.bridge import DaisyError
        if index != 0:
            raise DaisyError(f"update_address failed: address index {index} "
                             "out of range (0..0)")
        return [address]

    async def delete_address(self, index):
        self.calls.append(("delete_address", index))
        from backend.daisy.bridge import DaisyError
        if index != 0:
            raise DaisyError(f"delete_address failed: address index {index} "
                             "out of range (0..0)")
        return []

    async def generate_address(self, origin_address, radius_miles=5.0):
        self.calls.append(("generate_address", origin_address, radius_miles))
        # mimics the worker. Sentinels: "miss" -> None (MapQuest found nothing);
        # "boom" -> DaisyError (an upstream MapQuest/worker failure).
        from backend.daisy.bridge import DaisyError
        if origin_address == "boom":
            raise DaisyError("generate_address failed: MapQuest unreachable")
        if origin_address == "miss":
            return None
        return {"full_address": f"42 Near {origin_address}", "city": "Reno",
                "state": "NV"}


@pytest.fixture
async def client():
    from backend.main import create_app

    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://test") as c:
        yield c


def _patch_bridge(monkeypatch, bridge):
    from backend.routes import daisy as daisy_routes

    async def fake():
        return bridge

    monkeypatch.setattr(daisy_routes, "_bridge", fake)
    return bridge


async def test_list_tags_in_dashmanager_by_email(client, monkeypatch):
    # one daisy row whose email matches a DashManager customer, one that doesn't
    await db.create_customer("2026-06-16", first_name="Ada",
                             email="ada@x.net")
    rows = [{"customer_id": "cd-1", "email": "ada@x.net", "first_name": "Ada"},
            {"customer_id": "cd-2", "email": "bo@x.net", "first_name": "Bo"}]
    _patch_bridge(monkeypatch, _FakeBridge(rows))
    r = await client.get("/api/daisy")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    by_id = {c["customer_id"]: c for c in body["customers"]}
    assert by_id["cd-1"]["in_dashmanager"] is True   # email matches DM row
    assert by_id["cd-2"]["in_dashmanager"] is False


async def test_analytics_route(client, monkeypatch):
    # /analytics must be matched as a literal, NOT captured as /{customer_id}.
    b = _patch_bridge(monkeypatch, _FakeBridge([]))
    r = await client.get("/api/daisy/analytics")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2 and body["verified"] == 1
    assert body["by_state"][0] == {"key": "NV", "count": 2}
    # it hit analytics, not get_customer("analytics")
    assert any(c[0] == "analytics" for c in b.calls)
    assert not any(c[0] == "get_customer" for c in b.calls)


async def test_get_404_when_missing(client, monkeypatch):
    _patch_bridge(monkeypatch, _FakeBridge([], get=None))
    r = await client.get("/api/daisy/nope")
    assert r.status_code == 404


async def test_get_found(client, monkeypatch):
    _patch_bridge(monkeypatch, _FakeBridge(
        [], get={"customer_id": "cd-9", "email": "z@x.net"}))
    r = await client.get("/api/daisy/cd-9")
    assert r.status_code == 200
    assert r.json()["customer_id"] == "cd-9"
    assert r.json()["in_dashmanager"] is False


async def test_routes_never_leak_password(client, monkeypatch):
    # the worker's normalized row carries the plaintext password; the HTTP
    # routes MUST strip it (it would otherwise sit in the browser's cache).
    row = {"customer_id": "cd-1", "email": "a@x.net", "password": "SECRETpw",
           "first_name": "Ada"}
    _patch_bridge(monkeypatch, _FakeBridge([dict(row)], get=dict(row),
                                           updated=dict(row)))
    r = await client.get("/api/daisy")
    assert "SECRETpw" not in r.text
    assert "password" not in r.json()["customers"][0]

    r = await client.get("/api/daisy/cd-1")
    assert "SECRETpw" not in r.text and "password" not in r.json()

    r = await client.patch("/api/daisy/cd-1", json={"city": "Reno"})
    assert "SECRETpw" not in r.text and "password" not in r.json()


async def test_patch_forwards_fields(client, monkeypatch):
    b = _patch_bridge(monkeypatch, _FakeBridge(
        [], updated={"customer_id": "cd-1", "city": "Sparks"}))
    r = await client.patch("/api/daisy/cd-1", json={"city": "Sparks",
                                                    "phone": "5551112222"})
    assert r.status_code == 200
    assert r.json()["city"] == "Sparks"
    call = next(c for c in b.calls if c[0] == "update_customer")
    assert call[2] == {"city": "Sparks", "phone": "5551112222"}


async def test_patch_404(client, monkeypatch):
    _patch_bridge(monkeypatch, _FakeBridge([], updated=None))
    r = await client.patch("/api/daisy/nope", json={"city": "X"})
    assert r.status_code == 404


async def test_patch_rejects_unknown_field(client, monkeypatch):
    # extra="forbid": an unsupported key 422s instead of silently no-opping.
    _patch_bridge(monkeypatch, _FakeBridge([], updated={"customer_id": "cd-1"}))
    r = await client.patch("/api/daisy/cd-1", json={"customer_id": "HACK"})
    assert r.status_code == 422


async def test_delete(client, monkeypatch):
    _patch_bridge(monkeypatch, _FakeBridge([], deleted=True))
    r = await client.delete("/api/daisy/cd-1")
    assert r.status_code == 200 and r.json() == {"ok": True}


async def test_delete_404(client, monkeypatch):
    _patch_bridge(monkeypatch, _FakeBridge([], deleted=False))
    r = await client.delete("/api/daisy/nope")
    assert r.status_code == 404


async def test_export_csv_is_attachment(client, monkeypatch):
    _patch_bridge(monkeypatch, _FakeBridge([], export_text="a,b\n1,2\n"))
    r = await client.get("/api/daisy/export/csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert 'customerdaisy.csv' in r.headers["content-disposition"]
    assert r.text == "a,b\n1,2\n"


async def test_export_json_is_attachment(client, monkeypatch):
    _patch_bridge(monkeypatch, _FakeBridge([], export_text='[{"a": 1}]'))
    r = await client.get("/api/daisy/export/json")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert "attachment" in r.headers["content-disposition"]
    assert 'customerdaisy.json' in r.headers["content-disposition"]
    assert r.text == '[{"a": 1}]'


async def test_export_txt_is_attachment(client, monkeypatch):
    # txt is a valid export format (the worker emits it); the route must map it
    # to text/plain and serve it as an attachment, same as csv/json.
    _patch_bridge(monkeypatch, _FakeBridge([], export_text="Ada · ada@x.net\n"))
    r = await client.get("/api/daisy/export/txt")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert "attachment" in r.headers["content-disposition"]
    assert 'customerdaisy.txt' in r.headers["content-disposition"]
    assert r.text == "Ada · ada@x.net\n"


async def test_export_serves_worker_text_verbatim_no_password(client,
                                                              monkeypatch):
    # The export password-strip is the WORKER's job (daisy_worker._export drops
    # the password column for json and allowlists columns for csv/txt). The
    # route is a thin pass-through, so it must serve the worker's already-safe
    # text verbatim. This pins the contract: given stripped worker output, the
    # HTTP body carries no password. (If the worker ever regresses, that's a
    # worker-side test's job — the route can't re-strip a json blob it doesn't
    # parse without owning a responsibility that lives one layer down.)
    safe_json = '[{"email": "a@x.net", "first_name": "Ada"}]'
    _patch_bridge(monkeypatch, _FakeBridge([], export_text=safe_json))
    r = await client.get("/api/daisy/export/json")
    assert r.status_code == 200
    assert "password" not in r.text and r.text == safe_json


async def test_export_bad_format_400(client, monkeypatch):
    _patch_bridge(monkeypatch, _FakeBridge([]))
    r = await client.get("/api/daisy/export/xml")
    assert r.status_code == 400


async def test_addresses_route(client, monkeypatch):
    # /addresses must be matched as a literal, NOT captured as /{customer_id}.
    b = _patch_bridge(monkeypatch, _FakeBridge([]))
    r = await client.get("/api/daisy/addresses")
    assert r.status_code == 200
    assert r.json()["addresses"][0]["full_address"] == "1 Main St, Reno, NV"
    # it hit list_addresses, not get_customer("addresses")
    assert any(c[0] == "list_addresses" for c in b.calls)
    assert not any(c[0] == "get_customer" for c in b.calls)


async def test_add_address_route(client, monkeypatch):
    b = _patch_bridge(monkeypatch, _FakeBridge([]))
    r = await client.post("/api/daisy/addresses",
                          json={"full_address": "2 Oak Ave, Reno, NV",
                                "name": "Work"})
    assert r.status_code == 200
    fulls = [a["full_address"] for a in r.json()["addresses"]]
    assert "2 Oak Ave, Reno, NV" in fulls
    # the body was normalized to the full {full_address,name,city,state} shape
    assert b.calls[-1] == ("add_address",
                           {"full_address": "2 Oak Ave, Reno, NV",
                            "name": "Work", "city": "", "state": ""})


async def test_add_address_rejects_unknown_field(client, monkeypatch):
    _patch_bridge(monkeypatch, _FakeBridge([]))
    r = await client.post("/api/daisy/addresses",
                          json={"full_address": "x", "bogus": "y"})
    assert r.status_code == 422            # extra=forbid


async def test_add_address_requires_full_address(client, monkeypatch):
    _patch_bridge(monkeypatch, _FakeBridge([]))
    r = await client.post("/api/daisy/addresses", json={"name": "no addr"})
    assert r.status_code == 422            # full_address is required


async def test_add_address_blank_full_address_is_422(client, monkeypatch):
    # a blank/whitespace full_address is rejected at the EDGE (422) — it never
    # round-trips to the worker. (The worker still validates as a backstop.)
    _patch_bridge(monkeypatch, _FakeBridge([]))
    for blank in ("", "   "):
        r = await client.post("/api/daisy/addresses",
                              json={"full_address": blank})
        assert r.status_code == 422


async def test_add_address_strips_whitespace(client, monkeypatch):
    # the model strips every field, so the worker receives clean values.
    b = _patch_bridge(monkeypatch, _FakeBridge([]))
    r = await client.post("/api/daisy/addresses",
                          json={"full_address": "  2 Oak Ave  ", "name": " Home "})
    assert r.status_code == 200
    assert b.calls[-1] == ("add_address",
                           {"full_address": "2 Oak Ave", "name": "Home",
                            "city": "", "state": ""})


async def test_update_address_route(client, monkeypatch):
    _patch_bridge(monkeypatch, _FakeBridge([]))
    r = await client.patch("/api/daisy/addresses/0",
                         json={"full_address": "1 Main UPDATED"})
    assert r.status_code == 200
    assert r.json()["addresses"][0]["full_address"] == "1 Main UPDATED"


async def test_update_address_out_of_range_is_404(client, monkeypatch):
    _patch_bridge(monkeypatch, _FakeBridge([]))
    r = await client.patch("/api/daisy/addresses/9",
                         json={"full_address": "x"})
    assert r.status_code == 404
    assert "out of range" in r.json()["detail"]


async def test_delete_address_route(client, monkeypatch):
    b = _patch_bridge(monkeypatch, _FakeBridge([]))
    r = await client.delete("/api/daisy/addresses/0")
    assert r.status_code == 200
    assert r.json()["addresses"] == []
    assert b.calls[-1] == ("delete_address", 0)


async def test_delete_address_out_of_range_is_404(client, monkeypatch):
    _patch_bridge(monkeypatch, _FakeBridge([]))
    r = await client.delete("/api/daisy/addresses/9")
    assert r.status_code == 404


async def test_generate_address_route(client, monkeypatch):
    b = _patch_bridge(monkeypatch, _FakeBridge([]))
    r = await client.post("/api/daisy/generate-address",
                          json={"origin_address": "706 N Broad St, Edenton NC",
                                "radius_miles": 3})
    assert r.status_code == 200
    assert r.json()["address"]["full_address"].startswith("42 Near")
    assert b.calls[-1] == ("generate_address",
                           "706 N Broad St, Edenton NC", 3.0)


async def test_generate_address_none_when_nothing_found(client, monkeypatch):
    _patch_bridge(monkeypatch, _FakeBridge([]))
    r = await client.post("/api/daisy/generate-address",
                          json={"origin_address": "miss"})
    assert r.status_code == 200
    assert r.json()["address"] is None       # MapQuest found nothing nearby


async def test_generate_address_upstream_failure_is_502(client, monkeypatch):
    # a MapQuest/worker failure (DaisyError) -> 502, not an unhandled 500.
    _patch_bridge(monkeypatch, _FakeBridge([]))
    r = await client.post("/api/daisy/generate-address",
                          json={"origin_address": "boom"})
    assert r.status_code == 502
    assert "MapQuest" not in r.text          # generic detail, no internal echo


async def test_generate_address_blank_origin_is_422(client, monkeypatch):
    _patch_bridge(monkeypatch, _FakeBridge([]))
    r = await client.post("/api/daisy/generate-address",
                          json={"origin_address": "   "})
    assert r.status_code == 422


async def test_generate_address_bad_radius_is_422(client, monkeypatch):
    _patch_bridge(monkeypatch, _FakeBridge([]))
    for bad in (0, -5, 1000):
        r = await client.post("/api/daisy/generate-address",
                              json={"origin_address": "x", "radius_miles": bad})
        assert r.status_code == 422
