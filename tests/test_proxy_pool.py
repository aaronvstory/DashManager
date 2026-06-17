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


def test_proxy_id_uses_full_username_no_collision():
    # Two proxies sharing host:port but differing in their (long) flag username
    # must get DISTINCT ids — else /api/proxies/test/{id} probes the wrong line.
    a = {"scheme": "http", "host": "h", "port": "8080",
         "username": "tok_lightning_proxy-country-us-filter-medium-speed-fast",
         "password": "p1"}
    b = dict(a, username=a["username"].replace("-us-", "-gb-"))
    assert pp.proxy_id(a) != pp.proxy_id(b)
    assert pp.proxy_id(a).endswith(a["username"])  # full username, not truncated


def test_scrub_creds_redacts_url_and_password():
    px = pp.parse_proxy_line(LINE)
    assert px is not None
    # A realistic requests ProxyError string embedding the full proxy URL.
    leaky = (f"ProxyError: HTTPSConnectionPool ... url: "
             f"{pp.format_requests_proxy(px)}/myip.json (407)")
    scrubbed = pp._scrub_creds(leaky, px)
    assert "secretpass123" not in scrubbed      # password gone
    assert px["password"] not in scrubbed
    assert "<proxy>" in scrubbed or "<redacted>" in scrubbed


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


def test_classify_geo_non_string_ip_does_not_raise():
    # An echo returning a non-string ip (int/null) must not blow up .strip().
    geo = pp._classify_geo({"ip": 12345})
    assert geo["exit_ip"] == "12345"
    geo2 = pp._classify_geo({"ip": None, "query": None})
    assert geo2["exit_ip"] == ""


def test_classify_geo_country_falls_back_to_geo_subdict():
    # No top-level country/country_code, but the geo sub-dict carries one —
    # the `if not country: country = geo.get("country")...` fallback path.
    geo = pp._classify_geo({"ip": "1.2.3.4",
                            "geo": {"country": "GB", "city": "London"}})
    assert geo["country"] == "GB"
    assert geo["city"] == "London"


def test_classify_geo_handles_non_dict_geo_value():
    # A malformed echo where "geo" isn't a dict (a string/None) must not raise —
    # geo defaults to {} and the top-level country still resolves.
    geo = pp._classify_geo({"ip": "1.2.3.4", "geo": "not-a-dict",
                            "country": "FR"})
    assert geo["country"] == "FR"
    assert geo["city"] == "" and geo["region"] == ""


def test_classify_geo_country_code_alias():
    # Some echoes use country_code instead of country.
    geo = pp._classify_geo({"country_code": "DE"})
    assert geo["country"] == "DE"


# ── persistence helpers (add / delete / serialize) ──────────────────────────


def test_serialize_round_trips(tmp_path):
    line = "socks5://host.x:1080:user:pass:with:colons"
    px = pp.parse_proxy_line(line)
    assert px is not None
    # serialize -> parse -> same dict
    again = pp.parse_proxy_line(pp.serialize_proxy_line(px))
    assert again == px


def test_add_proxies_skips_exact_dupes(tmp_path):
    f = tmp_path / "p.txt"
    px = pp.parse_proxy_line("http://h:8080:u:p")
    assert pp.add_proxies([px], path=f) == 1
    assert pp.add_proxies([px], path=f) == 0   # dupe -> no-op
    assert len(pp.load_proxies(f)) == 1


def test_delete_proxy_by_id(tmp_path):
    f = tmp_path / "p.txt"
    a = pp.parse_proxy_line("http://h1:8080:u1:p1")
    b = pp.parse_proxy_line("http://h2:9090:u2:p2")
    pp.add_proxies([a, b], path=f)
    removed = pp.delete_proxy(pp.proxy_id(a), path=f)
    assert removed == 1
    remaining = pp.load_proxies(f)
    assert [pp.proxy_id(p) for p in remaining] == [pp.proxy_id(b)]


def test_delete_missing_id_returns_zero(tmp_path):
    f = tmp_path / "p.txt"
    pp.add_proxies([pp.parse_proxy_line("http://h:8080:u:p")], path=f)
    assert pp.delete_proxy("no:0~x", path=f) == 0


def test_concurrent_adds_dont_lose_data(tmp_path):
    """Two threads adding distinct proxies at once: _FILE_LOCK must serialize the
    read-modify-write so neither clobbers the other (both end up in the file)."""
    import threading
    f = tmp_path / "p.txt"
    a = pp.parse_proxy_line("http://h1:8080:u1:p1")
    b = pp.parse_proxy_line("http://h2:9090:u2:p2")
    barrier = threading.Barrier(2)

    def add(px):
        barrier.wait()  # maximize overlap
        pp.add_proxies([px], path=f)

    t1 = threading.Thread(target=add, args=(a,))
    t2 = threading.Thread(target=add, args=(b,))
    t1.start(); t2.start(); t1.join(); t2.join()

    ids = {pp.proxy_id(p) for p in pp.load_proxies(f)}
    assert ids == {pp.proxy_id(a), pp.proxy_id(b)}  # both survived
