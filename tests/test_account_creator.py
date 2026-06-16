"""Tests for the pure helpers in account_creator (no browser, no bridge)."""
from datetime import datetime, timedelta, timezone

from backend.account_creator import (_apply_fixed_address, _daisy_record,
                                      _extract_orphan, _notes, _within_hours)


def test_daisy_record_carries_identity_and_verified_flag():
    rec = _daisy_record({"first_name": "Ada", "email": "a@x.net"},
                        verified=True)
    assert rec["first_name"] == "Ada"
    assert rec["email"] == "a@x.net"
    assert rec["verification_completed"] is True


def test_daisy_record_without_batch_omits_batch_fields():
    rec = _daisy_record({"first_name": "Ada"}, verified=False)
    assert "apicc_batch_id" not in rec
    assert "apicc_batch_label" not in rec


def test_daisy_record_stamps_batch_id_and_label():
    rec = _daisy_record({"first_name": "Ada"}, verified=True,
                        batch_id="dm-20260612-x9", batch_label="Tampa run")
    assert rec["apicc_batch_id"] == "dm-20260612-x9"
    assert rec["apicc_batch_label"] == "Tampa run"


def test_daisy_record_label_defaults_to_id():
    rec = _daisy_record({"first_name": "Ada"}, verified=True,
                        batch_id="dm-20260612-x9")
    assert rec["apicc_batch_label"] == "dm-20260612-x9"


def test_daisy_record_does_not_mutate_input():
    identity = {"first_name": "Ada"}
    _daisy_record(identity, verified=True, batch_id="b1")
    assert "apicc_batch_id" not in identity  # copied, not mutated
    assert "verification_completed" not in identity


def test_notes_includes_address_and_daisy_id():
    n = _notes({"full_address": "12 Main St, Tampa, FL"}, "cust-7")
    assert "created via signup" in n
    assert "12 Main St, Tampa, FL" in n
    assert "daisy:cust-7" in n


def test_notes_minimal():
    assert _notes({}, "") == "created via signup"


# ── number-field mapping (the orphan-recovery fix) ───────────────────────────


def test_daisy_record_maps_number_fields_to_apicc_keys():
    """rent_number keys must be remapped to the apicc_* names CustomerDaisy
    actually persists, else the number drops on save (the orphan bug)."""
    identity = {"first_name": "Ada", "number_token": "tok123",
                "api_url": "https://api.cc/x", "mirror_hosts": ["h1"],
                "ordernum": "ord-9"}
    rec = _daisy_record(identity, verified=False)
    assert rec["apicc_number_token"] == "tok123"
    assert rec["apicc_api_url"] == "https://api.cc/x"
    assert rec["apicc_mirror_hosts"] == ["h1"]
    assert rec["apicc_ordernum"] == "ord-9"


def test_daisy_record_skips_missing_number_fields():
    rec = _daisy_record({"first_name": "Ada"}, verified=True)
    assert "apicc_number_token" not in rec
    assert "apicc_ordernum" not in rec


# ── orphan extraction ────────────────────────────────────────────────────────


def test_extract_orphan_returns_reusable_number():
    rec = {"first_name": "Kenneth", "last_name": "Roth",
           "verification_completed": False, "primary_phone": "14842105769",
           "metadata": {"apicc_number_token": "m8jdww1f",
                        "apicc_api_url": "https://api.cc/k",
                        "apicc_mirror_hosts": ["m"], "apicc_ordernum": "o1"}}
    o = _extract_orphan(rec)
    assert o is not None
    assert o["number_token"] == "m8jdww1f"
    assert o["phone_number"] == "14842105769"
    assert o["api_url"] == "https://api.cc/k"
    assert o["_daisy_name"] == "Kenneth Roth"


def test_extract_orphan_none_when_verified():
    rec = {"verification_completed": True,
           "metadata": {"apicc_number_token": "tok"}}
    assert _extract_orphan(rec) is None


def test_extract_orphan_none_when_no_token():
    rec = {"verification_completed": False, "metadata": {}}
    assert _extract_orphan(rec) is None


def test_extract_orphan_carries_created_at():
    rec = {"verification_completed": False, "created_at": "2026-06-12T22:05:20",
           "metadata": {"apicc_number_token": "tok"}}
    assert _extract_orphan(rec)["created_at"] == "2026-06-12T22:05:20"


# ── recency guard (only reuse freshly-bought numbers, never old/expired) ─────

_NOW = datetime(2026, 6, 12, 23, 0, 0, tzinfo=timezone.utc)


def test_within_hours_recent_is_true():
    recent = (_NOW - timedelta(hours=1)).isoformat()
    assert _within_hours(recent, _NOW, 24.0) is True


def test_within_hours_old_is_false():
    old = (_NOW - timedelta(days=2)).isoformat()
    assert _within_hours(old, _NOW, 24.0) is False


def test_within_hours_empty_is_false():
    # unknown age = treat as too old; never reuse what we can't prove is recent
    assert _within_hours("", _NOW, 24.0) is False


def test_within_hours_unparseable_is_false():
    assert _within_hours("not-a-date", _NOW, 24.0) is False


def test_within_hours_naive_timestamp_assumed_utc():
    naive = (_NOW - timedelta(hours=2)).replace(tzinfo=None).isoformat()
    assert _within_hours(naive, _NOW, 24.0) is True


# ── fixed-address override (unique=False shares one anchor address) ──────────


def test_apply_fixed_address_overrides_full_address():
    """unique=False pins the batch to one address; the random addr's lat/lng
    and dist-from-anchor describe the discarded pick, so they're dropped."""
    identity = {"first_name": "Ada", "full_address": "1 Random Way, Tampa, FL",
                "latitude": 27.9, "longitude": -82.4, "dist_from_anchor": 3.2}
    out = _apply_fixed_address(identity, "42 Shared St, Tampa, FL")
    assert out["full_address"] == "42 Shared St, Tampa, FL"
    assert "latitude" not in out
    assert "longitude" not in out
    assert "dist_from_anchor" not in out


def test_apply_fixed_address_noop_when_none():
    identity = {"full_address": "1 Random Way", "latitude": 27.9}
    out = _apply_fixed_address(identity, None)
    assert out["full_address"] == "1 Random Way"
    assert out["latitude"] == 27.9


def test_apply_fixed_address_noop_when_blank():
    identity = {"full_address": "1 Random Way"}
    out = _apply_fixed_address(identity, "")
    assert out["full_address"] == "1 Random Way"
