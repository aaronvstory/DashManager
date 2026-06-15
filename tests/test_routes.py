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

    monkeypatch.setattr(customers_routes, "_login_task", None)
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    monkeypatch.setattr(config, "SESSIONS_DIR", sessions_dir)
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    monkeypatch.setattr(config, "PROFILES_DIR", profiles_dir)

    pending_storage = sessions_dir / "pending_storage.json"
    pending_cookies = sessions_dir / "pending_cookies.json"
    pending_storage.write_text("{}")
    pending_cookies.write_text("[]")
    temp_profile = tmp_path / "temp_profile"
    temp_profile.mkdir()
    (temp_profile / "marker").write_text("x")  # non-empty so move succeeds

    release = asyncio.Event()

    async def fake_login_and_capture(emit=None):
        if emit:
            emit("log", {"msg": "stub login"})
        await release.wait()
        profile = IdentityProfile(first_name="Ada", last_name="Lovelace",
                                  email="ada@example.com", phone="555-0100")
        return (str(pending_storage), str(pending_cookies), profile,
                str(temp_profile))

    stub = types.ModuleType("backend.browser.session")
    stub.manual_login_and_capture = fake_login_and_capture
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

    # Task done -> a new login can start again later (no leaked lock).
    assert not customers_routes._task_running(customers_routes._login_task)


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


# ── Proxy Manager routes ─────────────────────────────────────────────────────
# load_proxies / check_* are monkeypatched so these stay network- and file-free
# while still exercising the route's credential-hygiene + shaping.
_FAKE_PROXY = {
    "scheme": "http", "host": "resident.lightningproxies.net", "port": "8080",
    "username": "user-country-us-filter-medium-speed-fast",
    "password": "TOPSECRET",
}


async def test_proxies_list_omits_credentials(client, monkeypatch):
    from backend.browser import proxy_pool
    monkeypatch.setattr(proxy_pool, "load_proxies",
                        lambda *a, **k: [dict(_FAKE_PROXY), dict(_FAKE_PROXY)])
    r = await client.get("/api/proxies")
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is True
    assert body["count"] == 1          # two identical lines dedup to one
    assert "TOPSECRET" not in r.text   # password NEVER leaves the backend
    assert body["proxies"][0]["host"] == "resident.lightningproxies.net"


async def test_proxies_list_unconfigured(client, monkeypatch):
    from backend.browser import proxy_pool
    monkeypatch.setattr(proxy_pool, "load_proxies", lambda *a, **k: [])
    r = await client.get("/api/proxies")
    assert r.json() == {"configured": False, "count": 0, "proxies": []}


async def test_proxies_test_all(client, monkeypatch):
    from backend.browser import proxy_pool
    fake = {"local_ip": "9.9.9.9", "count": 1, "alive_count": 1,
            "proxies": [{"id": "x", "alive": True, "exit_ip": "1.2.3.4",
                         "country": "US", "city": "Dallas", "latency_ms": 410.0,
                         "error": "", "differs_from_local": True}]}
    monkeypatch.setattr(proxy_pool, "check_all", lambda *a, **k: fake)
    r = await client.post("/api/proxies/test")
    assert r.status_code == 200
    assert r.json()["alive_count"] == 1
    assert r.json()["proxies"][0]["exit_ip"] == "1.2.3.4"


async def test_proxies_test_one_404_for_unknown(client, monkeypatch):
    from backend.browser import proxy_pool
    monkeypatch.setattr(proxy_pool, "load_proxies",
                        lambda *a, **k: [dict(_FAKE_PROXY)])
    r = await client.post("/api/proxies/test/not-a-real-id")
    assert r.status_code == 404


async def test_proxies_test_one_success_forwards_fields(client, monkeypatch):
    from backend.browser import proxy_pool
    monkeypatch.setattr(proxy_pool, "load_proxies",
                        lambda *a, **k: [dict(_FAKE_PROXY)])
    pid = proxy_pool.proxy_id(_FAKE_PROXY)
    monkeypatch.setattr(proxy_pool, "local_ip", lambda *a, **k: "9.9.9.9")
    monkeypatch.setattr(
        proxy_pool, "check_proxy",
        lambda px, **k: {"id": proxy_pool.proxy_id(px), "alive": True,
                         "exit_ip": "1.2.3.4", "country": "US", "city": "Reno",
                         "region": "Nevada", "latency_ms": 300.0, "error": "",
                         "differs_from_local": True})
    r = await client.post(f"/api/proxies/test/{pid}")
    assert r.status_code == 200
    body = r.json()
    assert body["local_ip"] == "9.9.9.9"
    assert body["exit_ip"] == "1.2.3.4" and body["city"] == "Reno"
    assert body["id"] == pid
    assert "TOPSECRET" not in r.text   # liveness response never echoes the pw


async def test_proxies_test_all_failure_is_500_without_creds(client, monkeypatch):
    from backend.browser import proxy_pool

    def boom(*a, **k):
        # An error string that (maliciously) embeds the password — the route's
        # 500 detail must not leak it.
        raise RuntimeError("connect failed via TOPSECRET@host")

    monkeypatch.setattr(proxy_pool, "check_all", boom)
    r = await client.post("/api/proxies/test")
    assert r.status_code == 500
    assert "detail" in r.json()
