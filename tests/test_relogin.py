"""Relogin recovery: variant-B wipe-profile path (live finding 2026-06-12)."""
from __future__ import annotations

import json

import backend.relogin as relogin
from backend import config, db
from backend.browser import driver


def test_write_storage_state_dict_uses_canonical_path(tmp_path, monkeypatch):
    # The CDP login path writes a pre-built dict; it must land on the SAME
    # canonical path as export_storage_state so both login paths agree.
    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path / "sessions")
    state = {"cookies": [{"name": "a", "value": "1"}], "origins": []}
    path = driver.write_storage_state_dict(7, state)
    assert path.endswith("7_storage.json")
    assert json.loads(open(path, encoding="utf-8").read()) == state


async def test_remove_profile_deletes_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROFILES_DIR", tmp_path / "profiles")
    d = driver.profile_dir(42)
    d.mkdir(parents=True, exist_ok=True)
    (d / "Cookies").write_text("stale")
    assert driver.profile_exists(42) is True
    driver.remove_profile(42)
    assert driver.profile_exists(42) is False
    # Idempotent: a second wipe on a missing dir must not raise.
    driver.remove_profile(42)


async def test_relogin_wipe_profile_calls_remove_first(tmp_path, monkeypatch):
    """wipe_profile=True must delete the profile dir before the login runs.

    The whole Playwright login is stubbed; we only assert the wipe happened
    (and happened BEFORE login_and_capture, which is what clears the
    variant-B stale-session gate).
    """
    monkeypatch.setattr(config, "PROFILES_DIR", tmp_path / "profiles")
    cid = await db.create_customer(
        "2026-06-12", first_name="H", email="h@x.test",
        number_token="tok", api_url="https://api.test", password="pw")

    d = driver.profile_dir(cid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "Cookies").write_text("stale flagged session")

    order: list[str] = []

    real_remove = driver.remove_profile

    def spy_remove(customer_id: int) -> None:
        order.append("wiped")
        real_remove(customer_id)

    monkeypatch.setattr(driver, "remove_profile", spy_remove)

    # Stub the browser-driving pieces so no Chromium launches.
    class FakeDaisy:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def fetch_otp(self, *a, **k): return {"code": "123456"}

    monkeypatch.setattr(relogin, "DaisyBridge", FakeDaisy)

    import contextlib

    @contextlib.asynccontextmanager
    async def fake_profile(*a, **k):
        order.append("opened")
        class FakeCtx:
            pages = []
            async def new_page(self): return object()
        yield FakeCtx()

    async def fake_login(*a, **k):
        order.append("login")
        return "logged_in"

    async def fake_export(*a, **k):
        return ""

    async def fake_pw():  # async_playwright() context manager
        class FakePW:
            async def __aenter__(self): return object()
            async def __aexit__(self, *a): return False
        return FakePW()

    import backend.browser.driver as drv
    import backend.browser.login_flow as lf
    monkeypatch.setattr(drv, "customer_profile", fake_profile)
    monkeypatch.setattr(lf, "login_and_capture", fake_login)
    monkeypatch.setattr(drv, "export_storage_state", fake_export)

    # async_playwright is imported inside relogin_customer; patch the module.
    import playwright.async_api as pw

    class FakeAsyncPW:
        async def __aenter__(self): return object()
        async def __aexit__(self, *a): return False

    monkeypatch.setattr(pw, "async_playwright", lambda: FakeAsyncPW())

    result = await relogin.relogin_customer(cid, headless=True,
                                            wipe_profile=True)
    assert result["outcome"] == "logged_in"
    # The wipe must have happened, and BEFORE the profile was opened/login ran.
    assert "wiped" in order
    assert order.index("wiped") < order.index("login")


# ── _token_fields: extract the api.cc OTP-fetch creds from a customer row ──

def test_token_fields_parses_json_string_mirror_hosts():
    c = {"number_token": "tok", "api_url": "https://api.test",
         "mirror_hosts": '["h1", "h2"]'}
    token, api_url, hosts = relogin._token_fields(c)
    assert token == "tok" and api_url == "https://api.test"
    assert hosts == ["h1", "h2"]


def test_token_fields_accepts_list_shaped_mirror_hosts():
    # a row handed to us BEFORE serialization (already a list) must NOT be
    # silently dropped — the old json.loads(list) raised TypeError -> [].
    c = {"number_token": "t", "mirror_hosts": ["h1", "h2"]}
    _t, _a, hosts = relogin._token_fields(c)
    assert hosts == ["h1", "h2"]


def test_token_fields_empty_and_garbage_mirror_hosts():
    assert relogin._token_fields({"mirror_hosts": ""})[2] == []
    assert relogin._token_fields({"mirror_hosts": None})[2] == []
    assert relogin._token_fields({"mirror_hosts": "not json"})[2] == []
    assert relogin._token_fields({})[2] == []           # missing key


def test_token_fields_defaults_token_and_url_to_empty():
    token, api_url, hosts = relogin._token_fields({})
    assert token == "" and api_url == "" and hosts == []
