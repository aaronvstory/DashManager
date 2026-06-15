"""CustomerDaisy worker — runs UNDER CustomerDaisy's own venv + cwd.

DashManager never imports CustomerDaisy directly (its modules assume their own
working directory for config.ini / .env.local / data/customers.db, and its deps
live in its own venv). Instead `bridge.py` launches this script with
CustomerDaisy's python, from CustomerDaisy's root, and speaks newline-delimited
JSON over stdin/stdout:

    -> {"cmd": "generate_identity", "args": {...}}
    <- {"ok": true, "result": {...}}   |   {"ok": false, "error": "..."}

One process can serve many commands (it loops on stdin), so the managers and
their caches/sessions persist across a single account-creation flow.

Commands:
  ping                                  -> {"pong": true}
  balance                               -> {"balance": float}
  locations                             -> {"locations": [{index,name,city,state,full_address}]}
  generate_identity {origin, radius}    -> identity dict (name/email/address/password)
  rent_number                           -> {phone_number, number_token, api_url, mirror_hosts, ...}
  fetch_otp {token, api_url, mirror_hosts} -> {code, sms_text, error}
  save_customer {customer}              -> {customer_id}
  list_recent_customers {limit}         -> {customers: [...]}
  list_customers {limit}                -> {customers: [...]}   (full list)
  customer_count                        -> {count: int}
  get_customer {customer_id}            -> {customer: {...}|null}
  update_customer {customer_id, fields} -> {customer, updated}
  delete_customer {customer_id}         -> {deleted: bool}
  export {format, limit}                -> {format, text}       (csv|json|txt)
  list_addresses                        -> {addresses: [...]}   (anchor pool)
  generate_address {origin, radius}     -> {address: {...}|null}

stdout is JSON-only; CustomerDaisy's Rich console noise is redirected to stderr
so it can never corrupt the protocol.
"""
import json
import os
import sys
from pathlib import Path

# CustomerDaisy assumes it runs from its own root. The launcher sets cwd, but
# enforce it too so relative paths (config.ini, .env.local, data/) resolve.
# Resolved at import (cheap); the cwd/path/stdout SIDE-EFFECTS happen in
# _bootstrap() (called from main), so importing this module for unit tests is
# side-effect-free.
DAISY_ROOT = Path(os.environ.get("DAISY_ROOT", Path.cwd()))

# stdout is the JSON channel only; _bootstrap swaps real stdout aside so
# CustomerDaisy's Rich console noise (which writes to sys.stdout) can't corrupt
# the protocol. Until then, _real_stdout is the actual stdout.
_real_stdout = sys.stdout


def _bootstrap() -> None:
    """Apply the worker's process side-effects. Call once from main()."""
    global _real_stdout
    os.chdir(DAISY_ROOT)
    sys.path.insert(0, str(DAISY_ROOT))
    _real_stdout = sys.stdout
    sys.stdout = sys.stderr


def _emit(obj: dict) -> None:
    _real_stdout.write(json.dumps(obj) + "\n")
    _real_stdout.flush()


class Managers:
    """Lazily-built CustomerDaisy managers, reused across commands."""

    def __init__(self) -> None:
        from src.config_manager import ConfigManager  # noqa: PLC0415
        self.config = ConfigManager("config.ini")
        self._apicc = None
        self._mail = None
        self._mapquest = None
        self._db = None

    @property
    def apicc(self):
        if self._apicc is None:
            from src.apicc_sms import ApiccSmsManager
            self._apicc = ApiccSmsManager(self.config.get_section("APICC"))
        return self._apicc

    @property
    def mail(self):
        if self._mail is None:
            from src.mail_tm import MailTmManager
            self._mail = MailTmManager(
                self.config.get_section("MAILTM"), config_manager=self.config)
        return self._mail

    @property
    def mapquest(self):
        if self._mapquest is None:
            from src.mapquest_address import MapQuestAddressManager
            self._mapquest = MapQuestAddressManager(
                self.config.get_section("MAPQUEST"))
        return self._mapquest

    @property
    def db(self):
        if self._db is None:
            from src.customer_db import CustomerDatabase
            self._db = CustomerDatabase(
                self.config.get_section("DATABASE"),
                mapquest_config=self.config.get_section("MAPQUEST"),
                mapquest_manager=self.mapquest)
        return self._db


def _load_locations() -> list[dict]:
    raw = json.loads(
        (DAISY_ROOT / "data" / "predefined_addresses.json").read_text("utf-8"))
    items = raw.get("addresses", raw) if isinstance(raw, dict) else raw
    out = []
    for i, x in enumerate(items):
        out.append({
            "index": i,
            "name": x.get("name", ""),
            "city": x.get("city", ""),
            "state": x.get("state", ""),
            "full_address": x.get("full_address")
                            or f"{x.get('city','')}, {x.get('state','')}".strip(", "),
        })
    return out


def _daisy_db_path() -> Path:
    return DAISY_ROOT / "data" / "customers.db"


def _connect():
    """Open CustomerDaisy's customers.db (row factory = dict-able). Raises if
    the DB doesn't exist so callers report a clear error, not an empty result."""
    import sqlite3

    p = _daisy_db_path()
    if not p.exists():
        raise FileNotFoundError(f"CustomerDaisy DB not found: {p}")
    con = sqlite3.connect(str(p))
    con.row_factory = sqlite3.Row
    return con


def _normalize_row(r: dict) -> dict:
    """Shape one customers.db row into the bridge's customer dict.

    Pulls the api.cc handle out of `metadata` (number_token/api_url/
    mirror_hosts) so the row is usable for a later OTP fetch. mirror_hosts may
    be a JSON list OR a comma string depending on which CustomerDaisy code path
    wrote it — both are normalized to a list.
    """
    try:
        meta = json.loads(r.get("metadata") or "{}")
    except (json.JSONDecodeError, TypeError):
        meta = {}
    raw_hosts = meta.get("apicc_mirror_hosts") or ""
    if isinstance(raw_hosts, list):
        hosts_list = [h for h in raw_hosts if h]
    else:
        hosts_list = [h for h in str(raw_hosts).split(",") if h]
    return {
        "customer_id": r.get("customer_id", ""),
        "first_name": r.get("first_name", ""),
        "last_name": r.get("last_name", ""),
        "full_name": r.get("full_name", ""),
        "email": r.get("email", ""),
        "password": r.get("password", ""),
        "phone": r.get("primary_phone", ""),
        "full_address": r.get("full_address", ""),
        "city": r.get("city", ""),
        "state": r.get("state", ""),
        "zip_code": r.get("zip_code", ""),
        "verification_completed": bool(r.get("verification_completed")),
        "number_token": (meta.get("apicc_number_token")
                         or r.get("primary_verification_id") or ""),
        "api_url": meta.get("apicc_api_url", ""),
        "mirror_hosts": hosts_list,
        "created_at": r.get("created_at", ""),
        "updated_at": r.get("updated_at", ""),
    }


def _list_recent_customers(limit: int) -> list[dict]:
    """Read CustomerDaisy's customers.db (identity + api.cc token per row)."""
    if not _daisy_db_path().exists():
        return []
    con = _connect()
    try:
        rows = con.execute(
            "SELECT * FROM customers ORDER BY created_at DESC LIMIT ?",
            (limit,)).fetchall()
    finally:
        con.close()
    return [_normalize_row(dict(r)) for r in rows]


def _get_customer(customer_id: str) -> dict | None:
    if not _daisy_db_path().exists():
        return None
    con = _connect()
    try:
        r = con.execute(
            "SELECT * FROM customers WHERE customer_id = ?",
            (customer_id,)).fetchone()
    finally:
        con.close()
    return _normalize_row(dict(r)) if r else None


# Columns a bridge caller may update directly (identity/address fields only —
# never customer_id/created_at/metadata, which the app owns).
_UPDATABLE = {
    "first_name", "last_name", "full_name", "email", "password",
    "full_address", "address_line1", "city", "state", "zip_code",
    "primary_phone",
}


def _update_customer(customer_id: str, fields: dict) -> dict | None:
    """Update whitelisted columns on a CustomerDaisy row; return the new row."""
    updates = {k: v for k, v in (fields or {}).items() if k in _UPDATABLE}
    if not updates:
        return _get_customer(customer_id)
    con = _connect()
    try:
        sets = ", ".join(f"{k} = ?" for k in updates)
        params = list(updates.values()) + [customer_id]
        cur = con.execute(
            f"UPDATE customers SET {sets}, updated_at = CURRENT_TIMESTAMP "
            f"WHERE customer_id = ?", params)
        con.commit()
        if cur.rowcount == 0:
            return None
    finally:
        con.close()
    return _get_customer(customer_id)


def _delete_customer(customer_id: str) -> bool:
    con = _connect()
    try:
        cur = con.execute(
            "DELETE FROM customers WHERE customer_id = ?", (customer_id,))
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()


def _customer_count() -> int:
    if not _daisy_db_path().exists():
        return 0
    con = _connect()
    try:
        return int(con.execute("SELECT COUNT(*) FROM customers").fetchone()[0])
    finally:
        con.close()


def _export(fmt: str, limit: int) -> dict:
    """Export customers as csv | json | txt text (NOT a file — the caller saves
    it). Keeps the worker side-effect-free; DashManager owns where it lands."""
    rows = _list_recent_customers(limit)
    fmt = (fmt or "json").lower()
    if fmt == "json":
        return {"format": "json", "text": json.dumps(rows, indent=2)}
    cols = ["customer_id", "first_name", "last_name", "email", "phone",
            "full_address", "created_at"]
    if fmt == "csv":
        import csv
        import io
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
        return {"format": "csv", "text": buf.getvalue()}
    if fmt == "txt":
        lines = []
        for r in rows:
            lines.append(f"{r['first_name']} {r['last_name']}  "
                         f"{r['phone']}  {r['email']}  {r['full_address']}")
        return {"format": "txt", "text": "\n".join(lines)}
    raise ValueError(f"unknown export format: {fmt!r} (csv|json|txt)")


def _list_addresses() -> list[dict]:
    """The user's anchor-address pool (my_addresses.json), if present."""
    p = DAISY_ROOT / "my_addresses.json"
    if not p.exists():
        return []
    try:
        raw = json.loads(p.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    items = raw.get("addresses", raw) if isinstance(raw, dict) else raw
    out = []
    for x in items if isinstance(items, list) else []:
        if isinstance(x, str):
            out.append({"full_address": x})
        elif isinstance(x, dict):
            out.append({
                "name": x.get("name", ""),
                "full_address": x.get("full_address")
                                or x.get("address", ""),
                "city": x.get("city", ""),
                "state": x.get("state", ""),
            })
    return out


def handle(mgr: Managers, cmd: str, args: dict) -> dict:
    if cmd == "ping":
        return {"pong": True}

    if cmd == "balance":
        return {"balance": float(mgr.apicc.get_balance(force_refresh=True))}

    if cmd == "locations":
        return {"locations": _load_locations()}

    if cmd == "generate_identity":
        origin = args.get("origin_address") or None
        radius = float(args.get("radius_miles", 5.0))
        # generate_customer_data builds name + password + address. When an
        # origin is given, override the address with a radius-scoped real one.
        data = mgr.db.generate_customer_data(origin_address=origin)
        if origin:
            addr = mgr.mapquest.get_random_address_near_location(origin, radius)
            if addr:
                data.update({
                    "address_line1": addr.get("address_line1", ""),
                    "city": addr.get("city", ""),
                    "state": addr.get("state", ""),
                    "zip_code": addr.get("zip_code", ""),
                    "full_address": addr.get("full_address", ""),
                    "latitude": addr.get("latitude"),
                    "longitude": addr.get("longitude"),
                    "address_source": addr.get("source", "mapquest_real_poi"),
                    "address_validated": True,
                })
        # Create the Mail.tm inbox now (so email is real + readable later).
        email = mgr.mail.create_account(data["first_name"], data["last_name"])
        data.update(email)
        return {"identity": data}

    if cmd == "rent_number":
        info = mgr.apicc.create_verification()
        if not info:
            raise RuntimeError("api.cc returned no number")
        # datetime -> iso for JSON
        if hasattr(info.get("created_at"), "isoformat"):
            info["created_at"] = info["created_at"].isoformat()
        return {"number": info}

    if cmd == "fetch_otp":
        res = mgr.apicc.fetch_code_once(
            args["token"], args.get("api_url", ""),
            args.get("mirror_hosts") or [])
        return {"code": res.get("code", ""), "sms_text": res.get("sms_text", ""),
                "error": res.get("error")}

    if cmd == "save_customer":
        cid = mgr.db.save_customer(dict(args["customer"]))
        return {"customer_id": cid}

    if cmd == "list_recent_customers":
        # Read CustomerDaisy's own DB so DashManager can import existing
        # accounts (identity + api.cc token for later OTP fetch).
        limit = int(args.get("limit", 20))
        return {"customers": _list_recent_customers(limit)}

    # ── Slice 1: full CustomerDaisy surface (DB read/write, export, addresses).
    # These read/write customers.db directly (schema verified) so they work
    # without driving CustomerDaisy's interactive UI.
    if cmd == "list_customers":
        return {"customers": _list_recent_customers(int(args.get("limit", 200)))}

    if cmd == "customer_count":
        return {"count": _customer_count()}

    if cmd == "get_customer":
        return {"customer": _get_customer(str(args["customer_id"]))}

    if cmd == "update_customer":
        row = _update_customer(str(args["customer_id"]),
                               dict(args.get("fields", {})))
        return {"customer": row, "updated": row is not None}

    if cmd == "delete_customer":
        return {"deleted": _delete_customer(str(args["customer_id"]))}

    if cmd == "export":
        return _export(args.get("format", "json"), int(args.get("limit", 1000)))

    if cmd == "list_addresses":
        return {"addresses": _list_addresses()}

    if cmd == "generate_address":
        # A radius-scoped real address near an origin (no customer created).
        origin = args.get("origin_address") or args.get("origin")
        if not origin:
            raise ValueError("generate_address needs origin_address")
        radius = float(args.get("radius_miles", 5.0))
        addr = mgr.mapquest.get_random_address_near_location(origin, radius)
        return {"address": addr or None}

    raise ValueError(f"unknown command: {cmd}")


def main() -> None:
    _bootstrap()
    try:
        mgr = Managers()
    except Exception as exc:  # init failure — report on first line and exit
        _emit({"ok": False, "error": f"init failed: {exc}"})
        return
    _emit({"ok": True, "result": {"ready": True}})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            result = handle(mgr, req["cmd"], req.get("args", {}))
            _emit({"ok": True, "result": result})
        except Exception as exc:  # never crash the worker on one bad command
            _emit({"ok": False, "error": str(exc)})


if __name__ == "__main__":
    main()
