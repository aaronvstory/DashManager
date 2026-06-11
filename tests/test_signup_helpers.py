"""Pure helpers for signup/login OTP detection (no browser)."""
from __future__ import annotations

from backend.browser.signup import looks_like_split_otp, normalize_phone


# ── 6-box OTP detection (live finding 2026-06-12) ────────────────────────────

def test_split_otp_index_labels():
    # Passwordless login: boxes labelled by index "0".."5".
    assert looks_like_split_otp(["0", "1", "2", "3", "4", "5"]) is True


def test_split_otp_too_few_boxes():
    assert looks_like_split_otp(["0", "1"]) is False


def test_single_code_field_is_not_split():
    # A single numeric field labelled "Enter your 6-digit code" must NOT be
    # mistaken for split boxes (its label isn't a bare index).
    assert looks_like_split_otp(["Enter your 6-digit code"]) is False


def test_split_otp_mixed_labels_filtered():
    # Only the bare index labels count; noise labels are ignored.
    assert looks_like_split_otp(
        ["0", "1", "2", "3", "search", ""]) is True
    assert looks_like_split_otp(["foo", "bar", "baz", "qux"]) is False


# ── phone normalization ──────────────────────────────────────────────────────

def test_normalize_phone_strips_to_ten_digits():
    assert normalize_phone("+1 (252) 555-0173") == "2525550173"
    assert normalize_phone("2525550173") == "2525550173"
    assert normalize_phone("") == ""
