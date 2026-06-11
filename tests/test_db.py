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


async def test_chat_order_keyed_and_attempts():
    """Chats are order-keyed; an order can have several stacked attempts."""
    cid = await db.create_customer("2026-06-12", first_name="P")
    oid = await db.upsert_order(cid, "u1", "https://x/orders/u1",
                                store_name="DQ", order_status="cancelled")
    run_id = await db.create_run({"customer_ids": [cid]}, "scripted")

    # order_id defaults from order_ids[0] when not passed explicitly.
    c1 = await db.create_chat(run_id, cid, [oid], "opening 1")
    assert await db.count_chats_for_order(oid) == 1
    n = await db.count_chats_for_order(oid, run_id) + 1
    c2 = await db.create_chat(run_id, cid, [oid], "opening 2",
                              order_id=oid, attempt_no=n)

    chats = await db.list_chats_for_order(oid)
    assert [c["id"] for c in chats] == [c1, c2]
    assert chats[0]["order_id"] == oid
    assert chats[1]["attempt_no"] == 2
    assert await db.count_chats_for_order(oid) == 2


def test_v5_backfills_legacy_chat_order_id(tmp_path):
    """A pre-V5 (customer-keyed) chat gets order_id backfilled from its
    order_ids_json first element when the migration runs."""
    import sqlite3

    db_path = tmp_path / "legacy.db"
    # Build the schema up to V4, then insert a legacy chat the old way.
    conn = sqlite3.connect(db_path)
    conn.executescript(db.SCHEMA_V1)
    for stmt in (db.SCHEMA_V2, db.SCHEMA_V3, db.SCHEMA_V4):
        conn.executescript(stmt)
    conn.execute("PRAGMA user_version = 4")
    conn.execute("INSERT INTO customers (bucket_date) VALUES ('2026-06-12')")
    conn.execute(
        "INSERT INTO runs (scope_json, chat_strategy) VALUES ('{}','scripted')")
    conn.execute(
        """INSERT INTO orders (customer_id, order_uuid, receipt_url)
           VALUES (1, 'u1', 'https://x/orders/u1')""")
    conn.execute(
        """INSERT INTO chats (run_id, customer_id, order_ids_json,
                              opening_message) VALUES (1, 1, '[1, 2]', 'hi')""")
    conn.commit()
    conn.close()

    # Now run init_db — it should apply V5 and backfill order_id = 1.
    db.init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT order_id, attempt_no FROM chats").fetchone()
    conn.close()
    assert row["order_id"] == 1   # first element of [1, 2]
    assert row["attempt_no"] == 1


async def test_claims_roundtrip():
    cid = await db.create_customer("2026-06-12", first_name="P")
    oid = await db.upsert_order(cid, "u1", "https://x/orders/u1",
                                store_name="DQ", order_status="cancelled")
    run_id = await db.create_run({"customer_ids": [cid]}, "scripted")

    claim_id = await db.create_claim(
        run_id, oid, cid, amount=112.24, to_original_payment=True,
        confirmed=True, outcome="success")
    assert claim_id

    claims = await db.list_claims(run_id)
    assert len(claims) == 1
    assert claims[0]["amount"] == 112.24
    assert claims[0]["to_original_payment"] == 1
    assert claims[0]["confirmed"] == 1
    assert claims[0]["outcome"] == "success"

    by_order = await db.list_claims_for_order(oid)
    assert [c["id"] for c in by_order] == [claim_id]

    # A failed claim still records an audit row (error preserved).
    await db.create_claim(run_id, oid, cid, amount=None,
                          to_original_payment=False, confirmed=False,
                          outcome="error", error="radio not found")
    assert len(await db.list_claims_for_order(oid)) == 2


async def test_clear_in_progress_orders():
    """Re-scrape replaces in-progress rows, never accumulates phantoms."""
    cid = await db.create_customer("2026-06-12", first_name="P")
    # two in-progress + one completed
    await db.upsert_order(cid, "inprogress:DQ:0", "", store_name="DQ",
                          order_status="in_progress")
    await db.upsert_order(cid, "inprogress:DQ:1", "", store_name="DQ",
                          order_status="in_progress")
    await db.upsert_order(cid, "real-uuid", "https://x/orders/real-uuid",
                          store_name="DQ", order_status="completed")
    await db.clear_in_progress_orders(cid)
    rows = await db.list_orders(cid)
    assert len(rows) == 1
    assert rows[0]["order_status"] == "completed"
