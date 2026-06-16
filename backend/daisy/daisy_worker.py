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
  analytics {limit?}                    -> {total, verified, by_state, by_city}
  get_customer {customer_id}            -> {customer: {...}|null}
  update_customer {customer_id, fields} -> {customer, updated}
  delete_customer {customer_id}         -> {deleted: bool}
  export {format, limit}                -> {format, text}       (csv|json|txt)
  list_addresses                        -> {addresses: [...]}   (anchor pool)
  add_address {address}                 -> {addresses: [...]}   (append + persist)
  update_address {index, address}       -> {addresses: [...]}   (replace at index)
  delete_address {index}                -> {addresses: [...]}   (remove at index)
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
    """Apply the worker's process side-effects. Call once from main().

    Does NOT re-capture _real_stdout — it's already the real stdout from module
    import. Re-capturing here would, on an accidental second call, grab the
    already-swapped sys.stderr and silently route the JSON protocol to stderr.
    """
    os.chdir(DAISY_ROOT)
    sys.path.insert(0, str(DAISY_ROOT))
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
    # `r.get(k, "")` returns None for a NULL column (key exists, value is None);
    # `or ""` coerces those to "" so exports/UI never show "None" or hit a
    # TypeError downstream.
    return {
        "customer_id": r.get("customer_id") or "",
        "first_name": r.get("first_name") or "",
        "last_name": r.get("last_name") or "",
        "full_name": r.get("full_name") or "",
        "email": r.get("email") or "",
        "password": r.get("password") or "",
        "phone": r.get("primary_phone") or "",
        "full_address": r.get("full_address") or "",
        "city": r.get("city") or "",
        "state": r.get("state") or "",
        "zip_code": r.get("zip_code") or "",
        "verification_completed": bool(r.get("verification_completed")),
        "number_token": (meta.get("apicc_number_token")
                         or r.get("primary_verification_id") or ""),
        "api_url": meta.get("apicc_api_url") or "",
        "mirror_hosts": hosts_list,
        "created_at": r.get("created_at") or "",
        "updated_at": r.get("updated_at") or "",
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
    """Update whitelisted columns on a CustomerDaisy row; return the new row.

    Accepts the read-shape key `phone` as an alias for the DB column
    `primary_phone` so a caller can round-trip the same key it read.
    """
    if not _daisy_db_path().exists():
        return None                         # missing DB -> nothing to update
    fields = dict(fields or {})
    if "phone" in fields and "primary_phone" not in fields:
        fields["primary_phone"] = fields.pop("phone")
    updates = {k: v for k, v in fields.items() if k in _UPDATABLE}
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
    if not _daisy_db_path().exists():
        return False
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


def _analytics(limit: int = 100000) -> dict:
    """Coverage/analytics over CustomerDaisy's pool — counts by state, by city,
    and verified-vs-not. Pure aggregation over the SAME normalized rows the rest
    of the surface reads (no extra DB shape), so it's missing-DB safe (-> zeros)
    and unit-testable without CustomerDaisy live.

    Returns ``{total, verified, unverified, by_state, by_city}`` where by_state
    /by_city are ``[{key, count}]`` lists sorted by count desc then key asc (a
    stable order for the UI). A blank state/city is bucketed under ``"—"`` so it
    is visible rather than silently dropped.
    """
    rows = _list_recent_customers(limit)
    by_state: dict[str, int] = {}
    by_city: dict[str, int] = {}
    verified = 0
    for r in rows:
        if r.get("verification_completed"):
            verified += 1
        st = (r.get("state") or "").strip() or "—"
        by_state[st] = by_state.get(st, 0) + 1
        city = (r.get("city") or "").strip() or "—"
        by_city[city] = by_city.get(city, 0) + 1

    def _ranked(d: dict[str, int]) -> list[dict]:
        return [{"key": k, "count": n}
                for k, n in sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))]

    total = len(rows)
    return {"total": total, "verified": verified,
            "unverified": total - verified,
            "by_state": _ranked(by_state), "by_city": _ranked(by_city)}


def _export(fmt: str, limit: int) -> dict:
    """Export customers as csv | json | txt text (NOT a file — the caller saves
    it). Keeps the worker side-effect-free; DashManager owns where it lands."""
    rows = _list_recent_customers(limit)
    fmt = (fmt or "json").lower()
    if fmt == "json":
        # Strip the plaintext password from the export (csv/txt already exclude
        # it via their explicit column allowlist) — exports get saved to disk.
        safe = [{k: v for k, v in r.items() if k != "password"} for r in rows]
        return {"format": "json", "text": json.dumps(safe, indent=2)}
    cols = ["customer_id", "first_name", "last_name", "email", "phone",
            "full_address", "created_at"]
    if fmt == "csv":
        import csv
        import io
        # newline="" so csv's \r\n isn't doubled to \r\r\n on Windows StringIO.
        buf = io.StringIO(newline="")
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
    # JSON values can be any type (a hand-edited my_addresses.json may carry an
    # int, null, etc.); coerce to a stripped string only for real strings so a
    # non-string never reaches .strip() (AttributeError) or becomes a bogus
    # address like "123".
    def _s(v: object) -> str:
        return v.strip() if isinstance(v, str) else ""

    items = raw.get("addresses", raw) if isinstance(raw, dict) else raw
    out = []
    for x in items if isinstance(items, list) else []:
        if isinstance(x, str):
            full, name, city, state = _s(x), "", "", ""
        elif isinstance(x, dict):
            # Prefer full_address, but fall back to the legacy "address" key when
            # full_address is missing OR blank-after-strip (a whitespace-only
            # full_address must not mask a real address).
            full = _s(x.get("full_address")) or _s(x.get("address"))
            name, city, state = (x.get("name", ""), x.get("city", ""),
                                 x.get("state", ""))
        else:
            continue                       # skip non-str/non-dict junk entries
        if not full:
            continue                       # an address with no address is useless
        out.append({"name": name, "full_address": full,
                    "city": city, "state": state})
    return out


def _addresses_path():
    return DAISY_ROOT / "my_addresses.json"


def _write_addresses(addresses: list[dict]) -> None:
    """Persist the anchor pool back to my_addresses.json in the canonical
    ``{"addresses": [...]}`` shape, atomically (write a temp file + replace) so a
    crash mid-write can't truncate the user's address book."""
    p = _addresses_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps({"addresses": addresses}, indent=2),
                       encoding="utf-8")
        tmp.replace(p)                      # atomic on the same filesystem
    except OSError:
        # disk full / locked / permission — don't leave a half-written .tmp
        # littering the user's CustomerDaisy dir; the original file is untouched
        # (replace is atomic, so on failure p is either old-or-new, never torn).
        tmp.unlink(missing_ok=True)
        raise


def _clean_address(entry: dict) -> dict:
    """Normalize one address dict to the stored shape; full_address required."""
    def _s(v: object) -> str:
        return v.strip() if isinstance(v, str) else ""

    full = _s(entry.get("full_address")) or _s(entry.get("address"))
    if not full:
        raise ValueError("address needs a non-empty full_address")
    return {"name": _s(entry.get("name")), "full_address": full,
            "city": _s(entry.get("city")), "state": _s(entry.get("state"))}


def _addresses_for_edit() -> list[dict]:
    """The current pool for a READ-MODIFY-WRITE edit — same normalized rows as
    ``_list_addresses``, but it RAISES on a corrupt/unreadable file instead of
    swallowing the error and returning [].

    This is the critical difference from ``_list_addresses``: the list path is a
    forgiving read for display, but an EDIT then writes the result BACK. If a
    hand-edited ``my_addresses.json`` had a JSON syntax error, the forgiving read
    would return [] and the write would CLOBBER the whole file (silent data
    loss). Here we let the JSON/OS error propagate so the edit aborts and the
    user's file is left untouched.
    """
    p = _addresses_path()
    if not p.exists():
        return []
    raw = json.loads(p.read_text("utf-8"))   # raises on bad JSON — do NOT swallow
    items = raw.get("addresses", raw) if isinstance(raw, dict) else raw
    out: list[dict] = []
    for x in items if isinstance(items, list) else []:
        try:
            out.append(_clean_address(x if isinstance(x, dict)
                                      else {"full_address": x}))
        except (ValueError, AttributeError):
            continue                         # skip an individual junk entry
    return out


def _add_address(entry: dict) -> list[dict]:
    """Append a cleaned address to the pool; returns the new full list."""
    cleaned = _clean_address(entry)          # validate BEFORE reading the file
    addresses = _addresses_for_edit()
    addresses.append(cleaned)
    _write_addresses(addresses)
    return addresses


def _update_address(index: int, entry: dict) -> list[dict]:
    """Replace the address at ``index`` (0-based) with a cleaned one."""
    cleaned = _clean_address(entry)
    addresses = _addresses_for_edit()
    if not 0 <= index < len(addresses):
        raise IndexError(f"address index {index} out of range "
                         f"(0..{len(addresses) - 1})")
    addresses[index] = cleaned
    _write_addresses(addresses)
    return addresses


def _delete_address(index: int) -> list[dict]:
    """Remove the address at ``index`` (0-based); returns the new full list."""
    addresses = _addresses_for_edit()
    if not 0 <= index < len(addresses):
        raise IndexError(f"address index {index} out of range "
                         f"(0..{len(addresses) - 1})")
    addresses.pop(index)
    _write_addresses(addresses)
    return addresses


def _req(args: dict, key: str, cmd: str):
    """Fetch a required command arg, or raise a descriptive ValueError.

    A bare ``args[key]`` raises ``KeyError(key)``, which main() serializes back
    over the pipe as ``error: "'key'"`` — useless for diagnosing which command
    was malformed. This names both the command and the missing arg instead.

    Treats absent / None / empty-string as missing. For string args
    (customer_id, token, customer) that's exactly right; don't reuse this for a
    numeric/boolean arg where 0/False would be wrongly rejected.
    """
    if key not in args or args[key] in (None, ""):
        raise ValueError(f"{cmd} needs {key}")
    return args[key]


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
        token = _req(args, "token", cmd)        # validate before touching mgr
        res = mgr.apicc.fetch_code_once(
            token, args.get("api_url", ""),
            args.get("mirror_hosts") or [])
        return {"code": res.get("code", ""), "sms_text": res.get("sms_text", ""),
                "error": res.get("error")}

    if cmd == "save_customer":
        customer = _req(args, "customer", cmd)   # validate before touching mgr
        cid = mgr.db.save_customer(dict(customer))
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

    if cmd == "analytics":
        return _analytics(int(args.get("limit", 100000)))

    if cmd == "get_customer":
        return {"customer": _get_customer(str(_req(args, "customer_id", cmd)))}

    if cmd == "update_customer":
        row = _update_customer(str(_req(args, "customer_id", cmd)),
                               dict(args.get("fields", {})))
        return {"customer": row, "updated": row is not None}

    if cmd == "delete_customer":
        return {"deleted": _delete_customer(str(_req(args, "customer_id", cmd)))}

    if cmd == "export":
        return _export(args.get("format", "json"), int(args.get("limit", 1000)))

    if cmd == "list_addresses":
        return {"addresses": _list_addresses()}

    if cmd == "add_address":
        return {"addresses": _add_address(dict(_req(args, "address", cmd)))}

    if cmd == "update_address":
        return {"addresses": _update_address(
            int(_req(args, "index", cmd)), dict(_req(args, "address", cmd)))}

    if cmd == "delete_address":
        return {"addresses": _delete_address(int(_req(args, "index", cmd)))}

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
