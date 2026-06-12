"""Tests for the pure helpers in otp_fetch (no bridge/network)."""
from backend import otp_fetch


def test_parse_args_date_only():
    assert otp_fetch._parse_args(["2026-06-12"]) == ("2026-06-12", None)


def test_parse_args_ids():
    assert otp_fetch._parse_args(["--ids", "3,5,7"]) == (None, [3, 5, 7])


def test_parse_args_date_and_ids():
    assert otp_fetch._parse_args(["2026-06-12", "--ids", "1"]) == \
        ("2026-06-12", [1])


def test_parse_args_empty():
    assert otp_fetch._parse_args([]) == (None, None)


def test_loads_list_variants():
    assert otp_fetch._loads_list(["a", "b"]) == ["a", "b"]
    assert otp_fetch._loads_list('["x"]') == ["x"]
    assert otp_fetch._loads_list("") == []
    assert otp_fetch._loads_list("garbage") == []
    assert otp_fetch._loads_list(None) == []


def test_format_table_empty():
    assert "No customers" in otp_fetch.format_table([])


def test_format_table_shows_code_and_note():
    rows = [
        {"id": 1, "name": "Ada Vance", "phone": "+1 555", "code": "123456",
         "error": ""},
        {"id": 2, "name": "Bo Kim", "phone": "—", "code": "",
         "error": "no code yet"},
    ]
    out = otp_fetch.format_table(rows)
    assert "Ada Vance" in out and "123456" in out
    assert "no code yet" in out
    # missing code shows the placeholder, not a blank that hides the row
    assert "·····" in out
