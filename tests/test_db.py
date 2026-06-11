"""DB helper round-trips against the per-test temp database (conftest)."""
from __future__ import annotations

import pytest

from backend import config, db


async def test_customer_crud_roundtrip():
    cid = await db.create_customer(
        "2026-06-11", first_name="Ada", last_name="Lovelace",
        email="ada@example.com", phone="555-0100")

    row = await db.get_customer(cid)
    assert row is not None
    assert row["first_name"] == "Ada"
    assert row["bucket_date"] == "2026-06-11"
    assert row["session_status"] == "active"

    await db.update_customer(cid, notes="vip", session_status="expired")
    row = await db.get_customer(cid)
    assert row["notes"] == "vip"
    assert row["session_status"] == "expired"

    assert [c["id"] for c in await db.list_customers()] == [cid]

    with pytest.raises(ValueError):
        await db.update_customer(cid, not_a_column="x")

    await db.delete_customer(cid)
    assert await db.get_customer(cid) is None
    assert await db.list_customers() == []


async def test_upsert_order_refresh_preserves_refund_fields():
    cid = await db.create_customer("2026-06-11")
    oid = await db.upsert_order(
        cid, "uuid-1", "https://dd.test/orders/uuid-1",
        store_name="Store A", description="2 items", items_count=2,
        price=10.0)

    await db.update_order_refund(oid, "refunded", 10.0, 10.0)

    # Re-scrape: scrape columns refresh, refund columns must survive.
    oid2 = await db.upsert_order(
        cid, "uuid-1", "https://dd.test/orders/uuid-1?v=2",
        store_name="Store B", description="3 items", items_count=3,
        price=12.5)
    assert oid2 == oid

    rows = await db.list_orders(cid)
    assert len(rows) == 1
    row = rows[0]
    assert row["store_name"] == "Store B"
    assert row["receipt_url"].endswith("?v=2")
    assert row["items_count"] == 3
    assert row["refund_status"] == "refunded"
    assert row["total_amount"] == 10.0
    assert row["refund_amount"] == 10.0
    assert row["last_checked_at"] is not None


async def test_run_run_orders_chat_messages_roundtrip():
    cid = await db.create_customer("2026-06-11", first_name="Ada")
    oid = await db.upsert_order(cid, "u1", "https://dd.test/orders/u1",
                                store_name="Store A")

    run_id = await db.create_run({"customer_ids": [cid]}, "scripted")
    runs = await db.list_runs()
    assert runs[0]["id"] == run_id
    assert runs[0]["status"] == "running"
    assert runs[0]["chat_strategy"] == "scripted"

    await db.add_run_order(run_id, oid, cid, refund_status="not_refunded",
                           screenshot_path="shot.png")
    ros = await db.list_run_orders(run_id)
    assert len(ros) == 1
    assert ros[0]["order_uuid"] == "u1"          # joined from orders
    assert ros[0]["store_name"] == "Store A"
    assert ros[0]["refund_status"] == "not_refunded"

    chat_id = await db.create_chat(run_id, cid, [oid], "opening message")
    await db.add_chat_message(chat_id, "out", "hello")
    await db.add_chat_message(chat_id, "in", "hi, agent here")
    await db.finish_chat(chat_id, "success", True)

    chats = await db.list_chats(run_id)
    assert len(chats) == 1
    assert chats[0]["outcome"] == "success"
    assert chats[0]["agent_reached"] == 1
    assert chats[0]["finished_at"] is not None

    msgs = await db.list_chat_messages(chat_id)
    assert [(m["direction"], m["content"]) for m in msgs] == [
        ("out", "hello"), ("in", "hi, agent here")]

    await db.finish_run(run_id, "completed", {"orders_checked": 1})
    runs = await db.list_runs()
    assert runs[0]["status"] == "completed"
    assert runs[0]["finished_at"] is not None


async def test_settings_default_when_unset():
    assert await db.get_setting("chat") == config.DEFAULT_SETTINGS["chat"]
    everything = await db.get_all_settings()
    assert set(everything) == set(config.DEFAULT_SETTINGS)


async def test_settings_partial_dict_shallow_merges():
    await db.set_setting("chat", {"max_turns": 99})
    merged = await db.get_setting("chat")
    assert merged["max_turns"] == 99
    # Default keys not present in the stored partial must survive the merge.
    assert merged["agent_word"] == "AGENT"
    assert merged["max_escalations"] == 6


async def test_settings_unknown_key_raises():
    with pytest.raises(ValueError):
        await db.set_setting("definitely_not_a_key", 1)
