"""Tests for the pure helpers in account_creator (no browser, no bridge)."""
from backend.account_creator import _daisy_record, _extract_orphan, _notes


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
