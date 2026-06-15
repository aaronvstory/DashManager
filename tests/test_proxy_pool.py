"""Tests for the pure parts of proxy_pool (parse/format/dedup/geo) — no network.

The liveness check (check_proxy/check_all/local_ip) does real network I/O and is
not unit-tested here; these cover the parse→dict→SB-string round-trip that the
signup driver and the /api/proxies route depend on.
"""
from pathlib import Path

from backend.browser import proxy_pool as pp

# The exact line shape in working-proxies.txt (creds are fake here).
LINE = ("http://resident.lightningproxies.net:8080:"
        "bHo8ZhxOwcZK_lightning_proxy-country-us-filter-medium-speed-fast:"
        "secretpass123")


def test_parse_colon_separated_line():
    px = pp.parse_proxy_line(LINE)
    assert px == {
        "scheme": "http",
        "host": "resident.lightningproxies.net",
        "port": "8080",
        "username": "bHo8ZhxOwcZK_lightning_proxy-country-us-filter-medium-speed-fast",
        "password": "secretpass123",
    }


def test_parse_url_shaped_line():
    px = pp.parse_proxy_line("http://user:pass@host.example:9000")
    assert px is not None
    assert px["host"] == "host.example"
    assert px["port"] == "9000"
    assert px["username"] == "user"
    assert px["password"] == "pass"


def test_parse_password_may_contain_colon():
    # maxsplit=3 keeps a ':' inside the password intact (the 4th field is "all
    # the rest"), so a password like "a:b:c" survives.
    px = pp.parse_proxy_line("host:8080:user:a:b:c")
    assert px is not None
    assert px["password"] == "a:b:c"


def test_parse_skips_blank_and_comment_and_garbage():
    assert pp.parse_proxy_line("") is None
    assert pp.parse_proxy_line("   ") is None
    assert pp.parse_proxy_line("# a comment") is None
    assert pp.parse_proxy_line("notenoughfields") is None
    # non-numeric port is rejected
    assert pp.parse_proxy_line("host:notaport:user:pass") is None


def test_format_sb_proxy_is_user_pass_at_host_port():
    px = pp.parse_proxy_line(LINE)
    assert px is not None
    s = pp.format_sb_proxy(px)
    assert s == (
        "bHo8ZhxOwcZK_lightning_proxy-country-us-filter-medium-speed-fast:"
        "secretpass123@resident.lightningproxies.net:8080")
    # no scheme prefix in the SB inline-auth form
    assert not s.startswith("http")


def test_format_sb_proxy_no_auth_is_bare_hostport():
    assert pp.format_sb_proxy(
        {"host": "h", "port": "80", "username": "", "password": ""}) == "h:80"


def test_format_requests_proxy_has_scheme():
    px = pp.parse_proxy_line(LINE)
    assert px is not None
    assert pp.format_requests_proxy(px).startswith(
        "http://bHo8ZhxOwcZK_lightning_proxy")


def test_proxy_id_excludes_password():
    px = pp.parse_proxy_line(LINE)
    assert px is not None
    pid = pp.proxy_id(px)
    assert "secretpass123" not in pid          # password NEVER in the handle
    assert px["host"] in pid and px["port"] in pid


def test_dedup_collapses_identical_lines():
    px = pp.parse_proxy_line(LINE)
    assert px is not None
    out = pp.dedup_proxies([px, dict(px), dict(px)])
    assert len(out) == 1


def test_load_proxies_missing_file_is_empty(tmp_path: Path):
    assert pp.load_proxies(tmp_path / "nope.txt") == []


def test_load_proxies_parses_file(tmp_path: Path):
    f = tmp_path / "proxies.txt"
    f.write_text("\n".join([LINE, "# comment", "", LINE]), encoding="utf-8")
    out = pp.load_proxies(f)
    assert len(out) == 2  # two LINEs, comment + blank skipped
    assert pp.dedup_proxies(out) == [out[0]]


def test_classify_geo_lumtest_live_shape():
    # lumtest's real shape: country at top level, city/region under geo, and
    # crucially NO top-level 'ip' (only ip_version) — so exit_ip is empty here
    # and check_proxy relies on the ipify echo for the IP.
    geo = pp._classify_geo({
        "ip_version": 4, "country": "US",
        "geo": {"city": "Upper Darby", "region": "PA",
                "region_name": "Pennsylvania"},
    })
    assert geo["exit_ip"] == ""           # lumtest gives no ip
    assert geo["country"] == "US"
    assert geo["city"] == "Upper Darby"
    assert geo["region"] == "Pennsylvania"


def test_classify_geo_bare_ipify_shape():
    geo = pp._classify_geo({"ip": "5.6.7.8"})
    assert geo["exit_ip"] == "5.6.7.8"
    assert geo["country"] == "" and geo["city"] == ""
