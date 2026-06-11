"""Run-route behavior with the orchestrator faked (no browser in tests)."""
import httpx
import pytest

from backend import db
from backend.main import create_app
from backend.runner import manager


@pytest.fixture
async def client():
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://test") as c:
        yield c


async def test_start_requires_scope(client):
    r = await client.post("/api/runs", json={"scope": {}})
    assert r.status_code == 400


async def test_start_rejects_bad_strategy(client):
    r = await client.post("/api/runs", json={
        "scope": {"bucket_date": "2026-06-11"}, "chat_strategy": "wat"})
    assert r.status_code == 400


async def test_start_conflict_while_running(client, monkeypatch):
    async def fake_start(scope, strategy, headless=None):
        return 1
    monkeypatch.setattr(manager, "start", fake_start)
    r = await client.post("/api/runs", json={
        "scope": {"bucket_date": "2026-06-11"}})
    assert r.status_code == 200 and r.json()["run_id"] == 1

    async def busy_start(scope, strategy, headless=None):
        raise RuntimeError("a run is already active")
    monkeypatch.setattr(manager, "start", busy_start)
    r = await client.post("/api/runs", json={
        "scope": {"bucket_date": "2026-06-11"}})
    assert r.status_code == 409


async def test_history_round_trip(client):
    run_id = await db.create_run({"bucket_date": "2026-06-11"}, "scripted")
    cid = await db.create_customer("2026-06-11", first_name="Brit")
    oid = await db.upsert_order(cid, "u-1", "https://x/orders/u-1",
                                store_name="DQ", price=112.34)
    await db.add_run_order(run_id, oid, cid, refund_status="not_refunded")
    chat_id = await db.create_chat(run_id, cid, [oid], "hello",
                                   order_id=oid, attempt_no=1)
    await db.add_chat_message(chat_id, "out", "hello")
    await db.finish_chat(chat_id, "manual_flag", True)
    await db.finish_run(run_id, "completed", {"checked": 1})

    r = await client.get(f"/api/runs/{run_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["run"]["stats"]["checked"] == 1
    assert body["orders"][0]["store_name"] == "DQ"
    assert body["chats"][0]["messages"][0]["content"] == "hello"
    assert body["chats"][0]["order_ids"] == [oid]
    # V5 per-order keying surfaces on the chat envelope.
    assert body["chats"][0]["order_id"] == oid
    assert body["chats"][0]["attempt_no"] == 1
    # The envelope carries the claims list (empty here, but present).
    assert body["claims"] == []

    r = await client.get("/api/runs")
    assert any(run["id"] == run_id for run in r.json()["runs"])


async def test_history_includes_claims_and_attempts(client):
    """A pending_claim self-claim + a 2-attempt chat surface order-keyed."""
    run_id = await db.create_run({"bucket_date": "2026-06-12"}, "scripted")
    cid = await db.create_customer("2026-06-12", first_name="Heidi")
    claimed = await db.upsert_order(cid, "u-claim", "https://x/orders/u-claim",
                                    store_name="DQ", price=107.01)
    chatted = await db.upsert_order(cid, "u-chat", "https://x/orders/u-chat",
                                    store_name="Taco Bell", price=106.71)
    # Self-claim audit row.
    await db.create_claim(run_id, claimed, cid, amount=107.01,
                          to_original_payment=True, confirmed=True,
                          outcome="success")
    # Two chat attempts on the chatted order.
    c1 = await db.create_chat(run_id, cid, [chatted], "open 1",
                              order_id=chatted, attempt_no=1)
    await db.finish_chat(c1, "failed", True)
    c2 = await db.create_chat(run_id, cid, [chatted], "open 2",
                              order_id=chatted, attempt_no=2)
    await db.finish_chat(c2, "success", True)
    await db.finish_run(run_id, "completed", {"checked": 2})

    body = (await client.get(f"/api/runs/{run_id}")).json()
    assert len(body["claims"]) == 1
    assert body["claims"][0]["order_id"] == claimed
    assert body["claims"][0]["outcome"] == "success"
    chats = sorted(body["chats"], key=lambda c: c["attempt_no"])
    assert [c["attempt_no"] for c in chats] == [1, 2]
    assert all(c["order_id"] == chatted for c in chats)
