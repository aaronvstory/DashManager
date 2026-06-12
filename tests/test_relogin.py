"""Relogin recovery: variant-B wipe-profile path (live finding 2026-06-12)."""
from __future__ import annotations

import backend.relogin as relogin
from backend import config, db
from backend.browser import driver


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
