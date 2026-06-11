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

stdout is JSON-only; CustomerDaisy's Rich console noise is redirected to stderr
so it can never corrupt the protocol.
"""
import json
import os
import sys
from pathlib import Path

# CustomerDaisy assumes it runs from its own root. The launcher sets cwd, but
# enforce it here too so relative paths (config.ini, .env.local, data/) resolve.
DAISY_ROOT = Path(os.environ.get("DAISY_ROOT", Path.cwd()))
os.chdir(DAISY_ROOT)
sys.path.insert(0, str(DAISY_ROOT))

# Keep Rich/console chatter off stdout — stdout is the JSON channel only.
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


def _list_recent_customers(limit: int) -> list[dict]:
    """Read CustomerDaisy's customers.db (identity + api.cc token per row)."""
    import sqlite3

    db_path = DAISY_ROOT / "data" / "customers.db"
    if not db_path.exists():
        return []
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT * FROM customers ORDER BY created_at DESC LIMIT ?",
            (limit,)).fetchall()
    finally:
        con.close()
    out = []
    for r in rows:
        r = dict(r)
        try:
            meta = json.loads(r.get("metadata") or "{}")
        except (json.JSONDecodeError, TypeError):
            meta = {}
        hosts = meta.get("apicc_mirror_hosts") or ""
        out.append({
            "customer_id": r.get("customer_id", ""),
            "first_name": r.get("first_name", ""),
            "last_name": r.get("last_name", ""),
            "email": r.get("email", ""),
            "password": r.get("password", ""),
            "phone": r.get("primary_phone", ""),
            "full_address": r.get("full_address", ""),
            "number_token": (meta.get("apicc_number_token")
                             or r.get("primary_verification_id") or ""),
            "api_url": meta.get("apicc_api_url", ""),
            "mirror_hosts": [h for h in hosts.split(",") if h] if hosts else [],
            "created_at": r.get("created_at", ""),
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

    raise ValueError(f"unknown command: {cmd}")


def main() -> None:
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
