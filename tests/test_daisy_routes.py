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
    assert r.text == "a,b\n1,2\n"


async def test_export_bad_format_400(client, monkeypatch):
    _patch_bridge(monkeypatch, _FakeBridge([]))
    r = await client.get("/api/daisy/export/xml")
    assert r.status_code == 400
