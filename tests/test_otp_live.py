"""Tests for the pure render helpers in otp_live (no bridge/network)."""
from backend import otp_live


def _rows():
    return [
        {"name": "Michelle Green", "phone": "18507877395",
         "email": "m@x.net", "address": "904 Virginia Rd, Edenton, NC 27932",
         "id": "06-12 1 Michelle", "token": "tokA", "api_url": "", "hosts": []},
        {"name": "Jill Murphy", "phone": "12602914465", "email": "j@x.net",
         "address": "114 Marine Dr, Edenton, NC 27932",
         "id": "06-12 2 Jill", "token": "tokB", "api_url": "", "hosts": []},
    ]


def test_render_shows_names_phones_ids():
    out = otp_live._render(_rows(), {"tokA": "139411"}, 0)
    assert "Michelle Green" in out and "Jill Murphy" in out
    assert "18507877395" in out
    assert "06-12 1 Michelle" in out
    assert "139411" in out                 # known code shown
    assert "······" in out                 # missing code placeholder


def test_render_handles_no_codes_yet():
    out = otp_live._render(_rows(), {}, 1)
    assert out.count("······") == 2        # both pending


def test_short_id_format():
    sid = otp_live._short_id({"first_name": "Michelle"}, 1)
    # "<mm-dd> 1 Michelle"
    assert sid.endswith("1 Michelle")
    assert sid.count("-") == 1
