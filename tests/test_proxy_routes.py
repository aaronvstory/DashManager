"""/api/proxies routes — stubbed proxy_pool, no live network.

Focus: the creds-safe error contract. ``working-proxies.txt`` holds the proxy
URL with embedded credentials; an unexpected raise from ``local_ip`` /
``check_proxy`` / ``check_all`` must NEVER surface that string to the client.
Both /test and /test/{id} log server-side and return a generic 500 body.
"""
from __future__ import annotations

import httpx
import pytest

from backend.browser import proxy_pool

# The secret that must never escape in an error response. PASSWORD is asserted
# separately so a PARTIAL leak (just the password, without the rest of the URL)
# is still caught — checking only the full string would miss that.
PASSWORD = "p@ssw0rd"
SECRET = f"user:{PASSWORD}@gw.example.com:8080"


@pytest.fixture
async def client():
    from backend.main import create_app

    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://test") as c:
        yield c


def _one_proxy(monkeypatch):
    """Make load/dedup/proxy_id resolve to a single known proxy id."""
    px = {"scheme": "http", "host": "gw.example.com", "port": 8080,
          "username": "user", "password": "p@ssw0rd"}
    monkeypatch.setattr(proxy_pool, "load_proxies", lambda *a, **k: [px])
    monkeypatch.setattr(proxy_pool, "dedup_proxies", lambda ps: ps)
    monkeypatch.setattr(proxy_pool, "proxy_id", lambda p: "the-id")
    return px


async def test_test_one_404_when_unknown(client, monkeypatch):
    _one_proxy(monkeypatch)
    r = await client.post("/api/proxies/test/does-not-exist")
    assert r.status_code == 404


async def test_test_one_returns_result_on_success(client, monkeypatch):
    _one_proxy(monkeypatch)
    monkeypatch.setattr(proxy_pool, "local_ip", lambda: "1.2.3.4")
    # keyword-only local_ip mirrors the real check_proxy(proxy, *, ..., local_ip)
    # so the stub enforces the same contract the route relies on.
    monkeypatch.setattr(
        proxy_pool, "check_proxy",
        lambda target, *, local_ip=None: {"ok": True, "ip": "9.9.9.9"})
    r = await client.post("/api/proxies/test/the-id")
    assert r.status_code == 200
    body = r.json()
    assert body["local_ip"] == "1.2.3.4" and body["ok"] is True


async def test_test_one_500_never_leaks_creds(client, monkeypatch):
    # check_proxy raising with the proxy URL (creds) in its message must NOT
    # reach the client — generic 500 body only.
    _one_proxy(monkeypatch)
    monkeypatch.setattr(proxy_pool, "local_ip", lambda: "1.2.3.4")

    def boom(target, *, local_ip=None):
        raise RuntimeError(f"connect failed for {SECRET}")

    monkeypatch.setattr(proxy_pool, "check_proxy", boom)
    r = await client.post("/api/proxies/test/the-id")
    assert r.status_code == 500
    assert r.json()["detail"] == "proxy test failed"
    assert SECRET not in r.text          # the connection string never escapes
    assert PASSWORD not in r.text        # nor the password alone (partial leak)


async def test_test_all_500_never_leaks_creds(client, monkeypatch):
    # sibling contract for the test-all route.
    def boom():
        raise RuntimeError(f"connect failed for {SECRET}")

    monkeypatch.setattr(proxy_pool, "check_all", boom)
    r = await client.post("/api/proxies/test")
    assert r.status_code == 500
    assert r.json()["detail"] == "proxy test failed"
    assert SECRET not in r.text
    assert PASSWORD not in r.text        # nor the password alone (partial leak)
