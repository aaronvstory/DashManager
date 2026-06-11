"""REST routes over httpx ASGI transport — no server, no browser, no network.

The login test injects a stub ``backend.browser.session`` module into
sys.modules so the route's lazy import never touches Playwright.
"""
from __future__ import annotations

import asyncio
import sys
import types

import httpx
import pytest

from backend import config
from backend.events import bus
from backend.models import IdentityProfile


@pytest.fixture
async def client():
    from backend.main import create_app

    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://test") as c:
        yield c


async def test_health(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


async def test_settings_get_put_roundtrip(client):
    r = await client.get("/api/settings")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == set(config.DEFAULT_SETTINGS)
    assert body["chat"]["agent_word"] == "AGENT"

    # PUT body is the raw JSON value; partial dicts shallow-merge on read.
    r = await client.put("/api/settings/chat", json={"max_turns": 3})
    assert r.status_code == 200
    assert r.json()["value"]["max_turns"] == 3
    assert r.json()["value"]["agent_word"] == "AGENT"

    r = await client.get("/api/settings")
    assert r.json()["chat"]["max_turns"] == 3


async def test_settings_put_unknown_key_400(client):
    r = await client.put("/api/settings/not_a_key", json=123)
    assert r.status_code == 400


async def test_customers_empty(client):
    r = await client.get("/api/customers")
    assert r.status_code == 200
    assert r.json() == {"customers": []}


async def test_login_starts_then_409_then_captures(client, monkeypatch,
                                                   tmp_path):
    from backend.routes import customers as customers_routes

    monkeypatch.setattr(customers_routes, "_login_lock", asyncio.Lock())
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    monkeypatch.setattr(config, "SESSIONS_DIR", sessions_dir)

    pending_storage = sessions_dir / "pending_storage.json"
    pending_cookies = sessions_dir / "pending_cookies.json"
    pending_storage.write_text("{}")
    pending_cookies.write_text("[]")

    release = asyncio.Event()

    async def fake_login_and_capture(emit=None):
        if emit:
            emit("log", {"msg": "stub login"})
        await release.wait()
        profile = IdentityProfile(first_name="Ada", last_name="Lovelace",
                                  email="ada@example.com", phone="555-0100")
        return str(pending_storage), str(pending_cookies), profile

    stub = types.ModuleType("backend.browser.session")
    stub.login_and_capture = fake_login_and_capture
    monkeypatch.setitem(sys.modules, "backend.browser.session", stub)

    events_q = bus.subscribe()
    try:
        r = await client.post("/api/customers/login",
                              json={"bucket_date": "2026-06-01"})
        assert r.status_code == 200
        assert r.json() == {"started": True}

        # Second login while the first is pending must 409.
        r2 = await client.post("/api/customers/login", json={})
        assert r2.status_code == 409

        release.set()
        await asyncio.wait_for(customers_routes._login_task, timeout=5)

        seen = []
        while not events_q.empty():
            seen.append(events_q.get_nowait()["type"])
        assert "login_waiting" in seen
        assert "login_captured" in seen
        assert "login_failed" not in seen
    finally:
        bus.unsubscribe(events_q)

    r = await client.get("/api/customers")
    rows = r.json()["customers"]
    assert len(rows) == 1
    c = rows[0]
    cid = c["id"]
    assert c["first_name"] == "Ada"
    assert c["last_name"] == "Lovelace"
    assert c["email"] == "ada@example.com"
    assert c["bucket_date"] == "2026-06-01"
    assert c["storage_state_path"] == str(sessions_dir / f"{cid}_storage.json")
    assert c["cookies_path"] == str(sessions_dir / f"{cid}_cookies.json")
    assert (sessions_dir / f"{cid}_storage.json").exists()
    assert (sessions_dir / f"{cid}_cookies.json").exists()
    assert not pending_storage.exists()  # renamed, not copied

    # Lock released -> a new login can start again later.
    assert not customers_routes._login_lock.locked()


async def test_patch_and_delete_customer(client, monkeypatch, tmp_path):
    from backend import db

    cid = await db.create_customer("2026-06-01", first_name="Ada")
    storage = tmp_path / f"{cid}_storage.json"
    storage.write_text("{}")
    await db.update_customer(cid, storage_state_path=str(storage))

    r = await client.patch(f"/api/customers/{cid}", json={"notes": "vip"})
    assert r.status_code == 200
    assert r.json()["notes"] == "vip"
    assert r.json()["first_name"] == "Ada"

    r = await client.patch("/api/customers/99999", json={"notes": "x"})
    assert r.status_code == 404

    r = await client.delete(f"/api/customers/{cid}")
    assert r.status_code == 200
    assert not storage.exists()
    assert await db.get_customer(cid) is None

    r = await client.delete(f"/api/customers/{cid}")
    assert r.status_code == 404


async def test_llm_key_probe_missing_key_is_friendly(client, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    r = await client.post("/api/settings/test-llm-key")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "key" in body["message"].lower()
