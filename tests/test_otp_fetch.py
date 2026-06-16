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


def test_error_row_shape():
    row = otp_fetch._error_row(
        {"id": 7, "first_name": "Zoe", "last_name": "Kerr", "phone": "555"},
        "bridge failed: boom")
    assert row == {"id": 7, "name": "Zoe Kerr", "phone": "555", "code": "",
                   "error": "bridge failed: boom"}


async def test_fetch_bucket_otps_degrades_failed_shard(monkeypatch):
    """A shard whose bridge fails must yield error rows, not drop customers or
    raise — every customer gets a row, in input order."""
    custs = [{"id": i, "first_name": f"C{i}", "last_name": "X",
              "phone": str(i), "number_token": "tok", "bucket_date": "2026-06-15"}
             for i in range(1, 6)]

    async def fake_list():
        return custs

    from backend.daisy import sharded

    monkeypatch.setattr(otp_fetch.db, "list_customers", fake_list)
    # POOL_SIZE now lives in the shared sharded helper.
    monkeypatch.setattr(sharded, "POOL_SIZE", 2)

    calls = {"n": 0}

    # New _fetch_shard signature: takes [(idx, customer)], returns {idx: row}.
    async def flaky_shard(shard):
        calls["n"] += 1
        if calls["n"] == 1:                     # first shard's bridge "dies"
            raise RuntimeError("bridge down")
        out = {}
        for idx, c in shard:
            row = otp_fetch._error_row(c, "no code yet")
            out[idx] = row
        return out

    monkeypatch.setattr(otp_fetch, "_fetch_shard", flaky_shard)

    rows = await otp_fetch.fetch_bucket_otps("2026-06-15")
    assert [r["id"] for r in rows] == [1, 2, 3, 4, 5]   # all, in order
    # the failed shard's customers carry the bridge-failure note
    failed = [r for r in rows if "bridge failed" in r["error"]]
    assert failed and all(r["code"] == "" for r in rows)
