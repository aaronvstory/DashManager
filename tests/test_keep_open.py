"""Keep-open manager + routes (pure, no real browser).

`open_customer_profile` and `profile_dir` are monkeypatched so nothing launches
Chromium. The point is the lock/skip/reconcile logic, not Playwright itself.
"""
import httpx
import pytest

from backend import db, profiles_live
from backend.browser import driver
from backend.keep_open_manager import KeepOpenManager
from backend.main import create_app


class _FakeCtx:
    """Stands in for a Playwright BrowserContext."""

    def __init__(self) -> None:
        self.pages: list = []
        self.closed = False

    async def new_page(self):
        raise AssertionError("landing_url not used in these tests")

    async def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    # Redirect the live-state file and give every profile_dir a real (existing)
    # temp path so the "no profile dir yet" skip doesn't fire spuriously.
    monkeypatch.setattr(profiles_live, "STATE_PATH", tmp_path / "open.json")

    def fake_profile_dir(cid: int):
        d = tmp_path / "profiles" / str(cid)
        d.mkdir(parents=True, exist_ok=True)
        return d

    monkeypatch.setattr("backend.keep_open_manager.profile_dir", fake_profile_dir)

    # Fresh per-customer locks each test so state doesn't leak between cases.
    monkeypatch.setattr(driver, "_profile_locks", {})

    opened_ctxs: list[_FakeCtx] = []

    async def fake_open(pw, cid, headless, *, viewport=(1200, 720), **kw):
        ctx = _FakeCtx()
        opened_ctxs.append(ctx)
        return ctx

    monkeypatch.setattr("backend.keep_open_manager.open_customer_profile",
                        fake_open)
    # The manager starts Playwright lazily; stub it so no real browser engine
    # is launched.
    async def fake_ensure_pw(self):
        return object()
    monkeypatch.setattr(KeepOpenManager, "_ensure_pw", fake_ensure_pw)

    return opened_ctxs


async def test_open_then_status_lists_open_ids():
    m = KeepOpenManager()
    res = await m.open([1, 2])
    assert res == {"opened": [1, 2], "skipped": []}
    assert m.status()["open_ids"] == [1, 2]
    # Durable state recorded too.
    assert profiles_live.read_open_ids() == [1, 2]


async def test_open_holds_profile_lock():
    m = KeepOpenManager()
    await m.open([7])
    assert driver.profile_lock(7).locked() is True
    await m.close([7])
    assert driver.profile_lock(7).locked() is False


async def test_skip_already_open():
    m = KeepOpenManager()
    await m.open([1])
    res = await m.open([1, 2])  # 1 already ours
    assert res["opened"] == [2]
    assert 1 in res["skipped"]


async def test_skip_locked_id_no_double_open(_isolate):
    """A profile_lock held by a 'run' must NOT be double-opened."""
    m = KeepOpenManager()
    # Simulate a run holding the lock for customer 5.
    held = driver.profile_lock(5)
    await held.acquire()
    try:
        res = await m.open([5, 6])
        assert res["opened"] == [6]
        assert 5 in res["skipped"]
        assert m.status()["open_ids"] == [6]
        # No context was created for the locked id (1 ctx total, for id 6).
        assert len(_isolate) == 1
    finally:
        held.release()


async def test_close_releases_and_clears_state():
    m = KeepOpenManager()
    await m.open([1, 2, 3])
    closed = await m.close([2])
    assert closed == [2]
    assert m.status()["open_ids"] == [1, 3]
    assert profiles_live.read_open_ids() == [1, 3]
    # The closed id's lock is free again.
    assert driver.profile_lock(2).locked() is False


async def test_close_all_closes_everything(_isolate):
    m = KeepOpenManager()
    await m.open([1, 2])
    closed = await m.close_all()
    assert sorted(closed) == [1, 2]
    assert m.status()["open_ids"] == []
    assert all(c.closed for c in _isolate)


async def test_failed_launch_releases_lock(monkeypatch):
    m = KeepOpenManager()

    async def boom(pw, cid, headless, *, viewport=(1200, 720), **kw):
        raise RuntimeError("stale OS lock")

    monkeypatch.setattr("backend.keep_open_manager.open_customer_profile", boom)
    res = await m.open([9])
    assert res["opened"] == [] and 9 in res["skipped"]
    # A failed launch must not strand the asyncio lock.
    assert driver.profile_lock(9).locked() is False


# ── routes ──────────────────────────────────────────────────────────────────


@pytest.fixture
async def client():
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://test") as c:
        yield c


async def test_route_open_requires_ids_or_bucket(client):
    r = await client.post("/api/keep-open", json={})
    assert r.status_code == 400


async def test_route_open_by_ids(client):
    r = await client.post("/api/keep-open", json={"ids": [1, 2]})
    assert r.status_code == 200
    assert r.json()["opened"] == [1, 2]
    # GET reflects it.
    r = await client.get("/api/keep-open")
    assert r.json()["open_ids"] == [1, 2]
    # Close all.
    r = await client.post("/api/keep-open/close", json={})
    assert sorted(r.json()["closed"]) == [1, 2]


async def test_route_open_by_bucket_resolves_customers(client):
    a = await db.create_customer("2026-06-17", first_name="A")
    b = await db.create_customer("2026-06-17", first_name="B")
    await db.create_customer("2026-06-18", first_name="C")  # other bucket
    r = await client.post("/api/keep-open", json={"bucket_date": "2026-06-17"})
    assert r.status_code == 200
    assert sorted(r.json()["opened"]) == sorted([a, b])
    await client.post("/api/keep-open/close", json={})
