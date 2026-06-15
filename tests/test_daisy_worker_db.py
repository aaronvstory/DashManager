"""Slice 1: the daisy_worker DB functions against a stub customers.db.

These exercise the new bridge surface (get/list/update/delete/export/addresses)
WITHOUT CustomerDaisy live — a temp SQLite DB with CustomerDaisy's real schema
stands in for it. The worker module is import-side-effect-free (side-effects are
in _bootstrap, called only from main), so importing it here is safe.
"""
import json
import sqlite3

import pytest

from backend.daisy import daisy_worker as w

# CustomerDaisy's customers table schema (verified live 2026-06-16).
_SCHEMA = """
CREATE TABLE customers (
  customer_id TEXT, full_name TEXT, first_name TEXT, last_name TEXT,
  email TEXT, password TEXT, full_address TEXT, address_line1 TEXT,
  city TEXT, state TEXT, zip_code TEXT, latitude REAL, longitude REAL,
  address_source TEXT, address_validated BOOLEAN, primary_phone TEXT,
  primary_verification_id TEXT, verification_completed BOOLEAN,
  verification_code TEXT, created_at TEXT, updated_at TEXT, metadata TEXT
)
"""


@pytest.fixture
def daisy_db(tmp_path, monkeypatch):
    """A temp customers.db with two rows; point the worker at it."""
    root = tmp_path / "daisy"
    (root / "data").mkdir(parents=True)
    db_path = root / "data" / "customers.db"
    con = sqlite3.connect(str(db_path))
    con.executescript(_SCHEMA)
    meta = json.dumps({
        "apicc_number_token": "tok-123", "apicc_api_url": "https://api.cc/x",
        "apicc_mirror_hosts": ["h1", "h2"],
    })
    con.executemany(
        "INSERT INTO customers (customer_id, first_name, last_name, full_name,"
        " email, password, primary_phone, full_address, city, state, zip_code,"
        " verification_completed, created_at, metadata) VALUES"
        " (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("cd-1", "Ada", "Vance", "Ada Vance", "ada@x.net", "pw1",
             "5551110000", "1 A St, Reno, NV", "Reno", "NV", "89500", 1,
             "2026-06-16T10:00:00", meta),
            ("cd-2", "Bo", "Kim", "Bo Kim", "bo@x.net", "pw2",
             "5552220000", "2 B St, Reno, NV", "Reno", "NV", "89501", 0,
             "2026-06-15T10:00:00", "{}"),
        ])
    con.commit()
    con.close()
    monkeypatch.setattr(w, "_daisy_db_path", lambda: db_path)
    return db_path


def test_list_recent_normalizes_and_orders(daisy_db):
    rows = w._list_recent_customers(10)
    assert [r["customer_id"] for r in rows] == ["cd-1", "cd-2"]  # newest first
    ada = rows[0]
    assert ada["number_token"] == "tok-123"
    assert ada["api_url"] == "https://api.cc/x"
    assert ada["mirror_hosts"] == ["h1", "h2"]
    assert ada["verification_completed"] is True


def test_get_customer_found_and_missing(daisy_db):
    assert w._get_customer("cd-2")["first_name"] == "Bo"
    assert w._get_customer("nope") is None


def test_customer_count(daisy_db):
    assert w._customer_count() == 2


def test_update_customer_whitelisted_only(daisy_db):
    row = w._update_customer("cd-1", {"city": "Sparks",
                                      "customer_id": "HACK",   # ignored
                                      "metadata": "HACK"})      # ignored
    assert row["city"] == "Sparks"
    assert row["customer_id"] == "cd-1"        # not overwritten
    # an unknown id updates nothing
    assert w._update_customer("nope", {"city": "X"}) is None


def test_delete_customer(daisy_db):
    assert w._delete_customer("cd-2") is True
    assert w._customer_count() == 1
    assert w._delete_customer("cd-2") is False  # already gone


def test_export_json_csv_txt(daisy_db):
    j = w._export("json", 10)
    assert j["format"] == "json"
    assert json.loads(j["text"])[0]["customer_id"] == "cd-1"

    c = w._export("csv", 10)
    assert c["format"] == "csv"
    assert "customer_id" in c["text"].splitlines()[0]   # header
    assert "ada@x.net" in c["text"]

    t = w._export("txt", 10)
    assert "Ada Vance" in t["text"] and "5551110000" in t["text"]

    with pytest.raises(ValueError):
        w._export("xml", 10)


def test_list_addresses_missing_and_present(tmp_path, monkeypatch):
    monkeypatch.setattr(w, "DAISY_ROOT", tmp_path)
    assert w._list_addresses() == []          # no file -> empty
    (tmp_path / "my_addresses.json").write_text(
        json.dumps({"addresses": [
            {"name": "Home", "full_address": "1 Main St, Reno, NV"},
            "9 Oak Ave, Reno, NV",
        ]}), encoding="utf-8")
    addrs = w._list_addresses()
    assert addrs[0]["full_address"] == "1 Main St, Reno, NV"
    assert addrs[1]["full_address"] == "9 Oak Ave, Reno, NV"   # bare string


def test_db_functions_missing_db_are_safe(tmp_path, monkeypatch):
    monkeypatch.setattr(w, "_daisy_db_path", lambda: tmp_path / "nope.db")
    assert w._list_recent_customers(10) == []
    assert w._get_customer("x") is None
    assert w._customer_count() == 0
