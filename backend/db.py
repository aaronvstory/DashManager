"""SQLite persistence: WAL, versioned migrations, loop-safe async helpers.

Every operation opens its own connection inside ``asyncio.to_thread`` so
nothing blocks the event loop; writes are serialized through a module-level
asyncio.Lock (SQLite allows one writer). ``init_db()`` is sync and called once
at startup.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend import config

_write_lock = asyncio.Lock()

SCHEMA_V1 = """
CREATE TABLE customers (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  first_name  TEXT NOT NULL DEFAULT '',
  last_name   TEXT NOT NULL DEFAULT '',
  email       TEXT NOT NULL DEFAULT '',
  phone       TEXT NOT NULL DEFAULT '',
  bucket_date TEXT NOT NULL,
  storage_state_path TEXT NOT NULL DEFAULT '',
  cookies_path       TEXT,
  session_status TEXT NOT NULL DEFAULT 'active',
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  notes       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX idx_customers_bucket ON customers(bucket_date);

CREATE TABLE orders (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
  order_uuid  TEXT NOT NULL,
  receipt_url TEXT NOT NULL,
  store_name  TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  items_count INTEGER,
  price       REAL,
  order_status  TEXT NOT NULL DEFAULT 'active',
  refund_status TEXT NOT NULL DEFAULT 'unchecked',
  total_amount  REAL,
  refund_amount REAL,
  last_checked_at TEXT,
  UNIQUE(customer_id, order_uuid)
);

CREATE TABLE runs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at  TEXT NOT NULL DEFAULT (datetime('now')),
  finished_at TEXT,
  scope_json  TEXT NOT NULL DEFAULT '{}',
  chat_strategy TEXT NOT NULL DEFAULT 'scripted',
  status      TEXT NOT NULL DEFAULT 'running',
  stats_json  TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE run_orders (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id      INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  order_id    INTEGER NOT NULL REFERENCES orders(id),
  customer_id INTEGER NOT NULL REFERENCES customers(id),
  refund_status TEXT,
  error       TEXT,
  screenshot_path TEXT,
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE chats (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id      INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  customer_id INTEGER NOT NULL REFERENCES customers(id),
  order_ids_json TEXT NOT NULL DEFAULT '[]',
  opening_message TEXT NOT NULL DEFAULT '',
  outcome     TEXT,
  agent_reached INTEGER NOT NULL DEFAULT 0,
  started_at  TEXT NOT NULL DEFAULT (datetime('now')),
  finished_at TEXT
);

CREATE TABLE chat_messages (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id   INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
  ts        TEXT NOT NULL DEFAULT (datetime('now')),
  direction TEXT NOT NULL,
  content   TEXT NOT NULL
);

CREATE TABLE settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

# V2: credentials needed to re-login an account and fetch fresh OTPs later.
# A rented api.cc number stays reachable for ~60-80 days, so storing its token
# lets DashManager grab a verification code on demand (manual phone login) or
# drive a fresh headed login when cookies expire.
SCHEMA_V2 = """
ALTER TABLE customers ADD COLUMN password TEXT NOT NULL DEFAULT '';
ALTER TABLE customers ADD COLUMN number_token TEXT NOT NULL DEFAULT '';
ALTER TABLE customers ADD COLUMN api_url TEXT NOT NULL DEFAULT '';
ALTER TABLE customers ADD COLUMN mirror_hosts TEXT NOT NULL DEFAULT '[]';
"""

# V3: per-order lifecycle detail — the live status text ("Heading to you")
# and the assigned dasher name, shown on the live customer view.
SCHEMA_V3 = """
ALTER TABLE orders ADD COLUMN status_text TEXT NOT NULL DEFAULT '';
ALTER TABLE orders ADD COLUMN dasher_name TEXT NOT NULL DEFAULT '';
"""

# V4: the lifecycle contract is in_progress|completed|cancelled now; the V1
# default 'active' is legacy. Backfill so old rows become refund-checkable
# (and aren't stranded by clear_in_progress_orders, which only drops
# 'in_progress').
SCHEMA_V4 = """
UPDATE orders SET order_status='completed' WHERE order_status='active';
"""

# V5: chats become ORDER-keyed (the spec's per-order transcript audit). A chat
# now belongs to ONE order; an order can have MANY chats (attempt 1, attempt 2,
# a reopened session after a timeout). `order_ids_json` is kept for backward
# compatibility (old customer-keyed chats bundled several orders) and the new
# `order_id` FK is backfilled from its first element. `attempt_no` numbers the
# retries (1..3). A separate `claims` table records the non-chat resolution
# path — self-claiming a pending_claim refund to the original payment method —
# so the per-order audit covers claims as well as chats.
SCHEMA_V5 = """
ALTER TABLE chats ADD COLUMN order_id INTEGER REFERENCES orders(id);
ALTER TABLE chats ADD COLUMN attempt_no INTEGER NOT NULL DEFAULT 1;
UPDATE chats SET order_id = (
  SELECT CAST(json_extract(order_ids_json, '$[0]') AS INTEGER)
) WHERE order_id IS NULL AND json_valid(order_ids_json)
       AND json_array_length(order_ids_json) > 0;
CREATE INDEX idx_chats_order ON chats(order_id);

CREATE TABLE claims (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id      INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  order_id    INTEGER NOT NULL REFERENCES orders(id),
  customer_id INTEGER NOT NULL REFERENCES customers(id),
  amount      REAL,
  to_original_payment INTEGER NOT NULL DEFAULT 0,
  confirmed   INTEGER NOT NULL DEFAULT 0,
  outcome     TEXT NOT NULL DEFAULT '',
  error       TEXT,
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_claims_order ON claims(order_id);
"""

_MIGRATIONS: list[str] = [SCHEMA_V1, SCHEMA_V2, SCHEMA_V3, SCHEMA_V4, SCHEMA_V5]


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _already_applied(exc: sqlite3.OperationalError) -> bool:
    """Is this OperationalError just 'this migration already ran'?

    executescript() issues an implicit COMMIT before running, so the
    user_version bump that follows it lands in a SEPARATE transaction. If the
    process dies in that gap, a migration's DDL is on disk but user_version is
    still behind — the next init_db re-runs the migration and SQLite raises
    "duplicate column name" / "table ... already exists". We treat those as a
    completed migration and let init_db advance the version, instead of
    crashing the app on every subsequent boot.
    """
    msg = str(exc).lower()
    return ("duplicate column name" in msg
            or "already exists" in msg)


def init_db(db_path: Path | None = None) -> None:
    """Create/upgrade the schema. Safe to call repeatedly (and crash-safe:
    a half-applied migration left by a power loss is recovered, not re-crashed).
    """
    with _connect(db_path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        for i in range(version, len(_MIGRATIONS)):
            try:
                conn.executescript(_MIGRATIONS[i])
            except sqlite3.OperationalError as exc:
                if not _already_applied(exc):
                    raise
                # DDL already on disk from a previous interrupted run — just
                # advance the version below.
            conn.execute(f"PRAGMA user_version = {i + 1}")
            conn.commit()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── Low-level async helpers ──────────────────────────────────────────────────

async def query(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    def _run() -> list[dict[str, Any]]:
        with _connect() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
    return await asyncio.to_thread(_run)


async def query_one(sql: str, params: tuple = ()) -> dict[str, Any] | None:
    rows = await query(sql, params)
    return rows[0] if rows else None


async def execute(sql: str, params: tuple = ()) -> int:
    """Run a write statement; returns lastrowid."""
    def _run() -> int:
        with _connect() as conn:
            cur = conn.execute(sql, params)
            conn.commit()
            return cur.lastrowid or 0
    async with _write_lock:
        return await asyncio.to_thread(_run)


# ── Customers ────────────────────────────────────────────────────────────────

async def create_customer(bucket_date: str, **fields: Any) -> int:
    bad = set(fields) - _CUSTOMER_FIELDS
    if bad:
        raise ValueError(f"unknown customer fields: {bad}")
    cols = ["bucket_date"] + list(fields.keys())
    vals = [bucket_date] + list(fields.values())
    sql = (f"INSERT INTO customers ({', '.join(cols)}) "
           f"VALUES ({', '.join('?' * len(cols))})")
    return await execute(sql, tuple(vals))


async def get_customer(customer_id: int) -> dict[str, Any] | None:
    return await query_one("SELECT * FROM customers WHERE id=?", (customer_id,))


async def list_customers() -> list[dict[str, Any]]:
    return await query(
        "SELECT * FROM customers ORDER BY bucket_date DESC, created_at DESC")


_CUSTOMER_FIELDS = {"first_name", "last_name", "email", "phone", "bucket_date",
                    "storage_state_path", "cookies_path", "session_status",
                    "notes", "password", "number_token", "api_url",
                    "mirror_hosts"}


async def update_customer(customer_id: int, **fields: Any) -> None:
    bad = set(fields) - _CUSTOMER_FIELDS
    if bad:
        raise ValueError(f"unknown customer fields: {bad}")
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    await execute(f"UPDATE customers SET {sets} WHERE id=?",
                  (*fields.values(), customer_id))


async def delete_customer(customer_id: int) -> None:
    await execute("DELETE FROM customers WHERE id=?", (customer_id,))


# ── Orders ───────────────────────────────────────────────────────────────────

async def upsert_order(customer_id: int, order_uuid: str, receipt_url: str,
                       store_name: str = "", description: str = "",
                       items_count: int | None = None,
                       price: float | None = None,
                       order_status: str = "completed",
                       status_text: str = "", dasher_name: str = "") -> int:
    """Insert or refresh a scraped order; returns the order id.

    Refund fields are NOT touched on re-scrape (a later check owns those).
    """
    await execute(
        """INSERT INTO orders (customer_id, order_uuid, receipt_url, store_name,
                               description, items_count, price, order_status,
                               status_text, dasher_name)
           VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(customer_id, order_uuid) DO UPDATE SET
             receipt_url=excluded.receipt_url, store_name=excluded.store_name,
             description=excluded.description, items_count=excluded.items_count,
             price=excluded.price, order_status=excluded.order_status,
             status_text=excluded.status_text,
             dasher_name=excluded.dasher_name""",
        (customer_id, order_uuid, receipt_url, store_name, description,
         items_count, price, order_status, status_text, dasher_name))
    row = await query_one(
        "SELECT id FROM orders WHERE customer_id=? AND order_uuid=?",
        (customer_id, order_uuid))
    if row is None:  # not assert — must survive `python -O`
        raise RuntimeError(
            f"order vanished after upsert (customer={customer_id}, "
            f"uuid={order_uuid})")
    return row["id"]


async def update_order_refund(order_id: int, refund_status: str,
                              total_amount: float | None,
                              refund_amount: float | None) -> None:
    await execute(
        """UPDATE orders SET refund_status=?, total_amount=?, refund_amount=?,
                             last_checked_at=? WHERE id=?""",
        (refund_status, total_amount, refund_amount, now_iso(), order_id))


async def clear_in_progress_orders(customer_id: int) -> None:
    """Drop a customer's in-progress orders before re-scraping.

    In-progress orders have no stable identity (synthetic 'inprogress:*' uuid
    keyed by list position), so a fresh scrape must replace, not accumulate —
    otherwise a completed/vanished live order leaves a phantom row forever.
    """
    await execute(
        "DELETE FROM orders WHERE customer_id=? AND order_status='in_progress'",
        (customer_id,))


async def list_orders(customer_id: int | None = None) -> list[dict[str, Any]]:
    if customer_id is None:
        return await query("SELECT * FROM orders ORDER BY id")
    return await query("SELECT * FROM orders WHERE customer_id=? ORDER BY id",
                       (customer_id,))


# ── Runs ─────────────────────────────────────────────────────────────────────

async def create_run(scope: dict[str, Any], chat_strategy: str) -> int:
    return await execute(
        "INSERT INTO runs (scope_json, chat_strategy) VALUES (?,?)",
        (json.dumps(scope), chat_strategy))


async def finish_run(run_id: int, status: str, stats: dict[str, Any]) -> None:
    await execute(
        "UPDATE runs SET finished_at=?, status=?, stats_json=? WHERE id=?",
        (now_iso(), status, json.dumps(stats), run_id))


async def list_runs() -> list[dict[str, Any]]:
    return await query("SELECT * FROM runs ORDER BY id DESC")


async def add_run_order(run_id: int, order_id: int, customer_id: int,
                        refund_status: str | None = None,
                        error: str | None = None,
                        screenshot_path: str | None = None) -> int:
    return await execute(
        """INSERT INTO run_orders (run_id, order_id, customer_id, refund_status,
                                   error, screenshot_path) VALUES (?,?,?,?,?,?)""",
        (run_id, order_id, customer_id, refund_status, error, screenshot_path))


async def list_run_orders(run_id: int) -> list[dict[str, Any]]:
    return await query(
        """SELECT ro.*, o.store_name, o.description, o.price, o.order_uuid,
                  o.order_status
           FROM run_orders ro JOIN orders o ON o.id = ro.order_id
           WHERE ro.run_id=? ORDER BY ro.id""", (run_id,))


# ── Chats ────────────────────────────────────────────────────────────────────

async def create_chat(run_id: int, customer_id: int, order_ids: list[int],
                      opening_message: str, order_id: int | None = None,
                      attempt_no: int = 1) -> int:
    """Create a chat row. Chats are order-keyed (``order_id``); ``order_ids``
    is kept for backward-compat and defaults its first element as order_id.

    ``attempt_no`` numbers retries on the SAME order (1..3) so the per-order
    transcript view can stack and label reopened sessions.
    """
    if order_id is None and order_ids:
        order_id = order_ids[0]
    return await execute(
        """INSERT INTO chats (run_id, customer_id, order_ids_json,
                              opening_message, order_id, attempt_no)
           VALUES (?,?,?,?,?,?)""",
        (run_id, customer_id, json.dumps(order_ids), opening_message,
         order_id, attempt_no))


async def finish_chat(chat_id: int, outcome: str, agent_reached: bool) -> None:
    await execute(
        "UPDATE chats SET outcome=?, agent_reached=?, finished_at=? WHERE id=?",
        (outcome, int(agent_reached), now_iso(), chat_id))


async def add_chat_message(chat_id: int, direction: str, content: str) -> int:
    return await execute(
        "INSERT INTO chat_messages (chat_id, direction, content) VALUES (?,?,?)",
        (chat_id, direction, content))


async def list_chats(run_id: int) -> list[dict[str, Any]]:
    return await query("SELECT * FROM chats WHERE run_id=? ORDER BY id",
                       (run_id,))


async def list_chat_messages(chat_id: int) -> list[dict[str, Any]]:
    return await query(
        "SELECT * FROM chat_messages WHERE chat_id=? ORDER BY id", (chat_id,))


async def list_chats_for_order(order_id: int) -> list[dict[str, Any]]:
    """Every chat for one order, oldest first — the per-order audit trail.

    Spans runs: an order can be chatted across multiple runs, and each chat
    stacks chronologically (attempt 1, attempt 2, reopened sessions).
    """
    return await query(
        "SELECT * FROM chats WHERE order_id=? ORDER BY id", (order_id,))


async def count_chats_for_order(order_id: int, run_id: int | None = None) -> int:
    """How many chats already exist for an order (optionally within one run).

    Drives the retry attempt number so reopened sessions are numbered 1..N.
    """
    if run_id is None:
        row = await query_one(
            "SELECT COUNT(*) AS n FROM chats WHERE order_id=?", (order_id,))
    else:
        row = await query_one(
            "SELECT COUNT(*) AS n FROM chats WHERE order_id=? AND run_id=?",
            (order_id, run_id))
    return int(row["n"]) if row else 0


# ── Claims (pending_claim self-claim audit) ─────────────────────────────────

async def create_claim(run_id: int, order_id: int, customer_id: int,
                       amount: float | None, to_original_payment: bool,
                       confirmed: bool, outcome: str,
                       error: str | None = None) -> int:
    """Record one self-claim attempt (refund claimed to original payment).

    ``outcome`` is 'success' | 'failed' | 'wrong_method' | 'error'. The audit
    row exists even on failure so the per-order view shows what was attempted.
    """
    return await execute(
        """INSERT INTO claims (run_id, order_id, customer_id, amount,
                               to_original_payment, confirmed, outcome, error)
           VALUES (?,?,?,?,?,?,?,?)""",
        (run_id, order_id, customer_id, amount, int(to_original_payment),
         int(confirmed), outcome, error))


async def list_claims(run_id: int) -> list[dict[str, Any]]:
    return await query("SELECT * FROM claims WHERE run_id=? ORDER BY id",
                       (run_id,))


async def list_claims_for_order(order_id: int) -> list[dict[str, Any]]:
    return await query("SELECT * FROM claims WHERE order_id=? ORDER BY id",
                       (order_id,))


# ── Settings ─────────────────────────────────────────────────────────────────

async def get_setting(key: str) -> Any:
    """Stored value merged over DEFAULT_SETTINGS (shallow merge for dicts)."""
    default = config.DEFAULT_SETTINGS.get(key)
    row = await query_one("SELECT value FROM settings WHERE key=?", (key,))
    if row is None:
        return default
    stored = json.loads(row["value"])
    if isinstance(default, dict) and isinstance(stored, dict):
        return {**default, **stored}
    return stored


async def get_all_settings() -> dict[str, Any]:
    return {key: await get_setting(key) for key in config.DEFAULT_SETTINGS}


async def set_setting(key: str, value: Any) -> None:
    if key not in config.DEFAULT_SETTINGS:
        raise ValueError(f"unknown settings key: {key}")
    await execute(
        """INSERT INTO settings (key, value) VALUES (?,?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
        (key, json.dumps(value)))
