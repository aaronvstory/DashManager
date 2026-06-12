"""Tests for the pure helpers in account_creator (no browser, no bridge)."""
from backend.account_creator import _daisy_record, _notes


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
